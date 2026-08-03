"""Daily standup storage: DailyRegistry on aiosqlite."""
from __future__ import annotations

from typing import List, Optional

import aiosqlite

from ppbot.daily import DailyChat, DailyMember


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
        # migrate older DBs: add the scheduler-state columns if missing
        async with self._db.execute("PRAGMA table_info(daily_chats)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "last_start_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_start_date TEXT")
        if "last_catchup_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_catchup_date TEXT")
        if "last_advance_date" not in cols:
            await self._db.execute("ALTER TABLE daily_chats ADD COLUMN last_advance_date TEXT")
        # migrate older DBs: add the vacation column if missing
        async with self._db.execute("PRAGMA table_info(daily_members)") as cursor:
            mcols = [row[1] for row in await cursor.fetchall()]
        if "vacation_until" not in mcols:
            await self._db.execute("ALTER TABLE daily_members ADD COLUMN vacation_until TEXT")
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
            "last_start_date, last_catchup_date, last_advance_date "
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
            )

    async def upsert_chat(self, chat: DailyChat):
        await self._db.execute(
            """
            INSERT INTO daily_chats
                (chat_id, daily_time, next_index, last_reminder_date,
                 last_start_date, last_catchup_date, last_advance_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                daily_time = excluded.daily_time,
                next_index = excluded.next_index,
                last_reminder_date = excluded.last_reminder_date,
                last_start_date = excluded.last_start_date,
                last_catchup_date = excluded.last_catchup_date,
                last_advance_date = excluded.last_advance_date
            """,
            (
                chat.chat_id,
                chat.daily_time,
                chat.next_index,
                chat.last_reminder_date,
                chat.last_start_date,
                chat.last_catchup_date,
                chat.last_advance_date,
            ),
        )
        await self._db.commit()

    async def list_chats(self) -> List[DailyChat]:
        query = (
            "SELECT chat_id, daily_time, next_index, last_reminder_date, "
            "last_start_date, last_catchup_date, last_advance_date "
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
            )
            for r in rows
        ]

    # ---- members ----

    async def get_members(self, chat_id: int) -> List[DailyMember]:
        query = (
            "SELECT chat_id, position, user_id, username, first_name, skip_date, vacation_until "
            "FROM daily_members WHERE chat_id = ? ORDER BY position"
        )
        async with self._db.execute(query, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
        return [
            DailyMember(
                chat_id=r[0],
                position=r[1],
                user_id=r[2],
                username=r[3],
                first_name=r[4],
                skip_date=r[5],
                vacation_until=r[6],
            )
            for r in rows
        ]

    async def replace_members(self, chat_id: int, members: List[DailyMember]):
        async with self._db.execute("BEGIN"):
            await self._db.execute("DELETE FROM daily_members WHERE chat_id = ?", (chat_id,))
            await self._db.executemany(
                "INSERT INTO daily_members (chat_id, position, user_id, username, first_name, skip_date, vacation_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (m.chat_id, m.position, m.user_id, m.username, m.first_name, m.skip_date, m.vacation_until)
                    for m in members
                ],
            )
        await self._db.commit()

    async def add_member(self, member: DailyMember):
        await self._db.execute(
            "INSERT INTO daily_members (chat_id, position, user_id, username, first_name, skip_date, vacation_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (member.chat_id, member.position, member.user_id, member.username, member.first_name, member.skip_date, member.vacation_until),
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

    async def update_member_vacation(self, chat_id: int, position: int, vacation_until: Optional[str]):
        await self._db.execute(
            "UPDATE daily_members SET vacation_until = ? WHERE chat_id = ? AND position = ?",
            (vacation_until, chat_id, position),
        )
        await self._db.commit()

    async def delete_chat(self, chat_id: int):
        """Reset all daily data for a chat: config, rotation state, members."""
        await self._db.execute("DELETE FROM daily_chats WHERE chat_id = ?", (chat_id,))
        await self._db.execute("DELETE FROM daily_members WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    async def close(self):
        if self._db is not None:
            await self._db.close()
