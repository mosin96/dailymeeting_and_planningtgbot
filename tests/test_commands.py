"""Command menu (set_my_commands) and GREETING smoke tests."""
import pytest
from aiogram.types import Chat, Message, Update, User

from ppbot import bot as bot_module


def make_message(text="/start", message_id=100, user_id=10):
    return Message(
        message_id=message_id,
        date=0,
        chat=Chat(id=-1001, type="group"),
        from_user=User(id=user_id, is_bot=False, username="alice", first_name="Alice"),
        text=text,
    )


def test_greeting_contains_all_commands():
    expected = [cmd for cmd, _ in bot_module.COMMANDS]
    for cmd in expected:
        assert "/{}".format(cmd) in bot_module.GREETING


def test_commands_are_11_unique():
    cmds = [cmd for cmd, _ in bot_module.COMMANDS]
    assert len(cmds) == 11
    assert len(set(cmds)) == 11


@pytest.mark.asyncio
async def test_start_handler_answers_greeting(bot, session):
    msg = make_message("/start")
    await bot_module.dp.feed_update(bot, Update(update_id=1, message=msg))
    sends = [p for m, p in session.calls if m == "sendMessage"]
    assert len(sends) == 1
    assert "/poker" in sends[0]["text"]
    assert "/team" in sends[0]["text"]


@pytest.mark.asyncio
async def test_set_my_commands_smoke(bot, session):
    from aiogram.types import BotCommand

    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in bot_module.COMMANDS]
    )
    calls = [m for m, p in session.calls if m == "setMyCommands"]
    assert len(calls) == 1
