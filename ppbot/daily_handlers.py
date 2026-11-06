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
    next_scheduled_date,
    set_leader,
    today_in_tz,
)
from ppbot.daily_storage import (
    DailyRegistry,
    refresh_schedule_window,
)
from ppbot.game import GameRegistry
from ppbot.daily_ui import (
    GREETING_HELP,
    PREFIX_ADD,
    PREFIX_COSTREMIND,
    PREFIX_COSTREMIND_SETTIME,
    PREFIX_COSTREMIND_TOGGLE,
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
    PREFIX_VACPLAN,
    PREFIX_WHO,
    _ensure_today_schedule,
    _schedule_leader,
    build_cost_remind_settings_markup,
    build_member_picker_markup,
    show_menu,
    show_planned_vacations,
    show_remove_list,
    show_team,
    who_reply,
)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class AddMember(StatesGroup):
    waiting = State()


class SetTime(StatesGroup):
    waiting = State()


class SetCostReminderTime(StatesGroup):
    waiting = State()


class ResetChat(StatesGroup):
    confirm = State()


def _resolve_member(members: List[DailyMember], text: str) -> Optional[DailyMember]:
    text = (text or "").strip()
    if not text:
        return None
    stripped = text.lstrip("@")
    for m in members:
        if m.username == text or m.username == stripped:
            return m
    for m in members:
        handle = m.at_handle
        if handle and handle.lstrip("@") == stripped:
            return m
    for m in members:
        if stripped in m.username:
            return m
    return None


async def _refresh_schedule(daily: DailyRegistry, chat_id: int, tz, workdays=None) -> None:
    """Recompute the persisted 14-day leader window after a roster change."""
    await refresh_schedule_window(daily, chat_id, today_in_tz(tz), workdays=workdays)


def create_router() -> Router:
    r = Router(name="daily")

    @r.message(Command("daily"))
    async def daily_menu(message: Message, daily: DailyRegistry, tz, workdays=None):
        wd = workdays.is_workday if workdays is not None else None
        await show_menu(message, daily, tz, workdays=wd)

    @r.callback_query(F.data == PREFIX_MENU)
    async def menu_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
        wd = workdays.is_workday if workdays is not None else None
        await show_menu(callback.message, daily, tz, workdays=wd, edit=True)
        await callback.answer()

    @r.callback_query(F.data == PREFIX_TEAM)
    async def team_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        await show_team(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.callback_query(F.data == PREFIX_ADD)
    async def add_member_callback(callback: CallbackQuery, state: FSMContext):
        await state.set_state(AddMember.waiting)
        await callback.message.answer("Пришлите имя участника (например: «Иван @ivanov») или ответьте на его сообщение")
        await callback.answer()

    @r.callback_query(F.data == PREFIX_REMOVE_LIST)
    async def remove_list_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        await show_remove_list(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(.+)$".format(PREFIX_REMOVE)))
    async def remove_member_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
        position = int(callback.data[len(PREFIX_REMOVE):])
        await daily.remove_member(callback.message.chat.id, position)
        chat = await daily.get_chat(callback.message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
            await daily.upsert_chat(chat)
        wd = workdays.is_workday if workdays is not None else None
        await _refresh_schedule(daily, callback.message.chat.id, tz, workdays=wd)
        await show_remove_list(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.message(AddMember.waiting)
    async def add_member_input(message: Message, state: FSMContext, daily: DailyRegistry, tz, workdays=None):
        chat_id = message.chat.id
        members = await daily.get_members(chat_id)
        text = (message.text or "").strip()
        if not text:
            await message.answer("Пришлите имя участника (например: «Иван @ivanov») или ответьте на его сообщение")
            return

        # Check for duplicates
        existing = [m for m in members if m.username == text]
        if existing:
            await message.answer("Уже в команде")
            await state.clear()
            return

        # Adding by replying to a member's message captures their Telegram id,
        # so the mini-app can authorize them without any username matching.
        user_id = None
        replied = message.reply_to_message.from_user if message.reply_to_message else None
        if replied is not None and not replied.is_bot:
            user_id = replied.id
            if any(m.user_id == user_id for m in members):
                await message.answer("Уже в команде")
                await state.clear()
                return

        new_member = DailyMember(chat_id=chat_id, position=len(members), username=text, user_id=user_id)

        new_member.chat_id = chat_id
        new_member.position = len(members)
        await daily.add_member(new_member)
        chat = await daily.get_chat(chat_id)
        if chat is None:
            chat = DailyChat(chat_id=chat_id)
            await daily.upsert_chat(chat)
        wd = workdays.is_workday if workdays is not None else None
        await _refresh_schedule(daily, chat_id, tz, workdays=wd)
        await state.clear()
        await show_team(message, daily, tz)

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
    async def who_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
        wd = workdays.is_workday if workdays is not None else None
        await who_reply(callback.message, daily, tz, workdays=wd, edit=True)
        await callback.answer()

    @r.message(Command("who"))
    async def who_command(message: Message, daily: DailyRegistry, tz, workdays=None):
        wd = workdays.is_workday if workdays is not None else None
        await who_reply(message, daily, tz, workdays=wd)

    @r.message(Command("substitute"))
    async def substitute_command(message: Message, daily: DailyRegistry, tz, workdays=None):
        chat = await daily.get_chat(message.chat.id)
        members = await daily.get_members(message.chat.id)
        if not members:
            await message.answer("Команда пуста. Добавьте участников через /team")
            return
        if chat is None:
            chat = DailyChat(chat_id=message.chat.id)
        today = today_in_tz(tz)
        wd = workdays.is_workday if workdays is not None else None
        schedule = await _ensure_today_schedule(daily, chat, members, today, workdays=wd)
        leader = await _schedule_leader(schedule, members, str(today))
        if leader is None:
            await message.answer("Все пропущены сегодня")
            return
        other_s = next_scheduled_date(schedule, str(today))
        if other_s is None:
            await message.answer("Некого подменять")
            return
        b_pos, a_pos, msg, err = apply_substitute(schedule, members, str(today), other_s)
        if err is not None:
            await message.answer(err)
            return
        await daily.swap_schedule_dates(message.chat.id, str(today), other_s)
        chat.next_index = b_pos
        await daily.upsert_chat(chat)
        await message.answer(msg)

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_SUB)))
    async def substitute_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
        chat = await daily.get_chat(callback.message.chat.id)
        members = await daily.get_members(callback.message.chat.id)
        if not members:
            await callback.answer("Команда пуста")
            return
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
        today = today_in_tz(tz)
        wd = workdays.is_workday if workdays is not None else None
        schedule = await _ensure_today_schedule(daily, chat, members, today, workdays=wd)
        other_s = next_scheduled_date(schedule, str(today))
        if other_s is None:
            await callback.answer("Некого подменять")
            return
        b_pos, a_pos, msg, err = apply_substitute(schedule, members, str(today), other_s)
        if err is not None:
            await callback.answer(err)
            return
        await daily.swap_schedule_dates(callback.message.chat.id, str(today), other_s)
        chat.next_index = b_pos
        await daily.upsert_chat(chat)
        await callback.message.edit_text(msg)
        await callback.answer()

    @r.callback_query(F.data.regexp(r"^{}(\d+)$".format(PREFIX_SKIP)))
    async def skip_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
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
        wd = workdays.is_workday if workdays is not None else None
        await _refresh_schedule(daily, callback.message.chat.id, tz, workdays=wd)
        if new_leader is None:
            await callback.message.edit_text("Все пропущены сегодня, дейлик отменён")
        else:
            await callback.message.edit_text(
                "Пропуск принят. Сегодня ведёт {}".format(new_leader.get_display_name(today))
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
    async def team_command(message: Message, daily: DailyRegistry, tz):
        await show_team(message, daily, tz)

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
    async def leader_callback(callback: CallbackQuery, daily: DailyRegistry, tz, workdays=None):
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
        wd = workdays.is_workday if workdays is not None else None
        await _refresh_schedule(daily, callback.message.chat.id, tz, workdays=wd)
        chosen = next((m for m in members if m.position == position), None)
        await callback.message.edit_text(
            "Сегодня ведёт {}".format(chosen.get_display_name(today) if chosen else "")
        )
        await callback.answer()

    @r.message(Command("setleader"))
    async def setleader_command(message: Message, daily: DailyRegistry, tz, workdays=None):
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
        wd = workdays.is_workday if workdays is not None else None
        await _refresh_schedule(daily, message.chat.id, tz, workdays=wd)
        await message.answer("Сегодня ведёт {}".format(member.get_display_name(today)))

    # ---- vacation ----

    # Vacation editing moved to the mini-app calendar (PP_WEBAPP_PUBLIC_URL);
    # the chat-side /vacation command and the inline picker are removed.

    @r.callback_query(F.data == PREFIX_VACPLAN)
    async def vacplan_callback(callback: CallbackQuery, daily: DailyRegistry, tz):
        await show_planned_vacations(callback.message, daily, tz, edit=True)
        await callback.answer()

    @r.message(Command("vacplan"))
    async def vacplan_command(message: Message, daily: DailyRegistry, tz):
        await show_planned_vacations(message, daily, tz, edit=False)

    # ---- cost-reminder settings ----

    @r.callback_query(F.data == PREFIX_COSTREMIND)
    async def cost_remind_callback(callback: CallbackQuery, daily: DailyRegistry):
        chat = await daily.get_chat(callback.message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
            await daily.upsert_chat(chat)
        await callback.message.edit_text(
            "Напоминания о трудозатратах",
            reply_markup=build_cost_remind_settings_markup(chat.cost_reminder_enabled, chat.cost_reminder_time),
        )
        await callback.answer()

    @r.callback_query(F.data == PREFIX_COSTREMIND_TOGGLE)
    async def cost_remind_toggle_callback(callback: CallbackQuery, daily: DailyRegistry):
        chat = await daily.get_chat(callback.message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=callback.message.chat.id)
        chat.cost_reminder_enabled = not chat.cost_reminder_enabled
        await daily.upsert_chat(chat)
        await callback.message.edit_text(
            "Напоминания о трудозатратах",
            reply_markup=build_cost_remind_settings_markup(chat.cost_reminder_enabled, chat.cost_reminder_time),
        )
        await callback.answer()

    @r.callback_query(F.data == PREFIX_COSTREMIND_SETTIME)
    async def cost_remind_settime_callback(callback: CallbackQuery, state: FSMContext):
        await state.set_state(SetCostReminderTime.waiting)
        await callback.message.answer("Пришлите время напоминания в формате ЧЧ:ММ")
        await callback.answer()

    @r.message(SetCostReminderTime.waiting)
    async def cost_remind_time_input(message: Message, state: FSMContext, daily: DailyRegistry):
        text = (message.text or "").strip()
        if not TIME_RE.match(text):
            await message.answer("Неверный формат. Пришлите время в формате ЧЧ:ММ")
            return
        chat = await daily.get_chat(message.chat.id)
        if chat is None:
            chat = DailyChat(chat_id=message.chat.id)
        chat.cost_reminder_time = text
        await daily.upsert_chat(chat)
        await state.clear()
        await message.answer("Время напоминания о трудозатратах: {}".format(text))

    return r
