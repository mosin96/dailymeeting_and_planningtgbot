"""DailyRegistry storage tests (aiosqlite on tmp file)."""
import datetime

import pytest

from ppbot.daily import DailyChat, DailyMember
from ppbot.daily_storage import DailyRegistry


@pytest.fixture
async def registry(tmp_path):
    db = tmp_path / "daily.db"
    r = DailyRegistry()
    await r.init_db(str(db))
    yield r
    await r.close()


def member(chat_id, pos, name="X", user_id=None, username=None, skip_date=None, vacation_until=None, vacation_start=None):
    display = username if username else name
    m = DailyMember(
        chat_id=chat_id,
        position=pos,
        username=display,
        user_id=user_id,
        skip_date=skip_date,
        vacation_until=vacation_until,
        vacation_start=vacation_start,
    )
    return m


async def seed(registry, chat_id=1, n=3):
    for i in range(n):
        await registry.add_member(
            member(chat_id, i, f"U{i}", user_id=100 + i, username=f"@u{i}")
        )


async def mon_fri(date):
    return date.weekday() < 5


class TestChats:
    async def test_upsert_get_roundtrip(self, registry):
        chat = DailyChat(chat_id=1, daily_time="09:15", next_index=2, last_reminder_date="2026-08-03")
        await registry.upsert_chat(chat)
        loaded = await registry.get_chat(1)
        assert loaded is not None
        assert loaded.daily_time == "09:15"
        assert loaded.next_index == 2
        assert loaded.last_reminder_date == "2026-08-03"

    async def test_get_missing_returns_none(self, registry):
        assert await registry.get_chat(999) is None

    async def test_upsert_overwrites(self, registry):
        await registry.upsert_chat(DailyChat(chat_id=1, daily_time="10:00"))
        await registry.upsert_chat(DailyChat(chat_id=1, daily_time="11:30", next_index=1))
        loaded = await registry.get_chat(1)
        assert loaded.daily_time == "11:30"
        assert loaded.next_index == 1

    async def test_list_chats(self, registry):
        await registry.upsert_chat(DailyChat(chat_id=1))
        await registry.upsert_chat(DailyChat(chat_id=2))
        chats = await registry.list_chats()
        assert [c.chat_id for c in chats] == [1, 2]


class TestMembers:
    async def test_add_get_sorted(self, registry):
        await seed(registry)
        members = await registry.get_members(1)
        assert [m.position for m in members] == [0, 1, 2]
        assert [m.username for m in members] == ["@u0", "@u1", "@u2"]

    async def test_replace_members(self, registry):
        await seed(registry, n=2)
        new = [
            member(1, 0, "X", user_id=1),
            member(1, 1, "Y", user_id=2),
        ]
        await registry.replace_members(1, new)
        members = await registry.get_members(1)
        assert [m.username for m in members] == ["X", "Y"]
        # only chat 1 members replaced
        await seed(registry, chat_id=2, n=1)
        assert len(await registry.get_members(2)) == 1

    async def test_remove_middle_reindexes_and_fixes_next_index(self, registry):
        await seed(registry)
        await registry.upsert_chat(DailyChat(chat_id=1, next_index=2))
        await registry.remove_member(1, 1)  # remove U1 (position 1)
        members = await registry.get_members(1)
        assert [m.position for m in members] == [0, 1]
        assert [m.username for m in members] == ["@u0", "@u2"]
        # next_index 2 > removed position 1 -> becomes 1 (not below 0)
        chat = await registry.get_chat(1)
        assert chat.next_index == 1

    async def test_remove_first_with_next_index_0(self, registry):
        await seed(registry)
        await registry.upsert_chat(DailyChat(chat_id=1, next_index=0))
        await registry.remove_member(1, 0)
        members = await registry.get_members(1)
        assert [m.position for m in members] == [0, 1]
        assert [m.username for m in members] == ["@u1", "@u2"]
        chat = await registry.get_chat(1)
        assert chat.next_index == 0  # not decremented below 0

    async def test_remove_missing_position_is_noop(self, registry):
        await seed(registry, n=1)
        await registry.remove_member(1, 5)
        assert len(await registry.get_members(1)) == 1

    async def test_update_member_skip(self, registry):
        await seed(registry)
        await registry.update_member_skip(1, 101, "2026-08-03")
        members = await registry.get_members(1)
        assert members[1].skip_date == "2026-08-03"
        await registry.update_member_skip(1, 101, None)
        members = await registry.get_members(1)
        assert members[1].skip_date is None

    async def test_members_isolated_per_chat(self, registry):
        await seed(registry, chat_id=1, n=1)
        await seed(registry, chat_id=2, n=1)
        assert len(await registry.get_members(1)) == 1
        assert len(await registry.get_members(2)) == 1


def _vac(m, i):
    return m.vacations[i]


class TestReorder:
    async def test_move_down_swaps_positions_and_vacations(self, registry):
        await seed(registry)
        await registry.upsert_chat(DailyChat(chat_id=1, next_index=0))
        await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        assert await registry.move_member(1, 0, 1) is True

        members = await registry.get_members(1)
        assert [m.username for m in members] == ["@u1", "@u0", "@u2"]
        # the vacation stays attached to @u0 (now position 1)
        assert [v.start for v in members[1].vacations] == ["2026-08-01"]
        assert members[0].vacations == []
        # next_index followed @u0 from position 0 to 1
        chat = await registry.get_chat(1)
        assert chat.next_index == 1

    async def test_move_up_swaps_and_remaps_next_index(self, registry):
        await seed(registry)
        await registry.upsert_chat(DailyChat(chat_id=1, next_index=2))
        assert await registry.move_member(1, 2, -1) is True

        members = await registry.get_members(1)
        assert [m.username for m in members] == ["@u0", "@u2", "@u1"]
        chat = await registry.get_chat(1)
        assert chat.next_index == 1  # @u2 moved from 2 to 1

    async def test_move_does_not_touch_unrelated_next_index(self, registry):
        await seed(registry)
        await registry.upsert_chat(DailyChat(chat_id=1, next_index=2))
        assert await registry.move_member(1, 0, 1) is True
        chat = await registry.get_chat(1)
        assert chat.next_index == 2

    async def test_move_boundary_and_unknown_are_noops(self, registry):
        await seed(registry)
        assert await registry.move_member(1, 0, -1) is False
        assert await registry.move_member(1, 2, 1) is False
        assert await registry.move_member(1, 99, 1) is False
        members = await registry.get_members(1)
        assert [m.username for m in members] == ["@u0", "@u1", "@u2"]

    async def test_skip_date_follows_the_member(self, registry):
        await seed(registry)
        await registry.update_member_skip(1, 101, "2026-08-05")  # @u1
        assert await registry.move_member(1, 1, -1) is True
        members = await registry.get_members(1)
        assert [m.username for m in members] == ["@u1", "@u0", "@u2"]
        assert members[0].skip_date == "2026-08-05"

    async def test_move_is_isolated_per_chat(self, registry):
        await seed(registry, chat_id=1)
        await seed(registry, chat_id=2)
        assert await registry.move_member(1, 0, 1) is True
        members2 = await registry.get_members(2)
        assert [m.username for m in members2] == ["@u0", "@u1", "@u2"]


class TestVacationsV3:
    """v3 model: many vacations per member in the daily_vacations table."""

    async def test_vacations_table_exists_on_fresh_db(self, registry):
        async with registry._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_vacations'"
        ) as cursor:
            assert (await cursor.fetchone()) is not None

    async def test_legacy_columns_are_nulled_on_fresh_db(self, registry):
        """Fresh DB: the legacy daily_members vacation columns are created but unused."""
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        members = await registry.get_members(1)
        assert members[0].vacations == []
        assert members[0].is_on_vacation("2026-08-05") is False

    async def test_add_vacation_roundtrip(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        v = await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        assert v is not None and v.id is not None
        members = await registry.get_members(1)
        assert len(members[0].vacations) == 1
        assert members[0].vacations[0].start == "2026-08-01"
        assert members[0].vacations[0].end == "2026-08-05"
        assert members[0].is_on_vacation("2026-08-03") is True
        assert members[0].is_on_vacation("2026-07-31") is False
        assert members[0].is_on_vacation("2026-08-06") is False

    async def test_multiple_vacations_per_member(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry.add_vacation(1, 0, "2026-02-01", "2026-02-10")
        await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        members = await registry.get_members(1)
        assert [v.start for v in members[0].vacations] == ["2026-02-01", "2026-08-01"]
        assert members[0].is_on_vacation("2026-02-03") is True
        assert members[0].is_on_vacation("2026-05-01") is False
        assert members[0].is_on_vacation("2026-08-03") is True

    async def test_vacations_isolated_per_chat(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_member(member(2, 0, "B", user_id=2))
        await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        members = await registry.get_members(2)
        assert members[0].vacations == []

    async def test_add_vacation_unknown_position_is_noop(self, registry):
        assert await registry.add_vacation(1, 5, "2026-08-01", "2026-08-05") is None

    async def test_delete_vacation_by_id(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        v1 = await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        v2 = await registry.add_vacation(1, 0, "2026-02-01", "2026-02-10")
        assert await registry.delete_vacation(1, v1.id) is True
        members = await registry.get_members(1)
        assert [v.end for v in members[0].vacations] == ["2026-02-10"]
        assert await registry.delete_vacation(1, v2.id) is True
        members = await registry.get_members(1)
        assert members[0].vacations == []
        # deleting again (or another chat's id) is a False
        assert await registry.delete_vacation(1, v1.id) is False

    async def test_delete_other_chat_vacation_rejected(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_member(member(2, 0, "B", user_id=2))
        v = await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        assert await registry.delete_vacation(2, v.id) is False
        assert len(await registry.get_vacations(1)) == 1

    async def test_get_vacations_by_position(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_member(member(1, 1, "B", user_id=2))
        await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        v_b = await registry.add_vacation(1, 1, "2026-09-01", "2026-09-05")
        stored = await registry.get_vacations(1, position=1)
        assert len(stored) == 1
        assert stored[0].vacation.id == v_b.id

    async def test_legacy_null_start_vacation_semantics(self, registry):
        """Migrated legacy rows have start_date=NULL: 'продолжается до end'."""
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry._db.execute(
            "INSERT INTO daily_vacations (chat_id, position, start_date, end_date) VALUES (1, 0, NULL, '2026-08-05')"
        )
        await registry._db.commit()
        members = await registry.get_members(1)
        assert members[0].is_on_vacation("2026-08-05") is True
        assert members[0].is_on_vacation("2026-08-06") is False
        # 'Жив' до конца сегодняшнего дня: lower-bound does not apply
        assert members[0].is_on_vacation("2020-01-01") is True
        assert members[0].next_vacation("2026-01-01") is None

    async def test_remove_member_shifts_vacation_positions(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_member(member(1, 1, "B", user_id=2))
        await registry.add_member(member(1, 2, "C", user_id=3))
        await registry.add_vacation(1, 1, "2026-08-01", "2026-08-05")
        await registry.add_vacation(1, 2, "2026-09-01", "2026-09-05")
        await registry.remove_member(1, 0)
        members = await registry.get_members(1)
        assert [m.position for m in members] == [0, 1]
        b = members[0]
        c = members[1]
        assert [v.start for v in b.vacations] == ["2026-08-01"]
        assert [v.start for v in c.vacations] == ["2026-09-01"]

    async def test_delete_chat_cleans_vacations(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_vacation(1, 0, "2026-08-01", "2026-08-05")
        await registry.delete_chat(1)
        assert await registry.get_vacations(1) == []

    async def test_replace_members_keeps_vacations_of_unchanged_positions(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1))
        await registry.add_member(member(1, 1, "B", user_id=2))
        await registry.add_vacation(1, 1, "2026-08-01", "2026-08-05")
        await registry.replace_members(
            1,
            [member(1, 0, "A", user_id=1, skip_date="2026-08-05"), member(1, 1, "B", user_id=2)],
        )
        members = await registry.get_members(1)
        assert members[0].skip_date == "2026-08-05"
        assert [v.start for v in members[1].vacations] == ["2026-08-01"]


class TestVacationMigration:
    """Old DBs store one vacation per member as daily_members columns; on
    init_db the values are moved into daily_vacations and the columns are
    cleared. Re-running the migration must be a no-op."""

    async def _make_legacy_db(self, tmp_path):
        """Create a DB that looks like it was written by the old bot: rows in
        daily_members with non-NULL vacation_until / vacation_start and no
        daily_vacations rows."""
        db = tmp_path / "legacy.db"
        reg = DailyRegistry()
        await reg.init_db(str(db))
        await reg.replace_members(1, [member(1, 0, "Иван", user_id=100), member(1, 1, "Пётр", user_id=101)])
        await reg._db.execute(
            "UPDATE daily_members SET vacation_until = '2026-08-05', vacation_start = '2026-08-01' "
            "WHERE chat_id = 1 AND position = 0"
        )
        await reg._db.execute(
            "UPDATE daily_members SET vacation_until = '2026-02-10', vacation_start = NULL "
            "WHERE chat_id = 1 AND position = 1"
        )
        await reg._db.commit()
        await reg.close()
        return db

    async def test_migration_moves_rows_and_clears_columns(self, tmp_path):
        db = await self._make_legacy_db(tmp_path)
        reg2 = DailyRegistry()
        await reg2.init_db(str(db))
        members = await reg2.get_members(1)
        ivan = members[0]
        petr = members[1]
        assert [(v.start, v.end) for v in ivan.vacations] == [("2026-08-01", "2026-08-05")]
        # legacy row without start: NULL start is preserved
        assert [(v.start, v.end) for v in petr.vacations] == [(None, "2026-02-10")]
        assert petr.is_on_vacation("2026-02-01") is True
        assert petr.is_on_vacation("2026-02-11") is False
        async with reg2._db.execute(
            "SELECT COUNT(*) FROM daily_members WHERE vacation_until IS NOT NULL OR vacation_start IS NOT NULL"
        ) as cursor:
            count = (await cursor.fetchone())[0]
        assert count == 0
        await reg2.close()

    async def test_migration_from_original_schema(self, tmp_path):
        """A DB written by the very first bot version: no daily_vacations
        table and no vacation_start column at all."""
        import sqlite3

        db = tmp_path / "v1.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE daily_chats (
                chat_id INTEGER PRIMARY KEY,
                daily_time TEXT NOT NULL DEFAULT '10:00',
                next_index INTEGER NOT NULL DEFAULT 0,
                last_reminder_date TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE daily_members (
                chat_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                first_name TEXT NOT NULL,
                skip_date TEXT,
                vacation_until TEXT,
                PRIMARY KEY (chat_id, position)
            );
            CREATE TABLE daily_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE daily_schedule (
                chat_id INTEGER NOT NULL, date TEXT NOT NULL,
                position INTEGER, PRIMARY KEY (chat_id, date)
            );
            INSERT INTO daily_chats (chat_id, next_index) VALUES (1, 0);
            INSERT INTO daily_members VALUES (1, 0, 100, 'Иван', 'Иван', NULL, '2026-08-05');
            INSERT INTO daily_members VALUES (1, 1, 101, 'Пётр', 'Пётр', NULL, '2026-08-10');
            UPDATE daily_members SET vacation_until = NULL WHERE position = 1;
            """
        )
        con.commit()
        con.close()

        reg = DailyRegistry()
        await reg.init_db(str(db))
        members = await reg.get_members(1)
        assert [(v.start, v.end) for v in members[0].vacations] == [(None, "2026-08-05")]
        assert members[1].vacations == []
        assert members[0].is_on_vacation("2026-08-05") is True
        assert members[0].is_on_vacation("2026-08-06") is False
        await reg.close()

    async def test_migration_is_idempotent(self, tmp_path):
        db = await self._make_legacy_db(tmp_path)
        reg2 = DailyRegistry()
        await reg2.init_db(str(db))
        await reg2.init_db(str(db))  # second run must not duplicate rows
        members = await reg2.get_members(1)
        assert len(members[0].vacations) == 1
        assert len(members[1].vacations) == 1
        await reg2.close()


class TestCostReminderColumns:
    async def test_all_four_columns_exist_on_fresh_db(self, registry):
        async with registry._db.execute("PRAGMA table_info(daily_chats)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        assert "cost_reminder_enabled" in cols
        assert "cost_reminder_time" in cols
        assert "cost_reminder_last_week_date" in cols
        assert "cost_reminder_last_month_date" in cols

    async def test_default_values_on_fresh_insert(self, registry):
        await registry.upsert_chat(DailyChat(chat_id=1))
        async with registry._db.execute(
            "SELECT cost_reminder_enabled, cost_reminder_time, "
            "cost_reminder_last_week_date, cost_reminder_last_month_date "
            "FROM daily_chats WHERE chat_id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
        assert row[1] == "17:00"
        assert row[2] is None
        assert row[3] is None

    async def test_upsert_get_roundtrip(self, registry):
        chat = DailyChat(chat_id=1, daily_time="09:15", next_index=2, last_reminder_date="2026-08-03")
        await registry.upsert_chat(chat)
        loaded = await registry.get_chat(1)
        assert loaded is not None
        assert loaded.daily_time == "09:15"
        assert loaded.next_index == 2
        assert loaded.last_reminder_date == "2026-08-03"

    async def test_upsert_get_cost_reminder_roundtrip(self, registry):
        chat = DailyChat(
            chat_id=1,
            cost_reminder_enabled=True,
            cost_reminder_time="18:30",
            cost_reminder_last_week_date="2026-08-07",
            cost_reminder_last_month_date="2026-08-28",
        )
        await registry.upsert_chat(chat)
        loaded = await registry.get_chat(1)
        assert loaded is not None
        assert loaded.cost_reminder_enabled is True
        assert loaded.cost_reminder_time == "18:30"
        assert loaded.cost_reminder_last_week_date == "2026-08-07"
        assert loaded.cost_reminder_last_month_date == "2026-08-28"

    async def test_list_chats_includes_cost_reminder_values(self, registry):
        await registry.upsert_chat(DailyChat(
            chat_id=1,
            cost_reminder_enabled=True,
            cost_reminder_time="19:00",
            cost_reminder_last_week_date="2026-08-07",
            cost_reminder_last_month_date="2026-08-28",
        ))
        await registry.upsert_chat(DailyChat(chat_id=2))
        chats = await registry.list_chats()
        c1 = next(c for c in chats if c.chat_id == 1)
        c2 = next(c for c in chats if c.chat_id == 2)
        assert c1.cost_reminder_enabled is True
        assert c1.cost_reminder_time == "19:00"
        assert c1.cost_reminder_last_week_date == "2026-08-07"
        assert c1.cost_reminder_last_month_date == "2026-08-28"
        assert c2.cost_reminder_enabled is False
        assert c2.cost_reminder_time == "17:00"
        assert c2.cost_reminder_last_week_date is None
        assert c2.cost_reminder_last_month_date is None

    async def test_upsert_overwrites_cost_reminder(self, registry):
        await registry.upsert_chat(DailyChat(
            chat_id=1,
            cost_reminder_enabled=True,
            cost_reminder_time="18:00",
        ))
        await registry.upsert_chat(DailyChat(
            chat_id=1,
            cost_reminder_enabled=False,
            cost_reminder_time="20:00",
            cost_reminder_last_week_date="2026-08-07",
        ))
        loaded = await registry.get_chat(1)
        assert loaded.cost_reminder_enabled is False
        assert loaded.cost_reminder_time == "20:00"
        assert loaded.cost_reminder_last_week_date == "2026-08-07"


class TestSwapScheduleDates:
    BASE = datetime.date(2026, 8, 4)

    async def test_swap_exchanges_two_positions_only(self, registry):
        d0, d1, d2 = (
            self.BASE,
            self.BASE + datetime.timedelta(days=1),
            self.BASE + datetime.timedelta(days=2),
        )
        await registry.set_schedule(1, [(str(d0), 0), (str(d1), 1), (str(d2), 2)])
        await registry.swap_schedule_dates(1, str(d0), str(d1))
        schedule = await registry.get_schedule(1)
        assert schedule[str(d0)] == 1
        assert schedule[str(d1)] == 0
        assert schedule[str(d2)] == 2
        assert len(schedule) == 3

    async def test_swap_with_missing_date_is_noop(self, registry):
        d0, d1, d2 = (
            self.BASE,
            self.BASE + datetime.timedelta(days=1),
            self.BASE + datetime.timedelta(days=2),
        )
        rows = [(str(d0), 0), (str(d1), 1), (str(d2), 2)]
        await registry.set_schedule(1, rows)
        await registry.swap_schedule_dates(1, str(d0), "2030-01-01")
        assert await registry.get_schedule(1) == dict(rows)

    async def test_double_swap_restores_original(self, registry):
        d0, d1, d2 = (
            self.BASE,
            self.BASE + datetime.timedelta(days=1),
            self.BASE + datetime.timedelta(days=2),
        )
        rows = [(str(d0), 0), (str(d1), 1), (str(d2), 2)]
        await registry.set_schedule(1, rows)
        await registry.swap_schedule_dates(1, str(d0), str(d1))
        await registry.swap_schedule_dates(1, str(d0), str(d1))
        assert await registry.get_schedule(1) == dict(rows)


class TestEnsureScheduleExtension:
    """Extension cursor must continue from the last ASSIGNED leader, not from
    the stale next_index pointer (which names today's leader)."""

    async def test_ensure_schedule_extension_continues_after_weekend_tail(self, registry):
        await registry.upsert_chat(DailyChat(chat_id=1, daily_time="10:00", next_index=0))
        await seed(registry)
        members = await registry.get_members(1)
        chat = await registry.get_chat(1)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 3), workdays=mon_fri)
        # the loop ticks daily incl. weekends; the Sat tick leaves the window tail on None rows
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 15), workdays=mon_fri)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 17), workdays=mon_fri)

        schedule = await registry.get_schedule(1)
        d = datetime.date(2026, 8, 17)
        while d <= datetime.date(2026, 8, 31):
            if d.weekday() < 5:
                assert schedule[str(d)] is not None, str(d)
            else:
                assert schedule[str(d)] is None, str(d)
            d += datetime.timedelta(days=1)
        assert schedule["2026-08-17"] == (schedule["2026-08-14"] + 1) % 3
        seq = [pos for _, pos in sorted(schedule.items()) if pos is not None]
        for prev, cur in zip(seq, seq[1:]):
            assert cur == (prev + 1) % 3

    async def test_ensure_schedule_extension_all_none_falls_back_to_next_index(self, registry):
        await registry.upsert_chat(DailyChat(chat_id=1, daily_time="10:00", next_index=2))
        await seed(registry)
        for i in range(3):
            await registry.add_vacation(1, i, None, "2027-01-01")
        members = await registry.get_members(1)
        chat = await registry.get_chat(1)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 3), workdays=mon_fri)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 17), workdays=mon_fri)

        schedule = await registry.get_schedule(1)
        d = datetime.date(2026, 8, 18)
        while d <= datetime.date(2026, 8, 31):
            assert schedule[str(d)] is None, str(d)
            d += datetime.timedelta(days=1)
