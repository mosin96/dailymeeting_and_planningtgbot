import os

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tests.helpers import FakeSession

os.environ.setdefault("PP_BOT_TOKEN", "12345:test-token")


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def bot(session):
    return Bot(
        token="12345:test-token",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


@pytest.fixture
def dp():
    from ppbot.poker_handlers import create_router

    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    return dispatcher
