import asyncio
import logging
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import BotCommand, Message

from ppbot.utils import init_logging

TOKEN = os.environ["PP_BOT_TOKEN"]
DB_PATH = os.environ.get("PP_BOT_DB_PATH", os.path.expanduser("~/.tg_pp_bot.db"))
TZ_NAME = os.environ.get("PP_BOT_TZ", "Europe/Moscow")

try:
    TZ = ZoneInfo(TZ_NAME)
except ZoneInfoNotFoundError:
    logging.warning("Unknown timezone %r, falling back to Europe/Moscow", TZ_NAME)
    TZ = ZoneInfo("Europe/Moscow")

GREETING = """🤖 Дейлик-бот

/poker задача — начать голосование (многострочное описание поддерживается)
/voteban @имя — голосование за бан
/daily — главное меню дейликов
/team — состав команды
/time — время дейлика (ЧЧ:ММ)
/who — кто сегодня ведёт
/substitute — подменить ведущего
/setleader @ник — ведущий сегодня
/vacation @ник ДД.ММ.ГГГГ — отпуск («снять» — вернуть)
/costremind — напоминания о списании трудозатрат
/vacplan — запланированные отпуска
/reset — сброс истории бота для чата (двойное подтверждение)
/help — эта справка
"""

COMMANDS = [
    ("poker", "Начать голосование по задаче"),
    ("voteban", "Голосование за бан пользователя"),
    ("daily", "Главное меню дейликов"),
    ("team", "Состав команды дейлика"),
    ("time", "Время дейлика (ЧЧ:ММ)"),
    ("who", "Кто сегодня ведёт дейлик"),
    ("substitute", "Подменить ведущего дейлика"),
    ("setleader", "Выбрать ведущего сегодня"),
    ("vacation", "Отпуск участника (до ДД.ММ.ГГГГ)"),
    ("costremind", "Напоминания о трудозатратах"),
    ("vacplan", "Запланированные отпуска"),
    ("reset", "Сбросить историю бота для чата"),
    ("help", "Справка"),
]

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
init_logging()

from ppbot.poker_handlers import router as poker_router  # noqa: E402
from ppbot.daily_handlers import create_router as create_daily_router  # noqa: E402

dp.include_router(poker_router)
dp.include_router(create_daily_router())


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(GREETING)


def main():
    from ppbot.game import GameRegistry
    from ppbot.daily_storage import DailyRegistry
    from ppbot.scheduler import reminder_loop
    from ppbot.workdays import WorkdayClient

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    storage = GameRegistry()
    daily = DailyRegistry()

    async def _start():
        import aiohttp

        from ppbot.scheduler import migrate_schedule_model, reminder_loop
        from ppbot.workdays import WorkdayClient

        http_session = aiohttp.ClientSession()
        workdays = WorkdayClient(session=http_session)
        await storage.init_db(DB_PATH)
        await daily.init_db(DB_PATH)
        await migrate_schedule_model(daily, TZ, workdays)
        await bot.set_my_commands(
            [BotCommand(command=cmd, description=desc) for cmd, desc in COMMANDS]
        )
        try:
            await asyncio.gather(
                dp.start_polling(bot, storage=storage, daily=daily, tz=TZ, workdays=workdays),
                reminder_loop(bot, daily, workdays, TZ),
            )
        finally:
            await http_session.close()

    try:
        loop.run_until_complete(_start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
