"""Poker handlers: /poker, /voteban, vote/reveal/restart callbacks (aiogram port)."""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest

from ppbot.game import AVAILABLE_POINTS, Game, GameRegistry

logger = logging.getLogger(__name__)

router = Router(name="poker")

VOTEBAN_USAGE = "Использование: /voteban @username или имя"


def _game_markup(game: Game) -> InlineKeyboardMarkup:
    """Convert Game.get_markup() JSON dict into aiogram InlineKeyboardMarkup."""
    return InlineKeyboardMarkup.model_validate(game.get_markup())


def _initiator_from_message(message: Message) -> dict:
    if message.from_user is None:
        return {"id": 0, "first_name": "?"}
    user = message.from_user
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name or user.username or str(user.id),
    }


def _initiator_from_callback(callback: CallbackQuery) -> dict:
    user = callback.from_user
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name or user.username or str(user.id),
    }


@router.message(Command("poker"))
async def poker_start(message: Message, command: CommandObject, storage: GameRegistry):
    text = command.args
    if not text:
        return await message.reply(
            "Использование: /poker задача (или многострочное описание)\n"
            "Команда: /poker - начать голосование"
        )

    vote_id = str(message.message_id)
    game = storage.new_game(message.chat.id, vote_id, _initiator_from_message(message), text)
    resp = await message.answer(
        text=game.get_text(),
        reply_markup=_game_markup(game),
    )
    game.reply_message_id = resp.message_id
    await storage.save_game(game)


@router.message(Command("voteban"))
async def voteban(message: Message, command: CommandObject, bot):
    banuser = (command.args or "").strip()
    if not banuser:
        return await message.reply(VOTEBAN_USAGE)
    await bot.send_poll(
        chat_id=message.chat.id,
        question=f"Банить ли пользователя {banuser}?",
        options=["Да", "Нет"],
        is_anonymous=True,
    )


@router.callback_query(F.data.regexp(r"^vote-click-(.+?)-(.+)$"))
async def vote_click(callback: CallbackQuery, storage: GameRegistry, bot):
    vote_id, point = callback.data[len("vote-click-"):].rsplit("-", 1)
    game = await storage.get_game(callback.message.chat.id, vote_id)
    if not game:
        return await callback.answer(text="Нет такой игры")
    if game.revealed:
        return await callback.answer(text="Нельзя менять ответ после вскрытия оценок")

    game.add_vote(_initiator_from_callback(callback), point)
    await storage.save_game(game)
    try:
        await bot.edit_message_text(
            chat_id=callback.message.chat.id,
            message_id=game.reply_message_id,
            text=game.get_text(),
            reply_markup=_game_markup(game),
        )
    except TelegramBadRequest:
        logger.exception("Error when updating markup")

    await callback.answer(text="Ответ {} принят".format(point))


@router.callback_query(
    F.data.regexp(r"^(restart-new|reveal-new)-click-(.+)$")
)
async def reveal_click(callback: CallbackQuery, storage: GameRegistry, bot):
    operation, vote_id = callback.data.split("-click-", 1)
    game = await storage.get_game(callback.message.chat.id, vote_id)
    if not game:
        return await callback.answer(text="No such game")

    initiator = callback.from_user
    if initiator.id != game.initiator["id"]:
        return await callback.answer(
            text="{} доступно только инициатору игры".format(operation)
        )

    if operation == Game.OP_RESTART_NEW:
        game.restart()
        current_text = game.get_text()
    else:
        game.revealed = True
        current_text = game.get_text()

    try:
        await bot.edit_message_text(
            chat_id=callback.message.chat.id,
            message_id=game.reply_message_id,
            text=current_text,
            reply_markup=_game_markup(game),
        )
    except TelegramBadRequest:
        logger.exception("Error when updating markup")

    resp = await callback.message.answer(
        text=game.get_text(),
        reply_markup=_game_markup(game),
    )
    game.reply_message_id = resp.message_id
    await storage.save_game(game)
    await callback.answer()


def create_router() -> Router:
    r = Router(name="poker")
    r.message(Command("poker"))(poker_start)
    r.message(Command("voteban"))(voteban)
    r.callback_query(F.data.regexp(r"^vote-click-(.+?)-(.+)$"))(vote_click)
    r.callback_query(F.data.regexp(r"^(restart-new|reveal-new)-click-(.+)$"))(reveal_click)
    return r


router = create_router()
