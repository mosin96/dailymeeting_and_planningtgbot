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

logger = logging.getLogger(__name__)

TOKEN = os.environ["PP_BOT_TOKEN"]
DB_PATH = os.environ.get("PP_BOT_DB_PATH", os.path.expanduser("~/.tg_pp_bot.db"))
TZ_NAME = os.environ.get("PP_BOT_TZ", "Europe/Moscow")
WEBAPP_PORT = int(os.environ.get("PP_WEBAPP_PORT", "8443"))
WEBAPP_CERT = os.environ.get("PP_WEBAPP_CERT", "")
WEBAPP_KEY = os.environ.get("PP_WEBAPP_KEY", "")
WEBAPP_PUBLIC_URL = os.environ.get("PP_WEBAPP_PUBLIC_URL", "")

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
📅 Отпуска — через мини-апп (кнопка «Отпуска» в меню /daily)
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
        from ppbot.webapp import start_webapp

        http_session = aiohttp.ClientSession()
        workdays = WorkdayClient(session=http_session)
        await storage.init_db(DB_PATH)
        await daily.init_db(DB_PATH)
        await migrate_schedule_model(daily, TZ, workdays)
        await bot.set_my_commands(
            [BotCommand(command=cmd, description=desc) for cmd, desc in COMMANDS]
        )
        # The bot username is needed to build Mini App deep links
        # (t.me/<bot>?startapp=...): a plain URL button would open the page
        # without initData and the calendar API would reject it.
        from ppbot.daily_ui import configure_webapp_bot

        try:
            me = await bot.get_me()
            configure_webapp_bot(me.username)
            logger.info("mini-app deep links target @%s", me.username)
        except Exception:
            logger.warning("could not resolve bot username for mini-app links", exc_info=True)
        # Mini-app HTTP(S) server: runs in the same loop as polling and the
        # reminder cycle. Started as a task so a port/cert problem degrades
        # to a warning instead of killing the bot.
        webapp_task = asyncio.ensure_future(
            start_webapp(
                daily,
                TZ,
                TOKEN,
                workdays=workdays,
                port=WEBAPP_PORT,
                cert_file=WEBAPP_CERT,
                key_file=WEBAPP_KEY,
            )
        )
        try:
            await asyncio.gather(
                dp.start_polling(bot, storage=storage, daily=daily, tz=TZ, workdays=workdays),
                reminder_loop(bot, daily, workdays, TZ),
            )
        finally:
            webapp_task.cancel()
            await http_session.close()

    try:
        loop.run_until_complete(_start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
