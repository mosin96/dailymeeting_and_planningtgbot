"""Scheduler tests: decision functions + loop integration with fake time.

Model under test: a precomputed 14-day leader schedule (daily_schedule
table). The loop seeds/extends the window covering today..today+14, trims
rows older than 14 days, a leader tag 15 minutes before the daily, and a
"daily starts now" message at the daily time.
"""
import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

from ppbot.daily import DailyChat, DailyMember
from ppbot.daily_storage import DailyRegistry
from ppbot.scheduler import (
    should_send_reminder,
    should_send_start,
)


def chat(next_index=0, last_reminder_date=None, last_start_date=None, last_catchup_date=None, last_advance_date=None, daily_time="10:00"):
    return DailyChat(
        chat_id=1,
        daily_time=daily_time,
        next_index=next_index,
        last_reminder_date=last_reminder_date,
        last_start_date=last_start_date,
        last_catchup_date=last_catchup_date,
        last_advance_date=last_advance_date,
    )


MONDAY = datetime.date(2026, 8, 3)  # Monday


class TestShouldSendReminder:
    def test_workday_before_window_false(self):
        assert should_send_reminder(chat(), datetime.datetime(2026, 8, 3, 9, 44), MONDAY, True) is False

    def test_workday_in_window_true(self):
        assert should_send_reminder(chat(), datetime.datetime(2026, 8, 3, 9, 45), MONDAY, True) is True

    def test_workday_at_daily_time_false(self):
        # at the daily itself the start message takes over
        assert should_send_reminder(chat(), datetime.datetime(2026, 8, 3, 10, 0), MONDAY, True) is False

    def test_weekend_false(self):
        assert should_send_reminder(chat(), datetime.datetime(2026, 8, 3, 9, 45), MONDAY, False) is False

    def test_already_sent_today_false(self):
        c = chat(last_reminder_date="2026-08-03")
        assert should_send_reminder(c, datetime.datetime(2026, 8, 3, 9, 45), MONDAY, True) is False

    def test_sent_yesterday_true(self):
        c = chat(last_reminder_date="2026-08-02")
        assert should_send_reminder(c, datetime.datetime(2026, 8, 3, 9, 45), MONDAY, True) is True


class TestShouldSendStart:
    def test_before_daily_false(self):
        assert should_send_start(chat(), datetime.datetime(2026, 8, 3, 9, 59), MONDAY, True) is False

    def test_at_daily_true(self):
        assert should_send_start(chat(), datetime.datetime(2026, 8, 3, 10, 0), MONDAY, True) is True

    def test_within_grace_true(self):
        assert should_send_start(chat(), datetime.datetime(2026, 8, 3, 10, 30), MONDAY, True) is True

    def test_past_grace_false(self):
        assert should_send_start(chat(), datetime.datetime(2026, 8, 3, 11, 1), MONDAY, True) is False

    def test_weekend_false(self):
        assert should_send_start(chat(), datetime.datetime(2026, 8, 3, 10, 0), MONDAY, False) is False

    def test_already_started_false(self):
        c = chat(last_start_date="2026-08-03")
        assert should_send_start(c, datetime.datetime(2026, 8, 3, 10, 0), MONDAY, True) is False


class FakeCalendar:
    def __init__(self, predicate):
        self._predicate = predicate

    async def is_workday(self, date):
        return self._predicate(date)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return None


class FakeWorkdays:
    def __init__(self, value=True):
        self.value = value

    async def is_workday(self, date):
        return self.value


class FakeClock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


@pytest.fixture
async def storage(tmp_path):
    db = tmp_path / "daily.db"
    r = DailyRegistry()
    await r.init_db(str(db))
    yield r
    await r.close()


async def seed_chat(storage, chat_id=1, members=3, next_index=0, daily_time="10:00", last_reminder_date=None, last_start_date=None, last_catchup_date=None, last_advance_date=None):
    await storage.upsert_chat(DailyChat(
        chat_id=chat_id, daily_time=daily_time, next_index=next_index,
        last_reminder_date=last_reminder_date, last_start_date=last_start_date,
        last_catchup_date=last_catchup_date, last_advance_date=last_advance_date,
    ))
    for i in range(members):
        await storage.add_member(
            DailyMember(chat_id=chat_id, position=i, first_name=f"U{i}", user_id=100 + i, username=f"u{i}")
        )


async def run_one_iteration(reminder_loop, bot, storage, wd, clock):
    loop_task = asyncio.create_task(
        reminder_loop(bot, storage, wd, "UTC", interval=30, now=clock)
    )
    await asyncio.sleep(0.2)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_loop_tags_leader_15min_before(storage):
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)  # Monday 09:45, daily 10:00
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert "Сегодня ведущий - @u0" in bot.sent[0]["text"]
    assert bot.sent[0]["reply_markup"] is not None
    chat = await storage.get_chat(1)
    assert chat.last_reminder_date == "2026-08-03"
    assert chat.next_index == 0  # the reminder does NOT advance the rotation anymore
    assert chat.last_start_date is None


@pytest.mark.asyncio
async def test_loop_sends_start_message_at_daily_time(storage):
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 10, 30)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert "Дейлик начинается, всех ждем!" in bot.sent[0]["text"]
    chat = await storage.get_chat(1)
    assert chat.last_start_date == "2026-08-03"
    assert chat.next_index == 0


@pytest.mark.asyncio
async def test_loop_seeds_schedule_window(storage):
    """First loop pass seeds the daily_schedule covering today..today+14."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    schedule = await storage.get_schedule(1)
    today = datetime.date(2026, 8, 3)
    assert len(schedule) == 15  # today + 14 days
    for i in range(15):
        assert str(today + datetime.timedelta(days=i)) in schedule
    assert schedule["2026-08-03"] == 0
    assert schedule["2026-08-04"] == 1
    chat = await storage.get_chat(1)
    assert chat.next_index == 0


@pytest.mark.asyncio
async def test_loop_extends_and_trims_schedule(storage):
    """Next-day pass appends the new 14th-day leader; rows older than 14 days are trimmed."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    next_day = datetime.datetime(2026, 8, 4, 9, 45)
    bot2 = FakeBot()
    await run_one_iteration(reminder_loop, bot2, storage, FakeWorkdays(True), FakeClock([next_day, next_day + datetime.timedelta(seconds=35)]))

    schedule = await storage.get_schedule(1)
    assert "2026-08-18" in schedule
    assert "2026-08-03" in schedule

    await storage.extend_schedule(1, [("2026-07-01", 0)])
    bot3 = FakeBot()
    await run_one_iteration(reminder_loop, bot3, storage, FakeWorkdays(True), FakeClock([next_day, next_day + datetime.timedelta(seconds=35)]))
    schedule = await storage.get_schedule(1)
    assert "2026-07-01" not in schedule


@pytest.mark.asyncio
async def test_loop_skips_weekend(storage):
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(False), FakeClock([now]))

    assert bot.sent == []
    chat = await storage.get_chat(1)
    assert chat.last_reminder_date is None
    assert chat.last_start_date is None


@pytest.mark.asyncio
async def test_loop_all_skipped_sends_cancelled_message(storage):
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, members=1)
    await storage.update_member_skip(1, 100, "2026-08-03")
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)  # inside the reminder window
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert "отменён" in bot.sent[0]["text"]
    chat = await storage.get_chat(1)
    assert chat.last_reminder_date == "2026-08-03"
    assert chat.next_index == 0
    schedule = await storage.get_schedule(1)
    assert schedule["2026-08-03"] is None


@pytest.mark.asyncio
async def test_loop_rebuilds_schedule_from_next_index(storage):
    """Stale/missing schedule (e.g. after downtime) is rebuilt from next_index,
    so the seeded leader matches the rotation pointer."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, next_index=2)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert "Сегодня ведущий - @u2" in bot.sent[0]["text"]
    chat = await storage.get_chat(1)
    assert chat.next_index == 2


@pytest.mark.asyncio
async def test_loop_mentions_vacationers_without_at(storage):
    """Vacation notice appended to the reminder; vacationer NOT tagged with @."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, members=3)
    await storage.update_member_vacation(1, 1, "2026-08-05")  # U1 on vacation through Aug 5
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "Сегодня ведущий - @u0" in text  # leader tag unchanged (U0, vacationer skipped)
    assert "В отпуске: U1 (до 05.08.2026)" in text
    assert "@u1" not in text  # vacationer mentioned by plain name only


@pytest.mark.asyncio
async def test_loop_no_vacation_notice_when_none(storage):
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, members=3)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == "Сегодня ведущий - @u0"  # exact, no suffix


@pytest.mark.asyncio
async def test_loop_leader_skips_vacationer_in_tag(storage):
    """U0 on vacation -> the tag names U1, not U0."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, members=3)
    await storage.update_member_vacation(1, 0, "2026-08-05")
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45)
    await run_one_iteration(reminder_loop, bot, storage, FakeWorkdays(True), FakeClock([now, now + datetime.timedelta(seconds=35)]))

    assert len(bot.sent) == 1
    assert "Сегодня ведущий - @u1" in bot.sent[0]["text"]
    assert "@u0" not in bot.sent[0]["text"]


@pytest.mark.asyncio
async def test_loop_tags_leader_aware_clock(storage):
    """Regression: production clock is tz-aware; the loop must still fire the
    reminder (previously the aware-vs-naive comparison raised TypeError that
    the broad except swallowed, so nothing was sent)."""
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage)
    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45, tzinfo=ZoneInfo("UTC"))
    await run_one_iteration(
        reminder_loop, bot, storage, FakeWorkdays(True),
        FakeClock([now, now + datetime.timedelta(seconds=35)]),
    )

    assert len(bot.sent) == 1
    assert "Сегодня ведущий - @u0" in bot.sent[0]["text"]
    chat = await storage.get_chat(1)
    assert chat.last_reminder_date == "2026-08-03"


@pytest.mark.asyncio
async def test_manual_setleader_does_not_cancel_scheduler(storage):
    """A manual leader override (writes next_index) must NOT suppress the
    scheduled reminder and start messages later the same day."""
    from ppbot.daily import set_leader
    from ppbot.scheduler import reminder_loop

    await seed_chat(storage, next_index=0)
    members = await storage.get_members(1)
    new_next, err = set_leader(members, 0, 1, "2026-08-03")
    assert err is None
    chat = await storage.get_chat(1)
    chat.next_index = new_next
    await storage.upsert_chat(chat)

    bot = FakeBot()
    now = datetime.datetime(2026, 8, 3, 9, 45, tzinfo=ZoneInfo("UTC"))
    await run_one_iteration(
        reminder_loop, bot, storage, FakeWorkdays(True),
        FakeClock([now, now + datetime.timedelta(seconds=35)]),
    )
    assert len(bot.sent) == 1
    assert "Сегодня ведущий - @u1" in bot.sent[0]["text"]

    bot2 = FakeBot()
    now2 = datetime.datetime(2026, 8, 3, 10, 30, tzinfo=ZoneInfo("UTC"))
    await run_one_iteration(
        reminder_loop, bot2, storage, FakeWorkdays(True),
        FakeClock([now2, now2 + datetime.timedelta(seconds=35)]),
    )
    assert len(bot2.sent) == 1
    assert "Дейлик начинается, всех ждем!" in bot2.sent[0]["text"]
    chat = await storage.get_chat(1)
    assert chat.last_start_date == "2026-08-03"
