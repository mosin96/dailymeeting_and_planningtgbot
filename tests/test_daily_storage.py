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


class TestVacation:
    async def test_vacation_column_exists_on_fresh_db(self, registry):
        async with registry._db.execute("PRAGMA table_info(daily_members)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        assert "vacation_until" in cols

    async def test_add_get_vacation_roundtrip(self, registry):
        await registry.add_member(
            member(1, 0, "Иван", user_id=100, vacation_until="2026-08-05")
        )
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-05"

    async def test_update_member_vacation_sets_and_clears(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry.update_member_vacation(1, 0, "2026-08-05")
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-05"
        await registry.update_member_vacation(1, 0, None)
        members = await registry.get_members(1)
        assert members[0].vacation_until is None

    async def test_update_member_vacation_by_position_works_for_username_member(self, registry):
        """Members added by @username have user_id=None; update must key on position."""
        await registry.add_member(member(1, 0, "testuser", username="testuser"))
        await registry.update_member_vacation(1, 0, "2026-08-05")
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-05"

    async def test_update_member_vacation_unknown_position_is_noop(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry.update_member_vacation(1, 5, "2026-08-05")
        members = await registry.get_members(1)
        assert members[0].vacation_until is None

    async def test_replace_members_preserves_vacation(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1, vacation_until="2026-08-05"))
        await registry.add_member(member(1, 1, "B", user_id=2))
        await registry.replace_members(
            1,
            [
                member(1, 0, "A", user_id=1, vacation_until="2026-08-06"),
                member(1, 1, "B", user_id=2),
            ],
        )
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-06"
        assert members[1].vacation_until is None


class TestVacationStart:
    async def test_vacation_start_column_exists_on_fresh_db(self, registry):
        """Given a freshly initialized registry, daily_members has vacation_start."""
        async with registry._db.execute("PRAGMA table_info(daily_members)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        assert "vacation_start" in cols

    async def test_add_get_vacation_start_roundtrip(self, registry):
        await registry.add_member(
            member(1, 0, "Иван", user_id=100, vacation_until="2026-08-10", vacation_start="2026-08-01")
        )
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-10"
        assert members[0].vacation_start == "2026-08-01"

    async def test_update_member_vacation_sets_both_fields(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry.update_member_vacation(1, 0, "2026-08-10", "2026-08-01")
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-10"
        assert members[0].vacation_start == "2026-08-01"

    async def test_update_member_vacation_clears_both_fields(self, registry):
        await registry.add_member(
            member(1, 0, "Иван", user_id=100, vacation_until="2026-08-10", vacation_start="2026-08-01")
        )
        await registry.update_member_vacation(1, 0, None, None)
        members = await registry.get_members(1)
        assert members[0].vacation_until is None
        assert members[0].vacation_start is None

    async def test_update_member_vacation_three_args_backward_compat(self, registry):
        await registry.add_member(member(1, 0, "Иван", user_id=100))
        await registry.update_member_vacation(1, 0, "2026-08-05")
        members = await registry.get_members(1)
        assert members[0].vacation_until == "2026-08-05"
        assert members[0].vacation_start is None

    async def test_replace_members_preserves_vacation_start(self, registry):
        await registry.add_member(member(1, 0, "A", user_id=1, vacation_start="2026-08-01"))
        await registry.replace_members(1, [member(1, 0, "A", user_id=1, vacation_start="2026-08-02")])
        members = await registry.get_members(1)
        assert members[0].vacation_start == "2026-08-02"


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
            await registry.update_member_vacation(1, i, "2027-01-01")
        members = await registry.get_members(1)
        chat = await registry.get_chat(1)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 3), workdays=mon_fri)
        await registry.ensure_schedule(chat, members, datetime.date(2026, 8, 17), workdays=mon_fri)

        schedule = await registry.get_schedule(1)
        d = datetime.date(2026, 8, 18)
        while d <= datetime.date(2026, 8, 31):
            assert schedule[str(d)] is None, str(d)
            d += datetime.timedelta(days=1)
