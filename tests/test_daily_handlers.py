"""Daily handler smoke tests (buttons: substitute, skip)."""
import datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from ppbot.daily import DailyChat, DailyMember
from ppbot.daily_handlers import create_router
from ppbot.daily_storage import DailyRegistry

TZ = ZoneInfo("UTC")


def make_message(text=None, message_id=100, user_id=10, username="alice", first_name="Alice", chat_id=-1001, reply_to=None):
    return Message(
        message_id=message_id,
        date=0,
        chat=Chat(id=chat_id, type="group"),
        from_user=User(id=user_id, is_bot=False, username=username, first_name=first_name),
        text=text,
        reply_to_message=reply_to,
    )


def make_callback(message, data, user_id=11, username="bob", first_name="Bob"):
    return CallbackQuery(
        id=f"cb-{data}",
        from_user=User(id=user_id, is_bot=False, username=username, first_name=first_name),
        chat_instance="test-instance",
        message=message,
        data=data,
    )


def today_str():
    return datetime.datetime.now(TZ).strftime("%Y-%m-%d")


@pytest.fixture
async def storage(tmp_path):
    db = tmp_path / "daily.db"
    r = DailyRegistry()
    await r.init_db(str(db))
    yield r
    await r.close()


@pytest.fixture
def dp():
    from aiogram import Dispatcher

    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    return dispatcher


async def seed(storage, chat_id=-1001, last_reminder_date=None, next_index=0):
    chat = DailyChat(chat_id=chat_id, next_index=next_index, last_reminder_date=last_reminder_date)
    await storage.upsert_chat(chat)
    for i, name in enumerate(["A", "B", "C"]):
        await storage.add_member(
            DailyMember(chat_id=chat_id, position=i, first_name=name, user_id=1 + i, username=name.lower())
        )
    return chat


async def feed(dp, bot, storage, obj):
    await dp.feed_update(bot, obj, daily=storage, tz=TZ)


@pytest.mark.asyncio
async def test_substitute_button_today_b_tomorrow_a(dp, bot, session, storage):
    await seed(storage, last_reminder_date=today_str(), next_index=0)
    msg = make_message("original")
    cb = make_callback(msg, "daily:sub:0", user_id=2)  # anyone can press
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @b, завтра @a" in edits[0]["text"]

    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["B", "A", "C"]


@pytest.mark.asyncio
async def test_substitute_button_with_stale_next_index(dp, bot, session, storage):
    """Robustness: even if next_index is stale (1 instead of the leader's 0),
    the button payload (leader's position) still swaps A<->B, and next_index
    stays put so A leads tomorrow."""
    await seed(storage, last_reminder_date=today_str(), next_index=1)
    msg = make_message("original")
    cb = make_callback(msg, "daily:sub:0", user_id=2)  # anyone can press
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @b, завтра @a" in edits[0]["text"]

    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["B", "A", "C"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1  # tomorrow A (position 1)


@pytest.mark.asyncio
async def test_substitute_command_with_reminder_sent(dp, bot, session, storage):
    """Regression: /substitute substitutes today's leader (A, at next_index),
    even after the morning tag was already sent today."""
    await seed(storage, last_reminder_date=today_str(), next_index=0)
    msg = make_message("/substitute")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @b, завтра @a" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["B", "A", "C"]


@pytest.mark.asyncio
async def test_skip_button_repicks_leader(dp, bot, session, storage):
    await seed(storage, last_reminder_date=today_str(), next_index=0)
    msg = make_message("original")
    cb = make_callback(msg, "daily:skip:0", user_id=2)
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Пропуск принят. Сегодня ведёт @b" in edits[0]["text"]

    members = await storage.get_members(-1001)
    assert members[0].skip_date == today_str()
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1  # points at new leader B (position 1)


@pytest.mark.asyncio
async def test_substitute_button_works_without_reminder_fired(dp, bot, session, storage):
    """Regression (live bug): with last_reminder_date=None (reminder not fired
    yet today, e.g. weekend), pressing substitute/skip must NOT be rejected
    with 'напоминание устарело' — buttons act on the current queue state."""
    await seed(storage, last_reminder_date=None, next_index=0)
    msg = make_message("original")
    cb = make_callback(msg, "daily:sub:0")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @b, завтра @a" in edits[0]["text"]

    answers = [p for m, p in session.calls if m == "answerCallbackQuery"]
    assert not any(("устарело" in (a.get("text") or "")) for a in answers)


@pytest.mark.asyncio
async def test_substitute_single_member_rejected(dp, bot, session, storage):
    await storage.upsert_chat(DailyChat(chat_id=-1001, last_reminder_date=today_str()))
    await storage.add_member(DailyMember(chat_id=-1001, position=0, first_name="Solo", user_id=1))
    msg = make_message("original")
    cb = make_callback(msg, "daily:sub:0")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Некого подменять" in edits[0]["text"]


@pytest.mark.asyncio
async def test_who_command_shows_leader_with_buttons(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("/who")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @a" in sends[0]["text"]
    markup = sends[0]["reply_markup"]
    assert markup is not None


@pytest.mark.asyncio
async def test_who_shows_tomorrow_leader(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("/who")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @a" in sends[0]["text"]
    assert "Завтра ведёт @b" in sends[0]["text"]


@pytest.mark.asyncio
async def test_who_shows_vacationers(dp, bot, session, storage):
    """who output syncs with the scheduler reminder: vacationers listed by
    plain name (no @), and tomorrow's leader skips the vacationer."""
    await seed(storage, next_index=0)
    await storage.update_member_vacation(
        -1001, 1, (datetime.datetime.now(TZ) + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
    )
    msg = make_message("/who")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    text = sends[0]["text"]
    assert "Сегодня ведёт @a" in text
    assert "В отпуске: B" in text
    assert "@b" not in text
    assert "Завтра ведёт @c" in text


@pytest.mark.asyncio
async def test_who_has_main_menu_button(dp, bot, session, storage):
    """who view gets a jump-to-menu button; the scheduled reminder does not."""
    from ppbot.daily import next_leader
    from ppbot.daily_ui import build_reminder_markup

    await seed(storage, next_index=0)
    msg = make_message("/who")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    buttons = [
        btn["callback_data"]
        for row in sends[0]["reply_markup"]["inline_keyboard"]
        for btn in row
    ]
    assert "daily:menu" in buttons

    members = await storage.get_members(-1001)
    leader = next_leader(members, 0, today_str())
    reminder_markup = build_reminder_markup(leader)
    reminder_buttons = [
        btn.callback_data
        for row in reminder_markup.inline_keyboard
        for btn in row
    ]
    assert "daily:menu" not in reminder_buttons


@pytest.mark.asyncio
async def test_who_follows_schedule_not_stale_next_index(dp, bot, session, storage):
    """Regression: /who must read today's leader from the persisted schedule
    (the reminder's source of truth), not from a stale chat.next_index that
    froze at migration day and is no longer advanced by the scheduler."""
    await seed(storage, next_index=0)
    today = datetime.date.today()
    rows = [
        ((today + datetime.timedelta(days=i)).isoformat(), 2)
        for i in range(15)
    ]
    await storage.set_schedule(-1001, rows)
    msg = make_message("/who")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @c" in sends[0]["text"]
    assert "@a" not in sends[0]["text"]


@pytest.mark.asyncio
async def test_substitute_command(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("/substitute")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @b, завтра @a" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["B", "A", "C"]


@pytest.mark.asyncio
async def test_daily_menu_smoke(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/daily")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Участников: 3" in sends[0]["text"]


@pytest.mark.asyncio
async def test_time_command_valid_and_invalid(dp, bot, session, storage):
    await seed(storage)
    # start FSM
    msg = make_message("/time")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))
    assert any(m == "sendMessage" for m, _ in session.calls)
    session.calls.clear()

    # invalid
    msg2 = make_message("25:99")
    await feed(dp, bot, storage, Update(update_id=2, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("Неверный формат" in s.get("text", "") for s in sends)
    session.calls.clear()

    # valid
    msg3 = make_message("09:30")
    await feed(dp, bot, storage, Update(update_id=3, message=msg3))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("Время дейлика: 09:30" in s.get("text", "") for s in sends)
    chat = await storage.get_chat(-1001)
    assert chat.daily_time == "09:30"


@pytest.mark.asyncio
async def test_team_add_by_reply_and_duplicate_rejected(dp, bot, session, storage):
    await seed(storage)
    session.calls.clear()

    cb = make_callback(make_message("x"), "daily:add", user_id=11)
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))
    session.calls.clear()

    replied = make_message("/start", message_id=200, user_id=99, username="dave", first_name="Dave")
    msg = make_message("reply-add", message_id=201, user_id=11, username="bob", first_name="Bob", reply_to=replied)
    await feed(dp, bot, storage, Update(update_id=2, message=msg))
    members = await storage.get_members(-1001)
    assert len(members) == 4
    assert members[3].first_name == "Dave"
    session.calls.clear()

    cb2 = make_callback(make_message("x"), "daily:add", user_id=11)
    await feed(dp, bot, storage, Update(update_id=3, callback_query=cb2))
    session.calls.clear()

    dup = make_message("reply-dup", message_id=202, user_id=11, username="bob", first_name="Bob", reply_to=replied)
    await feed(dp, bot, storage, Update(update_id=4, message=dup))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("Уже в команде" in s.get("text", "") for s in sends)
    members = await storage.get_members(-1001)
    assert len(members) == 4


@pytest.mark.asyncio
async def test_substitute_button_works_for_user_id_none_members(dp, bot, session, storage):
    """Regression (live bug): members added by @username have user_id=None.
    build_reminder_markup must still emit a digits payload (position), so the
    substitute/skip callbacks match and work."""
    chat = DailyChat(chat_id=-1001, last_reminder_date=today_str(), next_index=0)
    await storage.upsert_chat(chat)
    await storage.add_member(DailyMember(chat_id=-1001, position=0, first_name="testuser", username="testuser"))
    await storage.add_member(DailyMember(chat_id=-1001, position=1, first_name="Никита", username="Никита"))

    from ppbot.daily_ui import build_reminder_markup
    from ppbot.daily import next_leader

    members = await storage.get_members(-1001)
    leader = next_leader(members, 0, today_str())
    assert leader is not None
    markup = build_reminder_markup(leader)
    data = markup.inline_keyboard[0][0].callback_data
    assert data == "daily:sub:0"  # position, not empty payload

    msg = make_message("original")
    cb = make_callback(msg, "daily:sub:0", user_id=2)  # anyone can press
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @Никита, завтра @testuser" in edits[0]["text"]
    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["Никита", "testuser"]


@pytest.mark.asyncio
async def test_skip_button_works_for_user_id_none_members(dp, bot, session, storage):
    """Regression (live bug): skip button must work for user_id=None members."""
    chat = DailyChat(chat_id=-1001, last_reminder_date=today_str(), next_index=0)
    await storage.upsert_chat(chat)
    await storage.add_member(DailyMember(chat_id=-1001, position=0, first_name="testuser", username="testuser"))
    await storage.add_member(DailyMember(chat_id=-1001, position=1, first_name="Никита", username="Никита"))

    msg = make_message("original")
    cb = make_callback(msg, "daily:skip:0", user_id=2)
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Пропуск принят. Сегодня ведёт @Никита" in edits[0]["text"]
    members = await storage.get_members(-1001)
    assert members[0].skip_date == today_str()


@pytest.mark.asyncio
async def test_team_remove_reindexes(dp, bot, session, storage):
    await seed(storage, next_index=2)
    cb = make_callback(make_message("x"), "daily:remove:1")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["A", "C"]
    assert [m.position for m in members] == [0, 1]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1  # 2 > 1 -> decremented


# ---- T5: menu buttons and member picker ----

@pytest.mark.asyncio
async def test_menu_has_leader_and_vacation_buttons(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/daily")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    markup = sends[0]["reply_markup"]
    flat = [
        btn["callback_data"]
        for row in markup["inline_keyboard"]
        for btn in row
    ]
    assert "daily:lead" in flat
    assert "daily:vac" in flat


def test_build_member_picker_markup_carries_positions():
    from ppbot.daily_ui import PREFIX_LEAD, build_member_picker_markup

    from ppbot.daily import DailyMember

    members = [
        DailyMember(chat_id=-1001, position=0, first_name="A", user_id=1),
        DailyMember(chat_id=-1001, position=1, first_name="testuser", username="testuser"),
    ]
    markup = build_member_picker_markup(members, PREFIX_LEAD)
    flat = [
        (btn.text, btn.callback_data)
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert flat[0] == ("👑 A", "daily:lead:0")
    assert flat[1] == ("👑 @testuser", "daily:lead:1")
    assert flat[-1] == ("🔙 Назад", "daily:menu")


def test_build_member_picker_markup_empty_members_still_has_back():
    from ppbot.daily_ui import PREFIX_VAC, build_member_picker_markup

    markup = build_member_picker_markup([], PREFIX_VAC)
    flat = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert flat == ["daily:menu"]


# ---- T6: /setleader command and leader picker ----

@pytest.mark.asyncio
async def test_setleader_command_by_username(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("/setleader @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @b" in sends[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1  # points at B, no queue reorder
    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["A", "B", "C"]  # order untouched


@pytest.mark.asyncio
async def test_setleader_command_without_at_sign(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("/setleader b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @b" in sends[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1


@pytest.mark.asyncio
async def test_setleader_command_by_reply(dp, bot, session, storage):
    await seed(storage, next_index=0)
    replied = make_message("/who", message_id=200, user_id=2, username="bob", first_name="B")
    msg = make_message("/setleader", message_id=201, user_id=10, username="alice", first_name="A", reply_to=replied)
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Сегодня ведёт @b" in sends[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1


@pytest.mark.asyncio
async def test_setleader_unknown_member_rejected(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/setleader @nobody")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Не нашёл участника" in sends[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 0  # untouched


@pytest.mark.asyncio
async def test_setleader_vacationer_rejected(dp, bot, session, storage):
    await seed(storage, next_index=0)
    await storage.update_member_vacation(-1001, 1, "2099-01-01")
    msg = make_message("/setleader @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Недоступен сегодня" in sends[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 0  # untouched


@pytest.mark.asyncio
async def test_setleader_empty_team(dp, bot, session, storage):
    await storage.upsert_chat(DailyChat(chat_id=-1001))
    msg = make_message("/setleader @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Команда пуста" in sends[0]["text"]


@pytest.mark.asyncio
async def test_leader_picker_button_opens_picker(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:lead")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Кто сегодня ведёт?" in edits[0]["text"]
    markup = edits[0]["reply_markup"]
    flat = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "daily:lead:0" in flat
    assert "daily:lead:1" in flat


@pytest.mark.asyncio
async def test_leader_picker_selects_member(dp, bot, session, storage):
    await seed(storage, next_index=0)
    msg = make_message("original")
    cb = make_callback(msg, "daily:lead:1")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @b" in edits[0]["text"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 1
    members = await storage.get_members(-1001)
    assert [m.first_name for m in members] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_leader_picker_vacationer_rejected(dp, bot, session, storage):
    await seed(storage, next_index=0)
    await storage.update_member_vacation(-1001, 1, "2099-01-01")
    msg = make_message("original")
    cb = make_callback(msg, "daily:lead:1")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    answers = [p for m, p in session.calls if m == "answerCallbackQuery"]
    assert any("Недоступен сегодня" in (a.get("text") or "") for a in answers)
    assert not [p for m, p in session.calls if m == "editMessageText"]
    chat = await storage.get_chat(-1001)
    assert chat.next_index == 0


# ---- T6: /vacation command, FSM, and vacation picker ----

@pytest.mark.asyncio
async def test_vacation_command_sets_date(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @b 05.08.2026")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@b в отпуске до 05.08.2026" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until == "2026-08-05"


@pytest.mark.asyncio
async def test_vacation_command_iso_date(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @b 2026-08-05")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@b в отпуске до 05.08.2026" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until == "2026-08-05"


@pytest.mark.asyncio
async def test_vacation_command_by_reply(dp, bot, session, storage):
    await seed(storage)
    replied = make_message("/who", message_id=200, user_id=3, username="carol", first_name="C")
    msg = make_message("/vacation 05.08.2026", message_id=201, user_id=10, username="alice", first_name="A", reply_to=replied)
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@c в отпуске до 05.08.2026" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[2].vacation_until == "2026-08-05"


@pytest.mark.asyncio
async def test_vacation_command_clear(dp, bot, session, storage):
    await seed(storage)
    await storage.update_member_vacation(-1001, 1, "2026-08-05")
    msg = make_message("/vacation @b снять")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@b вернулся в ротацию" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until is None


@pytest.mark.asyncio
async def test_vacation_command_invalid_date_rejected(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @b 32.13.2026")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Неверный формат даты" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until is None


@pytest.mark.asyncio
async def test_vacation_command_unknown_member_rejected(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @nobody 05.08.2026")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Не нашёл участника" in sends[0]["text"]


@pytest.mark.asyncio
async def test_vacation_command_starts_fsm_without_date(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "До какой даты отпуск?" in sends[0]["text"]

    session.calls.clear()
    msg2 = make_message("05.08.2026")
    await feed(dp, bot, storage, Update(update_id=2, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@b в отпуске до 05.08.2026" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until == "2026-08-05"


@pytest.mark.asyncio
async def test_vacation_fsm_invalid_then_valid(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("/vacation @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))
    session.calls.clear()

    # invalid keeps FSM active
    msg2 = make_message("not-a-date")
    await feed(dp, bot, storage, Update(update_id=2, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("Неверный формат даты" in s.get("text", "") for s in sends)
    session.calls.clear()

    # still in FSM: next message is consumed as the date
    msg3 = make_message("06.08.2026")
    await feed(dp, bot, storage, Update(update_id=3, message=msg3))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("@b в отпуске до 06.08.2026" in s.get("text", "") for s in sends)
    members = await storage.get_members(-1001)
    assert members[1].vacation_until == "2026-08-06"


@pytest.mark.asyncio
async def test_vacation_fsm_clear(dp, bot, session, storage):
    await seed(storage)
    await storage.update_member_vacation(-1001, 1, "2026-08-05")
    msg = make_message("/vacation @b")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))
    session.calls.clear()

    msg2 = make_message("снять")
    await feed(dp, bot, storage, Update(update_id=2, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "@b вернулся в ротацию" in sends[0]["text"]
    members = await storage.get_members(-1001)
    assert members[1].vacation_until is None


@pytest.mark.asyncio
async def test_vacation_picker_opens_and_selects(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:vac")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Кто уходит в отпуск?" in edits[0]["text"]
    markup = edits[0]["reply_markup"]
    flat = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "daily:vac:1" in flat
    session.calls.clear()

    cb2 = make_callback(msg, "daily:vac:1")
    await feed(dp, bot, storage, Update(update_id=2, callback_query=cb2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "До какой даты отпуск?" in sends[0]["text"]

    session.calls.clear()
    msg2 = make_message("05.08.2026", user_id=11)  # same user as the picker callback (FSM key)
    await feed(dp, bot, storage, Update(update_id=3, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert any("@b в отпуске до 05.08.2026" in s.get("text", "") for s in sends)
    members = await storage.get_members(-1001)
    assert members[1].vacation_until == "2026-08-05"


@pytest.mark.asyncio
async def test_setleader_then_vacationed_member_skipped_in_rotation(dp, bot, session, storage):
    """End-to-end: after B goes on vacation, a subsequent manual leader
    override of B must be rejected, and rotation skips B."""
    await seed(storage, next_index=0)
    msg = make_message("/vacation @b 2099-01-01")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    session.calls.clear()
    msg2 = make_message("/setleader @b")
    await feed(dp, bot, storage, Update(update_id=2, message=msg2))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Недоступен сегодня" in sends[0]["text"]

    # rotation after A leads: next non-available is C
    from ppbot.daily import next_leader

    members = await storage.get_members(-1001)
    leader = next_leader(members, 1, today_str())
    assert leader.first_name == "C"


@pytest.mark.asyncio
async def test_menu_callback_edits_in_place(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:menu")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "📋 Дейлик" in edits[0]["text"]
    assert not [p for m, p in session.calls if m == "sendMessage"]


@pytest.mark.asyncio
async def test_team_callback_edits_in_place(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:team")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Состав команды" in edits[0]["text"]
    assert not [p for m, p in session.calls if m == "sendMessage"]

    # removal is a single dedicated button, not inline per-member crosses
    flat = [
        btn["callback_data"]
        for row in edits[0]["reply_markup"]["inline_keyboard"]
        for btn in row
    ]
    assert "daily:remove:0" not in flat
    assert "daily:remove:1" not in flat
    assert "daily:removelist" in flat


@pytest.mark.asyncio
async def test_remove_list_opens_with_vertical_crosses(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:removelist")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Удаление участников" in edits[0]["text"]
    kb = edits[0]["reply_markup"]["inline_keyboard"]
    # one member per row, each cross button carrying its position
    assert len(kb) == 4
    for btn in kb[0]:
        assert btn["text"].startswith("❌")
        assert btn["callback_data"] == "daily:remove:0"
    for btn in kb[1]:
        assert btn["callback_data"] == "daily:remove:1"
    for btn in kb[2]:
        assert btn["callback_data"] == "daily:remove:2"
    assert kb[3][0]["callback_data"] == "daily:team"


@pytest.mark.asyncio
async def test_remove_through_list_stays_in_remove_mode(dp, bot, session, storage):
    await seed(storage)
    cb = make_callback(make_message("x"), "daily:remove:1")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    # stays in the dedicated removal list, not back to the team view
    assert "Удаление участников" in edits[0]["text"]
    kb = edits[0]["reply_markup"]["inline_keyboard"]
    assert len(kb) == 3  # 2 remaining members + back button
    assert kb[0][0]["callback_data"] == "daily:remove:0"
    assert kb[1][0]["callback_data"] == "daily:remove:1"
    assert kb[2][0]["callback_data"] == "daily:team"


@pytest.mark.asyncio
async def test_who_callback_edits_in_place(dp, bot, session, storage):
    await seed(storage)
    msg = make_message("original")
    cb = make_callback(msg, "daily:who")
    await feed(dp, bot, storage, Update(update_id=1, callback_query=cb))

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    assert "Сегодня ведёт @a" in edits[0]["text"]
    assert not [p for m, p in session.calls if m == "sendMessage"]


# ---- reset history ----

@pytest.fixture
async def game_storage(tmp_path):
    from ppbot.game import GameRegistry

    db = tmp_path / "game.db"
    r = GameRegistry()
    await r.init_db(str(db))
    yield r
    await r._db.close()


@pytest.mark.asyncio
async def test_reset_requires_double_confirmation(dp, bot, session, storage, game_storage):
    await seed(storage)
    await dp.feed_update(
        bot,
        Update(update_id=1, message=make_message("/reset")),
        daily=storage,
        storage=game_storage,
        tz=TZ,
    )

    answers = [p for m, p in session.calls if m == "sendMessage"]
    assert any("сбросит историю бота" in (a.get("text") or "") for a in answers)
    assert not any("сброшена" in (a.get("text") or "") for a in answers)
    members = await storage.get_members(-1001)
    assert len(members) == 3  # nothing deleted yet


@pytest.mark.asyncio
async def test_reset_executes_on_second_command(dp, bot, session, storage, game_storage):
    await seed(storage)
    from ppbot.game import Game as GameModel

    await game_storage.save_game(
        GameModel(chat_id=-1001, vote_id="100", initiator={"id": 1, "first_name": "A"}, text="task")
    )

    for update_id in (1, 2):
        await dp.feed_update(
            bot,
            Update(update_id=update_id, message=make_message("/reset", message_id=100 + update_id)),
            daily=storage,
            storage=game_storage,
            tz=TZ,
        )

    answers = [p for m, p in session.calls if m == "sendMessage"]
    assert any("сброшена" in (a.get("text") or "") for a in answers)
    assert await storage.get_members(-1001) == []
    assert await storage.get_chat(-1001) is None
    assert await game_storage.get_game(-1001, "100") is None
