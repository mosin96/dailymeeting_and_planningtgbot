"""Daily standup storage: DailyRegistry on aiosqlite."""
from __future__ import annotations

import datetime
from typing import Dict, List, NamedTuple, Optional

import aiosqlite

from ppbot.daily import DailyChat, DailyMember, Vacation, SCHEDULE_DAYS, build_schedule


class StoredVacation(NamedTuple):
    """One daily_vacations row: the member it belongs to + the period."""
    position: int
    vacation: Vacation


class DailyRegistry:
    def __init__(self):
        self._db = None

    async def init_db(self, db_path: str):
        con = aiosqlite.connect(db_path)
        con.daemon = True
        self._db = await con
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_chats (
                chat_id INTEGER PRIMARY KEY,
                daily_time TEXT NOT NULL DEFAULT '10:00',
                next_index INTEGER NOT NULL DEFAULT 0,
                last_reminder_date TEXT,
                last_start_date TEXT,
                last_catchup_date TEXT,
                last_advance_date TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_members (
                chat_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                first_name TEXT NOT NULL,
                skip_date TEXT,
                vacation_until TEXT,
                PRIMARY KEY (chat_id, position)
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_schedule (
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                position INTEGER,
                PRIMARY KEY (chat_id, date)
            )
        """)
        # Multiple vacations per member: standalone table (v3 vacation model).
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_vacations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                start_date TEXT,
                end_date TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_vac_chat_pos ON daily_vacations(chat_id, position)"
        )
        # migrate older DBs: add the scheduler-state columns if missing
        async with self._db.execute("PRAGMA table_info(daily_chats)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "last_start_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_start_date TEXT")
        if "last_catchup_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_catchup_date TEXT")
        if "last_advance_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_advance_date TEXT")
        if "cost_reminder_enabled" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN cost_reminder_enabled INTEGER DEFAULT 0")
        if "cost_reminder_time" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN cost_reminder_time TEXT DEFAULT '17:00'")
        if "cost_reminder_last_week_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN cost_reminder_last_week_date TEXT")
        if "cost_reminder_last_month_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN cost_reminder_last_month_date TEXT")
        # migrate older DBs: add the vacation column if missing
        async with self._db.execute("PRAGMA table_info(daily_members)") as cursor:
            mcols = [row[1] for row in await cursor.fetchall()]
        if "vacation_until" not in mcols:
            await self._db.execute("ALTER TABLE daily_members ADD COLUMN vacation_until TEXT")
        if "vacation_start" not in mcols:
            await self._db.execute("ALTER TABLE daily_members ADD COLUMN vacation_start TEXT")
        # migrate the v2 vacation model (one vacation per member, stored as
        # daily_members columns) into the v3 daily_vacations table, then clear
        # the legacy columns. Idempotent: after the first pass no non-NULL
        # legacy values remain, so re-running inserts nothing.
        async with self._db.execute(
            "SELECT chat_id, position, vacation_until, vacation_start "
            "FROM daily_members WHERE vacation_until IS NOT NULL"
        ) as cursor:
            legacy = await cursor.fetchall()
        if legacy:
            await self._db.executemany(
                "INSERT INTO daily_vacations (chat_id, position, start_date, end_date) "
                "VALUES (?, ?, ?, ?)",
                [
                    (row[0], row[1], row[3], row[2])
                    for row in legacy
                ],
            )
            await self._db.execute(
                "UPDATE daily_members SET vacation_until = NULL, vacation_start = NULL "
                "WHERE vacation_until IS NOT NULL"
            )
        await self._db.commit()

    # ---- meta ----

    async def get_meta(self, key: str) -> Optional[str]:
        query = "SELECT value FROM daily_meta WHERE key = ?"
        async with self._db.execute(query, (key,)) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_meta(self, key: str, value: str):
        await self._db.execute(
            "INSERT INTO daily_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.commit()

    # ---- chats ----

    async def get_chat(self, chat_id: int) -> Optional[DailyChat]:
        query = (
            "SELECT chat_id, daily_time, next_index, last_reminder_date, "
            "last_start_date, last_catchup_date, last_advance_date, "
            "cost_reminder_enabled, cost_reminder_time, "
            "cost_reminder_last_week_date, cost_reminder_last_month_date "
            "FROM daily_chats WHERE chat_id = ?"
        )
        async with self._db.execute(query, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return DailyChat(
                chat_id=row[0],
                daily_time=row[1],
                next_index=row[2],
                last_reminder_date=row[3],
                last_start_date=row[4],
                last_catchup_date=row[5],
                last_advance_date=row[6],
                cost_reminder_enabled=bool(row[7]),
                cost_reminder_time=row[8],
                cost_reminder_last_week_date=row[9],
                cost_reminder_last_month_date=row[10],
            )

    async def upsert_chat(self, chat: DailyChat):
        await self._db.execute(
            """
            INSERT INTO daily_chats
                (chat_id, daily_time, next_index, last_reminder_date,
                 last_start_date, last_catchup_date, last_advance_date,
                 cost_reminder_enabled, cost_reminder_time,
                 cost_reminder_last_week_date, cost_reminder_last_month_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                daily_time = excluded.daily_time,
                next_index = excluded.next_index,
                last_reminder_date = excluded.last_reminder_date,
                last_start_date = excluded.last_start_date,
                last_catchup_date = excluded.last_catchup_date,
                last_advance_date = excluded.last_advance_date,
                cost_reminder_enabled = excluded.cost_reminder_enabled,
                cost_reminder_time = excluded.cost_reminder_time,
                cost_reminder_last_week_date = excluded.cost_reminder_last_week_date,
                cost_reminder_last_month_date = excluded.cost_reminder_last_month_date
            """,
            (
                chat.chat_id,
                chat.daily_time,
                chat.next_index,
                chat.last_reminder_date,
                chat.last_start_date,
                chat.last_catchup_date,
                chat.last_advance_date,
                int(chat.cost_reminder_enabled),
                chat.cost_reminder_time,
                chat.cost_reminder_last_week_date,
                chat.cost_reminder_last_month_date,
            ),
        )
        await self._db.commit()

    async def list_chats(self) -> List[DailyChat]:
        query = (
            "SELECT chat_id, daily_time, next_index, last_reminder_date, "
            "last_start_date, last_catchup_date, last_advance_date, "
            "cost_reminder_enabled, cost_reminder_time, "
            "cost_reminder_last_week_date, cost_reminder_last_month_date "
            "FROM daily_chats"
        )
        async with self._db.execute(query) as cursor:
            rows = await cursor.fetchall()
        return [
            DailyChat(
                chat_id=r[0],
                daily_time=r[1],
                next_index=r[2],
                last_reminder_date=r[3],
                last_start_date=r[4],
                last_catchup_date=r[5],
                last_advance_date=r[6],
                cost_reminder_enabled=bool(r[7]),
                cost_reminder_time=r[8],
                cost_reminder_last_week_date=r[9],
                cost_reminder_last_month_date=r[10],
            )
            for r in rows
        ]

    async def get_members(self, chat_id: int) -> List[DailyMember]:
        query = (
            "SELECT chat_id, position, user_id, username, first_name, skip_date "
            "FROM daily_members WHERE chat_id = ? ORDER BY position"
        )
        async with self._db.execute(query, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
        # Vacations live in the daily_vacations table (v3 model): many periods
        # per member, keyed by (chat_id, position).
        vacations = await self.get_vacations(chat_id)
        by_position: Dict[int, List[Vacation]] = {}
        for v in vacations:
            by_position.setdefault(v.position, []).append(v.vacation)
        result = []
        for r in rows:
            db_username = r[3]
            db_first_name = r[4]
            if db_username and not db_first_name:
                name = db_username
            elif db_first_name and not db_username:
                name = db_first_name
            elif db_first_name and db_username and db_first_name != db_username:
                name = "{} @{}".format(db_first_name, db_username)
            elif db_first_name:
                name = db_first_name
            else:
                name = db_username or ""
            result.append(DailyMember(
                chat_id=r[0],
                position=r[1],
                user_id=r[2],
                username=name,
                skip_date=r[5],
                vacations=by_position.get(r[1], []),
            ))
        return result

    async def replace_members(self, chat_id: int, members: List[DailyMember]):
        async with self._db.execute("BEGIN"):
            await self._db.execute("DELETE FROM daily_members WHERE chat_id = ?", (chat_id,))
            await self._db.executemany(
                "INSERT INTO daily_members (chat_id, position, user_id, username, first_name, skip_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (m.chat_id, m.position, m.user_id, m.username, m.username, m.skip_date)
                    for m in members
                ],
            )
        await self._db.commit()

    async def add_member(self, member: DailyMember):
        await self._db.execute(
            "INSERT INTO daily_members (chat_id, position, user_id, username, first_name, skip_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (member.chat_id, member.position, member.user_id, member.username, member.username, member.skip_date),
        )
        await self._db.commit()

    async def remove_member(self, chat_id: int, position: int):
        """Remove member at position; reindex positions and correct next_index in daily_chats."""
        async with self._db.execute("BEGIN"):
            async with self._db.execute(
                "SELECT position FROM daily_members WHERE chat_id = ? AND position = ?",
                (chat_id, position),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await self._db.commit()
                return
            await self._db.execute(
                "DELETE FROM daily_members WHERE chat_id = ? AND position = ?",
                (chat_id, position),
            )
            await self._db.execute(
                "UPDATE daily_members SET position = position - 1 "
                "WHERE chat_id = ? AND position > ?",
                (chat_id, position),
            )
            # keep the vacation table in sync with the reindexed positions
            await self._db.execute(
                "DELETE FROM daily_vacations WHERE chat_id = ? AND position = ?",
                (chat_id, position),
            )
            await self._db.execute(
                "UPDATE daily_vacations SET position = position - 1 "
                "WHERE chat_id = ? AND position > ?",
                (chat_id, position),
            )
            chat = await self.get_chat(chat_id)
            if chat is not None:
                if chat.next_index > position:
                    chat.next_index = max(0, chat.next_index - 1)
                await self.upsert_chat(chat)
        await self._db.commit()

    async def update_member_skip(self, chat_id: int, user_id: int, skip_date: Optional[str]):
        await self._db.execute(
            "UPDATE daily_members SET skip_date = ? WHERE chat_id = ? AND user_id = ?",
            (skip_date, chat_id, user_id),
        )
        await self._db.commit()

    async def move_member(self, chat_id: int, position: int, direction: int) -> bool:
        """Swap a member with the adjacent one (direction -1 up, +1 down).

        Positions define the rotation order, so this swaps the two rows and
        moves their vacations with them. `daily_chats.next_index` is remapped
        so it keeps pointing at the same person (today's leader does not change
        because of a reorder). Returns False on boundary/unknown positions.
        """
        target = position + direction
        if direction not in (-1, 1) or target < 0:
            return False
        async with self._db.execute("BEGIN"):
            async with self._db.execute(
                "SELECT position FROM daily_members WHERE chat_id = ? AND position IN (?, ?)",
                (chat_id, position, target),
            ) as cursor:
                found = {row[0] for row in await cursor.fetchall()}
            if found != {position, target}:
                await self._db.commit()
                return False
            # swap through a temporary position because position is the PK
            await self._db.execute(
                "UPDATE daily_members SET position = -1 WHERE chat_id = ? AND position = ?",
                (chat_id, position),
            )
            await self._db.execute(
                "UPDATE daily_members SET position = ? WHERE chat_id = ? AND position = ?",
                (position, chat_id, target),
            )
            await self._db.execute(
                "UPDATE daily_members SET position = ? WHERE chat_id = ? AND position = -1",
                (target, chat_id),
            )
            # vacations follow their member
            await self._db.execute(
                "UPDATE daily_vacations SET position = -1 WHERE chat_id = ? AND position = ?",
                (chat_id, position),
            )
            await self._db.execute(
                "UPDATE daily_vacations SET position = ? WHERE chat_id = ? AND position = ?",
                (position, chat_id, target),
            )
            await self._db.execute(
                "UPDATE daily_vacations SET position = ? WHERE chat_id = ? AND position = -1",
                (target, chat_id),
            )
            chat = await self.get_chat(chat_id)
            if chat is not None:
                if chat.next_index == position:
                    chat.next_index = target
                elif chat.next_index == target:
                    chat.next_index = position
                await self.upsert_chat(chat)
        await self._db.commit()
        return True

    async def bind_member_user_id(self, chat_id: int, position: int, user_id: int) -> bool:
        """Attach a Telegram id to a member added manually by @username.

        Only rows without a known id are updated (never reassigns an id).
        Returns True when the row was bound.
        """
        async with self._db.execute(
            "UPDATE daily_members SET user_id = ? "
            "WHERE chat_id = ? AND position = ? AND user_id IS NULL",
            (user_id, chat_id, position),
        ) as cursor:
            bound = cursor.rowcount > 0
            await self._db.commit()
        return bound

    # ---- vacations (v3 model) ----

    async def get_vacations(self, chat_id: int, position: Optional[int] = None) -> List[StoredVacation]:
        """All vacations of a chat (or one member), ordered by position/date.

        Legacy migrated rows have start_date = NULL ("продолжается до end").
        """
        query = (
            "SELECT id, position, start_date, end_date FROM daily_vacations "
            "WHERE chat_id = ?"
        )
        params: tuple = (chat_id,)
        if position is not None:
            query += " AND position = ?"
            params = (chat_id, position)
        query += (
            " ORDER BY position,"
            " CASE WHEN start_date IS NULL THEN 0 ELSE 1 END, start_date, end_date, id"
        )
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [
            StoredVacation(
                position=row[1],
                vacation=Vacation(start=row[2], end=row[3], id=row[0]),
            )
            for row in rows
        ]

    async def add_vacation(
        self, chat_id: int, position: int, start: Optional[str], end: str
    ) -> Optional[Vacation]:
        """Insert a vacation period; returns the stored Vacation or None if
        the member does not exist."""
        async with self._db.execute(
            "SELECT position FROM daily_members WHERE chat_id = ? AND position = ?",
            (chat_id, position),
        ) as cursor:
            member = await cursor.fetchone()
        if member is None:
            return None
        await self._db.execute(
            "INSERT INTO daily_vacations (chat_id, position, start_date, end_date) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, position, start, end),
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cursor:
            vac_id = (await cursor.fetchone())[0]
        return Vacation(start=start, end=end, id=vac_id)

    async def delete_vacation(self, chat_id: int, vacation_id: int) -> bool:
        """Delete one vacation by id. True when a row was actually removed."""
        async with self._db.execute(
            "DELETE FROM daily_vacations WHERE chat_id = ? AND id = ?",
            (chat_id, vacation_id),
        ) as cursor:
            deleted = cursor.rowcount > 0
            await self._db.commit()
        return deleted

    async def delete_chat(self, chat_id: int):
        """Reset all daily data for a chat: config, rotation state, members, schedule."""
        await self._db.execute("DELETE FROM daily_chats WHERE chat_id = ?", (chat_id,))
        await self._db.execute("DELETE FROM daily_members WHERE chat_id = ?", (chat_id,))
        await self._db.execute("DELETE FROM daily_schedule WHERE chat_id = ?", (chat_id,))
        await self._db.execute("DELETE FROM daily_vacations WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    # ---- leader schedule ----

    async def get_schedule(self, chat_id: int) -> Dict[str, Optional[int]]:
        """Return {date: position} for the chat's precomputed leader window."""
        query = "SELECT date, position FROM daily_schedule WHERE chat_id = ?"
        async with self._db.execute(query, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
        return {d: pos for d, pos in rows}

    async def set_schedule(self, chat_id: int, rows: List[tuple]):
        """Replace the whole precomputed leader window for a chat.

        `rows` is an iterable of (date, position) where position may be None
        (no available leader that day).
        """
        async with self._db.execute("BEGIN"):
            await self._db.execute("DELETE FROM daily_schedule WHERE chat_id = ?", (chat_id,))
            await self._db.executemany(
                "INSERT INTO daily_schedule (chat_id, date, position) VALUES (?, ?, ?)",
                [(chat_id, d, pos) for d, pos in rows],
            )
        await self._db.commit()

    async def swap_schedule_dates(self, chat_id: int, date_a: str, date_b: str) -> None:
        """Swap the scheduled positions for two dates, transactionally.

        Other rows are untouched. No-op (commit + return) if either date has
        no row in the chat's schedule window.
        """
        async with self._db.execute("BEGIN"):
            async with self._db.execute(
                "SELECT date, position FROM daily_schedule WHERE chat_id = ? AND date IN (?, ?)",
                (chat_id, date_a, date_b),
            ) as cursor:
                rows = await cursor.fetchall()
            pos = {d: p for d, p in rows}
            if date_a not in pos or date_b not in pos:
                await self._db.commit()
                return
            await self._db.execute(
                "UPDATE daily_schedule SET position = ? WHERE chat_id = ? AND date = ?",
                (pos[date_b], chat_id, date_a),
            )
            await self._db.execute(
                "UPDATE daily_schedule SET position = ? WHERE chat_id = ? AND date = ?",
                (pos[date_a], chat_id, date_b),
            )
        await self._db.commit()

    async def rebuild_schedule(
        self,
        chat_id: int,
        next_index: int,
        members: List[DailyMember],
        today,
        workdays=None,
        *,
        days=SCHEDULE_DAYS + 1,
    ) -> None:
        """Rebuild the leader window starting from `today`.

        Called after a roster/rotation change (add/remove/substitute/skip/
        manual leader/vacation) so the persisted schedule reflects the updated
        rotation and availability. Builds `days` rows (default: the standard
        15-row window covering today..today+14).
        """
        rows = await build_schedule(members, today, next_index, days, workdays=workdays)
        await self.set_schedule(chat_id, [(d.isoformat(), p) for d, p in rows])

    async def ensure_schedule(
        self,
        chat: DailyChat,
        members: List[DailyMember],
        today,
        workdays=None,
    ) -> None:
        """Guarantee the daily_schedule window covers today..today+14.

        If the schedule is missing or stale (today absent), rebuild the full
        window from today with `chat.next_index` as the seed. Otherwise extend
        the tail one day at a time (the new 14th-day leader, continuing the
        rotation after the last scheduled position) and trim rows dated more
        than 14 days ago.

        Also keeps `chat.next_index` in sync with today's scheduled leader so
        the on-the-fly rotation readers (/who, /daily, /substitute) match the
        announced reminder without depending on the schedule table.
        """
        today_s = str(today)
        schedule = await self.get_schedule(chat.chat_id)
        if not schedule or today_s not in schedule:
            rows = await build_schedule(members, today, chat.next_index, SCHEDULE_DAYS + 1, workdays=workdays)
            await self.set_schedule(chat.chat_id, [(d.isoformat(), p) for d, p in rows])
            schedule = {d.isoformat(): p for d, p in rows}

        target = today + datetime.timedelta(days=SCHEDULE_DAYS)
        target_s = str(target)
        if target_s not in schedule:
            last_date = max(schedule)
            n = len(members)
            if n == 0:
                cursor = 0
            else:
                assigned = [(d, pos) for d, pos in schedule.items() if pos is not None]
                cursor = (max(assigned)[1] + 1) % n if assigned else chat.next_index % n
            start = datetime.date.fromisoformat(last_date) + datetime.timedelta(days=1)
            days = (target - start).days + 1
            rows = await build_schedule(members, start, cursor, days, workdays=workdays)
            await self.extend_schedule(chat.chat_id, [(d.isoformat(), p) for d, p in rows])
            schedule.update({d.isoformat(): p for d, p in rows})

        earliest = today - datetime.timedelta(days=SCHEDULE_DAYS)
        await self.trim_schedule(chat.chat_id, str(earliest))

        position = schedule.get(today_s)
        if position is not None and chat.next_index != position:
            chat.next_index = position
            await self.upsert_chat(chat)

    async def extend_schedule(self, chat_id: int, rows: List[tuple]):
        """Append new (date, position) rows and drop rows older than the
        retained 14-day window for a chat."""
        if not rows:
            return
        async with self._db.execute("BEGIN"):
            await self._db.executemany(
                "INSERT INTO daily_schedule (chat_id, date, position) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, date) DO UPDATE SET position = excluded.position",
                [(chat_id, d, pos) for d, pos in rows],
            )
        await self._db.commit()

    async def trim_schedule(self, chat_id: int, earliest: str):
        """Delete schedule rows dated before `earliest` (ISO, exclusive)."""
        await self._db.execute(
            "DELETE FROM daily_schedule WHERE chat_id = ? AND date < ?",
            (chat_id, earliest),
        )
        await self._db.commit()

    async def close(self):
        if self._db is not None:
            await self._db.close()


async def refresh_schedule_window(daily: "DailyRegistry", chat_id: int, today, workdays=None) -> None:
    """Recompute the persisted 14-day leader window after a roster/vacation change.

    Shared by the chat handlers and the mini-app HTTP API (a vacation change
    alters who is available on which day).
    """
    chat = await daily.get_chat(chat_id)
    if chat is None:
        return
    members = await daily.get_members(chat_id)
    await daily.rebuild_schedule(chat_id, chat.next_index, members, today, workdays=workdays)
