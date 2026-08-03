"""Daily standup chat handlers: reminder buttons, /daily, /team, /time, /who, /substitute."""
from __future__ import annotations

import re
from typing import List, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from ppbot.daily import (
    DailyChat,
    DailyMember,
    apply_skip,
    apply_substitute,
    format_ru_date,
    parse_vacation_date,
    set_leader,
    today_in_tz,
    today_leader,
)
from ppbot.daily_storage import DailyRegistry
from ppbot.game import GameRegistry
from ppbot.daily_ui import (
    GREETING_HELP,
    PREFIX_ADD,
    PREFIX_HELP,
    PREFIX_LEAD,
    PREFIX_LEADER,
    PREFIX_MENU,
    PREFIX_REMOVE,
    PREFIX_REMOVE_LIST,
    PREFIX_SKIP,
    PREFIX_SUB,
    PREFIX_TEAM,
    PREFIX_TIME,
    PREFIX_VAC,
    PREFIX_VACATION,
    PREFIX_WHO,
    build_member_picker_markup,
    show_menu,
    show_remove_list,
    show_team,
    who_reply,
)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class AddMember(StatesGroup):
    waiting = State()


class SetTime(StatesGroup):
    waiting = State()


class SetVacation(StatesGroup):
    waiting = State()


class ResetChat(StatesGroup):
    confirm = State()


def _member_from_user(user: User) -> DailyMember:
    return DailyMember(
        chat_id=0,
        position=-1,
        first_name=user.first_name or user.username or str(user.id),
        user_id=user.id,
        username=user.username,
    )


def _resolve_member(members: List[DailyMember], text: str) -> Optional[DailyMember]:
    """Resolve a member by '@username', plain username, or exact first_name."""
    text = (text or "").strip().lstrip("@")
    if not text:
        return None
    for m in members:
        if m.username and m.username == text:
            return m
    for m in members:
        if m.first_name == text:
            return m
    return None


async def _refresh_schedule(daily: DailyRegistry, chat_id: int, tz) -> None:
    """Recompute the persisted 14-day leader window after a roster change."""
    chat = await daily.get_chat(chat_id)
    if chat is None:
        return
    members = await daily.get_members(chat_id)
    await daily.rebuild_schedule(chat_id, chat.next_index, members, today_in_tz(tz))


def create_router() -> Router:
    r = Router(name="daily")

    @r.message(Command("daily"))
    async def daily_menu(message: Message, daily: DailyRegistry, tz):
        await show_menu(message, daily, tz)

    @r.callback_query(F.data == PREFIX_MENU)
    async def menu_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        await show_menu(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.callback_query(F.data == PREFIX_TEAM)
    async def team_callback(callback: CallbackQuery, daily: DailyRegistry):
        await show_team(callback.message, daily, edit=True)
        await callback.answer()

    @r.callback_query(F.data == PREFIX_ADD)
    async def add_member_callback(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AddMember.waiting)
        await callback.message.answer("Пришлите @username или перешлите сообщение участника")
        await callback.answer()

    @r.callback_query(F.data == PREFIX_REMOVE_LIST)
    async def remove_list_callback(callback: CallbackQuery, daily: DailyRegistry):
        await show_remove_list(callback.message, daily, edit=True)
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(.+)$".format(PREFIX_REMOVE)))
    async def remove_member_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        position = int(callback.data[len(PREFIX_REMOVE):])
        await daily.remove_member(callback.message.chat.id, position)
        chat = await daily.get_chat(callback.message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
            await daily.upsert_chat(chat)
        await _refresh_schedule(daily, callback.message.chat.id, tz)
        await show_remove_list(callback.message, daily, edit=True)
        await callback.answer()

    @r.message(AddMember.waiting)
    async def add_member_input(message: Message, state: FSMContext, daily: DailyRegistry, tz):
        chat_id = message.chat.id
        members = await daily.get_members(chat_id)
        new_member = None
        if message.reply_to_message and message.reply_to_message.from_user:
            new_member = _member_from_user(message.reply_to_message.from_user)
        else:
            text = (message.text or "").strip()
            if text.startswith("@"):
                username = text[1:]
                existing = [m for m in members if m.username == username]
                if existing:
                    await message.answer("Уже в команде")
                    await state.clear()
                    return
                new_member = DailyMember(chat_id=chat_id, position=len(members), first_name=username, username=username)
            elif text:
                existing = [m for m in members if m.first_name == text]
                if existing:
                    await message.answer("Уже в команде")
                    await state.clear()
                    return
                new_member = DailyMember(chat_id=chat_id, position=len(members), first_name=text)

        if new_member is None:
            await message.answer("Не понял. Пришлите @username или перешлите сообщение участника")
            return

        if new_member.user_id is not None and any(m.user_id == new_member.user_id for m in members):
            await message.answer("Уже в команде")
            await state.clear()
            return

        new_member.chat_id = chat_id
        new_member.position = len(members)
        await daily.add_member(new_member)
        chat = await daily.get_chat(chat_id)
        if chat is None:
            chat = DailyChat(chat_id=chat_id)
            await daily.upsert_chat(chat)
        await _refresh_schedule(daily, chat_id, tz)
        await state.clear()
        await show_team(message, daily)

    @r.callback_query(F.data == PREFIX_TIME)
    async def time_callback(callback: CallbackQuery, state: FSMContext):
        await state.set_state(SetTime.waiting)
        await callback.message.answer("Пришлите время дейлика в формате ЧЧ:ММ")
        await callback.answer()

    @r.message(Command("time"))
    async def time_command(message: Message, state: FSMContext):
        await state.set_state(SetTime.waiting)
        await message.answer("Пришлите время дейлика в формате ЧЧ:ММ")

    @r.message(SetTime.waiting)
    async def time_input(message: Message, state: FSMContext, daily: DailyRegistry):
        text = (message.text or "").strip()
        if not TIME_RE.match(text):
            await message.answer("Неверный формат. Пришлите время в формате ЧЧ:ММ")
            return
        chat = await daily.get_chat(message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=message.chat.id)
        chat.daily_time = text
        await daily.upsert_chat(chat)
        await state.clear()
        await message.answer("Время дейлика: {}".format(text))

    @r.callback_query(F.data == PREFIX_WHO)
    async def who_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        await who_reply(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.message(Command("who"))
    async def who_command(message: Message, daily: DailyRegistry, tz):
        await who_reply(message, daily, tz)

    @r.message(Command("substitute"))
    async def substitute_command(message: Message, daily: DailyRegistry, tz):
        chat = await daily.get_chat(message.chat.id)
        members = await daily.get_members(message.chat.id)
        if not members:
            await message.answer("Команда пуста. Добавьте участников через /team")
            return
        if chat is None:
            chat = DailyChat(chat_id=message.chat.id)
        today = str(today_in_tz(tz))
        leader = today_leader(members, chat.next_index, today)
        if leader is None:
            await message.answer("Все пропущены сегодня")
            return
        new_members, new_next, msg = apply_substitute(
            members, chat.next_index, leader.position, today
        )
        await daily.replace_members(message.chat.id, new_members)
        chat.next_index = new_next
        await daily.upsert_chat(chat)
        await _refresh_schedule(daily, message.chat.id, tz)
        await message.answer(msg or "Подмена выполнена")

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_SUB)))
    async def substitute_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        position = int(callback.data[len(PREFIX_SUB):])
        chat = await daily.get_chat(callback.message.chat.id)
        members = await daily.get_members(callback.message.chat.id)
        today = str(today_in_tz(tz))
        if chat is None or not members:
            await callback.answer("Команда пуста")
            return
        new_members, new_next, msg = apply_substitute(members, chat.next_index, position, today)
        await daily.replace_members(callback.message.chat.id, new_members)
        chat.next_index = new_next
        await daily.upsert_chat(chat)
        await _refresh_schedule(daily, callback.message.chat.id, tz)
        await callback.message.edit_text(msg)
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_SKIP)))
    async def skip_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        position = int(callback.data[len(PREFIX_SKIP):])
        chat = await daily.get_chat(callback.message.chat.id)
        members = await daily.get_members(callback.message.chat.id)
        today = str(today_in_tz(tz))
        if chat is None or not members:
            await callback.answer("Команда пуста")
            return
        new_members, new_next, new_leader, msg = apply_skip(members, chat.next_index, position, today)
        await daily.replace_members(callback.message.chat.id, new_members)
        chat.next_index = new_next
        await daily.upsert_chat(chat)
        await _refresh_schedule(daily, callback.message.chat.id, tz)
        if new_leader is None:
            await callback.message.edit_text("Все пропущены сегодня, дейлик отменён")
        else:
            await callback.message.edit_text(
                "Пропуск принят. Сегодня ведёт {}".format(new_leader.display_name)
            )
        await callback.answer()

    @r.callback_query(F.data == PREFIX_HELP)
    async def help_callback(callback: CallbackQuery):
        await callback.message.edit_text(GREETING_HELP)
        await callback.answer()

    @r.message(Command("reset"))
    async def reset_command(message: Message, state: FSMContext, daily: DailyRegistry, storage: GameRegistry):
        if await state.get_state() == ResetChat.confirm.state:
            await daily.delete_chat(message.chat.id)
            await storage.delete_chat_games(message.chat.id)
            await state.clear()
            await message.answer("История бота для этого чата сброшена")
            return
        await state.set_state(ResetChat.confirm)
        await message.answer(
            "🥲 Это сбросит историю бота для этого чата: состав команды, "
            "настройки дейлика и покер-голосования.\n"
            "Отправьте /reset ещё раз для подтверждения."
        )

    @r.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(GREETING_HELP)

    @r.message(Command("team"))
    async def team_command(message: Message, daily: DailyRegistry):
        await show_team(message, daily)

    # ---- manual leader override ----

    @r.callback_query(F.data == PREFIX_LEADER)
    async def leader_picker_callback(callback: CallbackQuery, daily: DailyRegistry):
        members = await daily.get_members(callback.message.chat.id)
        if not members:
            await callback.message.answer("Команда пуста. Добавьте участников через /team")
            await callback.answer()
            return
        await callback.message.edit_text(
            "Кто сегодня ведёт?",
            reply_markup=build_member_picker_markup(members, PREFIX_LEAD),
        )
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_LEAD)))
    async def leader_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        position = int(callback.data[len(PREFIX_LEAD):])
        chat = await daily.get_chat(callback.message.chat.id)
        members = await daily.get_members(callback.message.chat.id)
        today = str(today_in_tz(tz))
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
        new_next, err = set_leader(members, chat.next_index, position, today)
        if err is not None:
            await callback.answer(err)
            return
        chat.next_index = new_next
        await daily.upsert_chat(chat)
        await _refresh_schedule(daily, callback.message.chat.id, tz)
        chosen = next((m for m in members if m.position == position), None)
        await callback.message.edit_text(
            "Сегодня ведёт {}".format(chosen.display_name if chosen else "")
        )
        await callback.answer()

    @r.message(Command("setleader"))
    async def setleader_command(message: Message, daily: DailyRegistry, tz):
        members = await daily.get_members(message.chat.id)
        if not members:
            await message.answer("Команда пуста. Добавьте участников через /team")
            return
        chat = await daily.get_chat(message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=message.chat.id)
        member = None
        if message.reply_to_message and message.reply_to_message.from_user:
            member = next(
                (m for m in members if m.user_id == message.reply_to_message.from_user.id),
                None,
            )
        if member is None:
            text = (message.text or "").replace("/setleader", "", 1)
            member = _resolve_member(members, text)
        if member is None:
            await message.answer("Не нашёл участника. Напишите /setleader @ник или ответьте на сообщение")
            return
        today = str(today_in_tz(tz))
        new_next, err = set_leader(members, chat.next_index, member.position, today)
        if err is not None:
            await message.answer(err)
            return
        chat.next_index = new_next
        await daily.upsert_chat(chat)
        await _refresh_schedule(daily, message.chat.id, tz)
        await message.answer("Сегодня ведёт {}".format(member.display_name))

    # ---- vacation ----

    @r.callback_query(F.data == PREFIX_VACATION)
    async def vacation_picker_callback(callback: CallbackQuery, daily: DailyRegistry):
        members = await daily.get_members(callback.message.chat.id)
        if not members:
            await callback.message.answer("Команда пуста. Добавьте участников через /team")
            await callback.answer()
            return
        await callback.message.edit_text(
            "Кто уходит в отпуск?",
            reply_markup=build_member_picker_markup(members, PREFIX_VAC),
        )
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_VAC)))
    async def vacation_pick_callback(callback: CallbackQuery, state: FSMContext, daily: DailyRegistry):
        position = int(callback.data[len(PREFIX_VAC):])
        await state.set_state(SetVacation.waiting)
        await state.update_data(vacation_position=position)
        await callback.message.answer("До какой даты отпуск? (ДД.ММ.ГГГГ, «снять» — убрать)")
        await callback.answer()

    @r.message(Command("vacation"))
    async def vacation_command(message: Message, state: FSMContext, daily: DailyRegistry, tz):
        members = await daily.get_members(message.chat.id)
        if not members:
            await message.answer("Команда пуста. Добавьте участников через /team")
            return
        member = None
        if message.reply_to_message and message.reply_to_message.from_user:
            member = next(
                (m for m in members if m.user_id == message.reply_to_message.from_user.id),
                None,
            )
        if member is None:
            text = (message.text or "").replace("/vacation", "", 1).strip()
            arg_parts = text.split(None, 1)
            name_part = arg_parts[0] if arg_parts else ""
            member = _resolve_member(members, name_part)
            date_part = arg_parts[1] if len(arg_parts) > 1 else ""
        else:
            text = (message.text or "").replace("/vacation", "", 1).strip()
            arg_parts = text.split(None, 1)
            date_part = arg_parts[0] if arg_parts else ""
        if member is None:
            await message.answer("Не нашёл участника. Напишите /vacation @ник ДД.ММ.ГГГГ или ответьте на сообщение")
            return
        if date_part in ("снять", "нет", "0"):
            await daily.update_member_vacation(message.chat.id, member.position, None)
            await _refresh_schedule(daily, message.chat.id, tz)
            await message.answer("{} вернулся в ротацию".format(member.display_name))
            return
        if date_part:
            iso = parse_vacation_date(date_part)
            if iso is None:
                await message.answer("Неверный формат даты. Пришлите ДД.ММ.ГГГГ")
                return
            await daily.update_member_vacation(message.chat.id, member.position, iso)
            await _refresh_schedule(daily, message.chat.id, tz)
            await message.answer(
                "{} в отпуске до {}".format(member.display_name, format_ru_date(iso))
            )
            return
        await state.set_state(SetVacation.waiting)
        await state.update_data(vacation_position=member.position)
        await message.answer("До какой даты отпуск? (ДД.ММ.ГГГГ, «снять» — убрать)")

    @r.message(SetVacation.waiting)
    async def vacation_input(message: Message, state: FSMContext, daily: DailyRegistry, tz):
        text = (message.text or "").strip()
        data = await state.get_data()
        position = data.get("vacation_position")
        members = await daily.get_members(message.chat.id)
        member = next((m for m in members if m.position == position), None)
        if member is None:
            await message.answer("Участник не найден, попробуйте ещё раз")
            await state.clear()
            return
        if text in ("снять", "нет", "0"):
            await daily.update_member_vacation(message.chat.id, member.position, None)
            await _refresh_schedule(daily, message.chat.id, tz)
            await message.answer("{} вернулся в ротацию".format(member.display_name))
            await state.clear()
            return
        iso = parse_vacation_date(text)
        if iso is None:
            await message.answer("Неверный формат даты. Пришлите ДД.ММ.ГГГГ или «снять»")
            return
        await daily.update_member_vacation(message.chat.id, member.position, iso)
        await _refresh_schedule(daily, message.chat.id, tz)
        await message.answer(
            "{} в отпуске до {}".format(member.display_name, format_ru_date(iso))
        )
        await state.clear()

    return r
