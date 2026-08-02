"""Offline smoke tests for poker handlers (no Telegram network)."""
import json

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from ppbot.game import GameRegistry


def make_message(text, message_id=100, user_id=10, username="alice", first_name="Alice"):
    return Message(
        message_id=message_id,
        date=0,
        chat=Chat(id=-1001, type="group"),
        from_user=User(id=user_id, is_bot=False, username=username, first_name=first_name),
        text=text,
    )


def make_callback(message, data, user_id=11, username="bob"):
    return CallbackQuery(
        id=f"cb-{data}",
        from_user=User(id=user_id, is_bot=False, username=username, first_name="Bob"),
        chat_instance="test-instance",
        message=message,
        data=data,
    )


@pytest.fixture
async def storage(tmp_path):
    db = tmp_path / "test.db"
    registry = GameRegistry()
    await registry.init_db(str(db))
    yield registry
    await registry._db.close()


async def feed(dp, bot, storage, obj):
    await dp.feed_update(bot, obj, storage=storage)


@pytest.mark.asyncio
async def test_poker_command_smoke(dp, bot, session, storage):
    msg = make_message("/poker Задача: сделать фичу")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))

    calls = session.calls
    send = [p for m, p in calls if m == "sendMessage"]
    assert len(send) == 1, f"expected 1 sendMessage, got {[m for m, _ in calls]}"
    assert "Голосование для задачи" in send[0]["text"]

    markup = json.loads(send[0]["reply_markup"]) if isinstance(send[0]["reply_markup"], str) else send[0]["reply_markup"]
    rows = markup["inline_keyboard"]
    first_row_texts = [b["text"] for b in rows[0]]
    assert len(first_row_texts) == 5  # HALF_POINTS
    assert "Перезапустить" in [b["text"] for b in rows[2]]
    assert "Открыть карты" in [b["text"] for b in rows[3]]

    # game persisted
    game = await storage.get_game(-1001, "100")
    assert game is not None
    assert game.text == "Задача: сделать фичу"
    assert game.reply_message_id == 1  # FakeSession returns message_id=1


@pytest.mark.asyncio
async def test_poker_multiline(dp, bot, session, storage):
    msg = make_message("/poker line1\nline2\nline3")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))
    send = [p for m, p in session.calls if m == "sendMessage"]
    assert "line1\nline2\nline3" in send[0]["text"]


@pytest.mark.asyncio
async def test_voteban_smoke(dp, bot, session, storage):
    msg = make_message("/voteban @someone")
    await feed(dp, bot, storage, Update(update_id=1, message=msg))
    polls = [p for m, p in session.calls if m == "sendPoll"]
    assert len(polls) == 1
    assert polls[0]["options"] == ["Да", "Нет"]
    assert polls[0]["is_anonymous"] is True


@pytest.mark.asyncio
async def test_vote_click_adds_vote(dp, bot, session, storage):
    await feed(dp, bot, storage, Update(update_id=1, message=make_message("/poker задача")))
    session.calls.clear()

    game = await storage.get_game(-1001, "100")
    markup = json.loads(game.get_send_kwargs()["reply_markup"])
    cb_data = markup["inline_keyboard"][0][0]["callback_data"]  # first point button

    await feed(dp, bot, storage, Update(update_id=2, callback_query=make_callback(make_message("x"), cb_data)))

    game = await storage.get_game(-1001, "100")
    assert "@bob (Bob)" in game.votes  # vote comes from callback user (bob, id=11)
    assert "Нельзя менять ответ" not in [p.get("text", "") for p in (p for _, p in session.calls)]

    edits = [p for m, p in session.calls if m == "editMessageText"]
    assert len(edits) == 1
    answers = [p for m, p in session.calls if m == "answerCallbackQuery"]
    assert answers[0]["text"] == "Ответ 1 принят"


@pytest.mark.asyncio
async def test_reveal_by_non_initiator_rejected(dp, bot, session, storage):
    await feed(dp, bot, storage, Update(update_id=1, message=make_message("/poker задача")))
    session.calls.clear()

    cb = make_callback(make_message("x"), "reveal-new-click-100", user_id=99, username="intruder")
    await feed(dp, bot, storage, Update(update_id=2, callback_query=cb))

    answers = [p for m, p in session.calls if m == "answerCallbackQuery"]
    assert "доступно только инициатору" in answers[0]["text"]


@pytest.mark.asyncio
async def test_reveal_by_initiator_sends_results(dp, bot, session, storage):
    await feed(dp, bot, storage, Update(update_id=1, message=make_message("/poker задача")))
    session.calls.clear()

    cb = make_callback(make_message("x"), "reveal-new-click-100", user_id=10, username="alice")
    await feed(dp, bot, storage, Update(update_id=2, callback_query=cb))

    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "Результаты для задачи" in sends[0]["text"]


@pytest.mark.asyncio
async def test_restart_clears_votes(dp, bot, session, storage):
    await feed(dp, bot, storage, Update(update_id=1, message=make_message("/poker задача")))
    game = await storage.get_game(-1001, "100")
    markup = json.loads(game.get_send_kwargs()["reply_markup"])
    cb_data = markup["inline_keyboard"][0][0]["callback_data"]
    await feed(dp, bot, storage, Update(update_id=2, callback_query=make_callback(make_message("x"), cb_data)))
    session.calls.clear()

    cb = make_callback(make_message("x"), "restart-new-click-100", user_id=10, username="alice")
    await feed(dp, bot, storage, Update(update_id=3, callback_query=cb))

    game = await storage.get_game(-1001, "100")
    assert not game.votes
    assert not game.revealed
