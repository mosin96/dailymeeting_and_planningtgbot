"""Unit tests for the shared daily service (used by chat and mini-app)."""
import datetime

import pytest

from ppbot.daily import DailyChat, DailyMember
from ppbot.daily_service import (
    add_member,
    build_overview,
    move_member,
    remove_member,
    set_daily_time,
    update_settings,
)
from ppbot.daily_storage import DailyRegistry

CHAT_ID = -1001


@pytest.fixture
async def storage(tmp_path):
    registry = DailyRegistry()
    await registry.init_db(str(tmp_path / "daily.db"))
    yield registry
    await registry.close()


async def _seed(storage):
    await storage.upsert_chat(DailyChat(chat_id=CHAT_ID, next_index=0))
    for position, name in enumerate(["@a", "@b", "@c"]):
        await storage.add_member(
            DailyMember(chat_id=CHAT_ID, position=position, user_id=position + 1, username=name)
        )


@pytest.mark.asyncio
async def test_overview_reports_leader_tomorrow_and_vacationers(storage):
    await _seed(storage)
    today = datetime.date(2026, 8, 3)  # Monday
    tomorrow = today + datetime.timedelta(days=1)
    await storage.set_schedule(
        CHAT_ID, [(today.isoformat(), 0), (tomorrow.isoformat(), 1)]
    )
    await storage.add_vacation(CHAT_ID, 2, today.isoformat(), today.isoformat())

    overview = await build_overview(storage, CHAT_ID, today)
    assert overview["is_workday"] is True
    assert overview["leader_position"] == 0
    assert overview["leader_name"] == "@a"
    assert overview["tomorrow_position"] == 1
    assert overview["tomorrow_name"] == "@b"
    assert overview["members_count"] == 3
    assert overview["available_count"] == 2
    assert [v["name"] for v in overview["vacations_today"]] == ["@c"]


async def _weekdays(day):
    return day.weekday() < 5


@pytest.mark.asyncio
async def test_overview_on_nonworkday_finds_next_daily(storage):
    await _seed(storage)
    saturday = datetime.date(2026, 8, 8)
    overview = await build_overview(
        storage, CHAT_ID, saturday, workdays=_weekdays
    )
    assert overview["is_workday"] is False
    assert overview["leader_position"] is None
    # The next row with a leader is inside the generated 15-day window.
    assert overview["next_daily_date"] == "2026-08-10"
    assert overview["next_daily_leader_position"] is not None


@pytest.mark.asyncio
async def test_settings_validation_and_persistence(storage):
    result = await set_daily_time(storage, CHAT_ID, "25:99")
    assert result.ok is False
    assert "Неверный формат" in result.message

    result = await set_daily_time(storage, CHAT_ID, "09:15")
    assert result.ok is True
    chat = await storage.get_chat(CHAT_ID)
    assert chat.daily_time == "09:15"

    result = await update_settings(
        storage,
        CHAT_ID,
        cost_reminder_enabled=True,
        cost_reminder_time="18:45",
    )
    assert result.ok is True
    chat = await storage.get_chat(CHAT_ID)
    assert chat.cost_reminder_enabled is True
    assert chat.cost_reminder_time == "18:45"


@pytest.mark.asyncio
async def test_roster_actions_refresh_positions(storage):
    await _seed(storage)
    today = datetime.date(2026, 8, 3)

    result = await add_member(storage, CHAT_ID, "Dave @dave", None, today)
    assert result.ok is True
    members = await storage.get_members(CHAT_ID)
    assert members[-1].username == "Dave @dave"
    assert members[-1].position == 3

    result = await remove_member(storage, CHAT_ID, 1, today)
    assert result.ok is True
    members = await storage.get_members(CHAT_ID)
    assert [m.position for m in members] == [0, 1, 2]
    assert [m.username for m in members] == ["@a", "@c", "Dave @dave"]

    result = await move_member(storage, CHAT_ID, 1, "up", today)
    assert result.ok is True and result.moved is True
    members = await storage.get_members(CHAT_ID)
    assert [m.username for m in members] == ["@c", "@a", "Dave @dave"]
