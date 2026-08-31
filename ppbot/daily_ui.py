"""Daily standup UI helpers: markup builders, /daily status, /team list, /who reply, help text."""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ppbot.daily import (
    DailyChat,
    DailyMember,
    format_ru_date,
    member_list_text,
    next_scheduled_date,
    today_in_tz,
)
from ppbot.daily_storage import DailyRegistry

PREFIX_SUB = "daily:sub:"
PREFIX_SKIP = "daily:skip:"
PREFIX_TEAM = "daily:team"
PREFIX_TIME = "daily:time"
PREFIX_WHO = "daily:who"
PREFIX_HELP = "daily:help"
PREFIX_MENU = "daily:menu"
PREFIX_ADD = "daily:add"
PREFIX_REMOVE = "daily:remove:"
PREFIX_REMOVE_LIST = "daily:removelist"
PREFIX_BACK = "daily:back"
PREFIX_LEADER = "daily:lead"
PREFIX_LEAD = "daily:lead:"
PREFIX_VACATION = "daily:vac"
PREFIX_VAC = "daily:vac:"
PREFIX_VACPLAN = "daily:vacplan"
PREFIX_COSTREMIND = "daily:costremind"
PREFIX_COSTREMIND_SETTIME = "daily:costremind:settime"
PREFIX_COSTREMIND_TOGGLE = "daily:costremind:toggle"


def build_reminder_markup(leader: DailyMember, show_menu: bool = False) -> InlineKeyboardMarkup:
    """Buttons on the 'who leads today' message: substitute and skip.

    Payload carries the leader's POSITION (always an int, stable) instead of
    user_id: members added by @username have user_id=None, which would make
    the callback_data lack the trailing digits the handlers' regex requires.

    When `show_menu` is set the markup also gets a jump to the main /daily
    menu (the scheduled reminder keeps it off, only the /who views enable it).
    """
    sub_data = "{}{}".format(PREFIX_SUB, leader.position)
    skip_data = "{}{}".format(PREFIX_SKIP, leader.position)
    rows = [
        [
            InlineKeyboardButton(text="Подмените меня", callback_data=sub_data),
            InlineKeyboardButton(text="Пропуск", callback_data=skip_data),
        ]
    ]
    if show_menu:
        rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=PREFIX_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Состав команды", callback_data=PREFIX_TEAM),
                InlineKeyboardButton(text="Время дейлика", callback_data=PREFIX_TIME),
            ],
            [
                InlineKeyboardButton(text="Кто сегодня ведёт", callback_data=PREFIX_WHO),
                InlineKeyboardButton(text="Справка", callback_data=PREFIX_HELP),
            ],
            [
                InlineKeyboardButton(text="Выбрать ведущего", callback_data=PREFIX_LEADER),
                InlineKeyboardButton(text="Отпуск", callback_data=PREFIX_VACATION),
            ],
            [
                InlineKeyboardButton(text="Напомн. о трудозатратах", callback_data=PREFIX_COSTREMIND),
            ],
        ]
    )


def build_cost_remind_settings_markup(enabled: bool, time: str) -> InlineKeyboardMarkup:
    """Cost-reminder settings view: on/off toggle, time, and back to menu."""
    toggle_text = "Выключить" if enabled else "Включить"
    rows = [
        [InlineKeyboardButton(text=toggle_text, callback_data=PREFIX_COSTREMIND_TOGGLE)],
        [InlineKeyboardButton(text="Время: {}".format(time), callback_data=PREFIX_COSTREMIND_SETTIME)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_MENU)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_member_picker_markup(
    members: List[DailyMember], prefix: str, *, vacplan_button: bool = False,
) -> InlineKeyboardMarkup:
    """One button per member carrying its position, plus a back button.

    `prefix` is the callback prefix (e.g. PREFIX_LEAD or PREFIX_VAC); the
    payload carries the member POSITION (stable even for user_id=None members).

    When *vacplan_button* is True, a "Планируемые отпуска" row is inserted
    before the back button (only meaningful for the vacation picker).
    """
    rows = [
        [
            InlineKeyboardButton(
                text="{} {}".format(("👑" if prefix == PREFIX_LEAD else "🏖"), m.get_display_name()),
                callback_data="{}{}".format(prefix, m.position),
            )
        ]
        for m in members
    ]
    if vacplan_button:
        rows.append([InlineKeyboardButton(text="Планируемые отпуска", callback_data=PREFIX_VACPLAN)])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_remove_markup(members: List[DailyMember]) -> InlineKeyboardMarkup:
    """Buttons for the dedicated removal list: one member per row (vertically),
    each prefixed with a cross, plus a back button to the team view."""
    rows = [
        [
            InlineKeyboardButton(
                text="❌ {}".format(m.get_display_name()),
                callback_data="{}{}".format(PREFIX_REMOVE, m.position),
            )
        ]
        for m in members
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_TEAM)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ensure_today_schedule(daily: DailyRegistry, chat: DailyChat, members: List[DailyMember], today, workdays=None) -> Dict[str, Optional[int]]:
    """Ensure the persisted 14-day schedule covers today, return it as dict.

    Mirrors the scheduler's window maintenance so the /who and /daily views
    always read the same source of truth as the reminder.
    """
    await daily.ensure_schedule(chat, members, today, workdays=workdays)
    return await daily.get_schedule(chat.chat_id)


async def _schedule_leader(
    schedule: Dict[str, Optional[int]],
    members: List[DailyMember],
    day_s: str,
) -> Optional[DailyMember]:
    """Member scheduled to lead on `day_s` (position -> member lookup)."""
    position = schedule.get(day_s)
    if position is None:
        return None
    return next((m for m in members if m.position == position), None)


async def show_menu(message: Message, daily: DailyRegistry, tz, workdays=None, edit: bool = False) -> None:
    chat_id = message.chat.id
    chat = await daily.get_chat(chat_id)
    members = await daily.get_members(chat_id)
    if chat is None:
        chat = DailyChat(chat_id=chat_id)
    today = today_in_tz(tz)
    schedule = await _ensure_today_schedule(daily, chat, members, today, workdays=workdays)
    leader = await _schedule_leader(schedule, members, str(today))
    leader_str = leader.get_display_name(str(today)) if leader else "—"
    vacationers = [m for m in members if m.is_on_vacation(str(today))]
    available = len(members) - len(vacationers)
    count_str = "{} из {}".format(available, len(members)) if vacationers else str(available)
    text = (
        "📋 Дейлик\n"
        "🕙 Время: {time}\n"
        "👥 Участников: {count}\n"
    ).format(time=chat.daily_time, count=count_str)
    if vacationers:
        parts = ", ".join(
            "{} (до {})".format(m.plain_name, format_ru_date(m.vacation_until))
            for m in vacationers
        )
        text += "В отпуске: {}\n".format(parts)
    nonworkday = ((await workdays(today)) is False) if workdays else today.weekday() >= 5
    if nonworkday:
        text += "🚫 Сегодня нерабочий день, дейлика нет"
        nxt = next_scheduled_date(schedule, str(today))
        nxt_member = None
        if nxt is not None:
            nxt_member = await _schedule_leader(schedule, members, nxt)
        if nxt is not None and nxt_member is not None:
            text += "\n📅 Ближайший дейлик: {}, ведёт {}".format(format_ru_date(nxt), nxt_member.get_display_name(str(today)))
    else:
        text += "👤 Сегодня ведёт: {leader}".format(leader=leader_str)
        if leader is None:
            text += "\nВсе пропущены сегодня"
    if edit:
        await message.edit_text(text, reply_markup=build_menu_markup())
    else:
        await message.answer(text, reply_markup=build_menu_markup())


async def show_team(message: Message, daily: DailyRegistry, tz, edit: bool = False) -> None:
    chat_id = message.chat.id
    members = await daily.get_members(chat_id)
    text = "Состав команды:\n" + member_list_text(members, str(today_in_tz(tz)))
    rows = [
        [InlineKeyboardButton(text="➕ Добавить", callback_data=PREFIX_ADD)],
        [InlineKeyboardButton(text="✖️ Удалить участника", callback_data=PREFIX_REMOVE_LIST)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_MENU)],
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def show_remove_list(message: Message, daily: DailyRegistry, tz, edit: bool = False) -> None:
    chat_id = message.chat.id
    members = await daily.get_members(chat_id)
    if not members:
        text = "Команда пуста. Добавьте участников через /team"
    else:
        text = "Удаление участников:\n" + member_list_text(members, str(today_in_tz(tz)))
    markup = build_remove_markup(members)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def show_planned_vacations(message: Message, daily: DailyRegistry, tz, edit: bool = False) -> None:
    chat_id = message.chat.id
    members = await daily.get_members(chat_id)
    today_s = str(today_in_tz(tz))
    future = sorted(
        [m for m in members if m.vacation_start is not None and m.vacation_start > today_s],
        key=lambda m: m.vacation_start,
    )
    if future:
        lines = [
            "\U0001f3d6 {}: с {} по {}".format(
                m.get_display_name(), format_ru_date(m.vacation_start), format_ru_date(m.vacation_until),
            )
            for m in future
        ]
        text = "\n".join(lines)
    else:
        text = "Нет запланированных отпусков"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_VACATION)]]
    )
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def who_reply(message: Message, daily: DailyRegistry, tz, workdays=None, edit: bool = False) -> None:
    chat_id = message.chat.id
    chat = await daily.get_chat(chat_id)
    members = await daily.get_members(chat_id)
    today = today_in_tz(tz)
    today_s = str(today)
    if chat is None:
        chat = DailyChat(chat_id=chat_id)
    schedule = await _ensure_today_schedule(daily, chat, members, today, workdays=workdays)
    leader = await _schedule_leader(schedule, members, today_s)
    if leader is None:
        if not members:
            text = "Команда пуста. Добавьте участников через /team"
        else:
            nonworkday = ((await workdays(today)) is False) if workdays else today.weekday() >= 5
            if nonworkday:
                text = "Сегодня нерабочий день, дейлика нет."
                nxt = next_scheduled_date(schedule, today_s)
                nxt_member = None
                if nxt is not None:
                    nxt_member = await _schedule_leader(schedule, members, nxt)
                if nxt is not None and nxt_member is not None:
                    text += "\nБлижайший дейлик: {}, ведёт {}".format(format_ru_date(nxt), nxt_member.get_display_name(today_s))
            else:
                text = "Все пропущены сегодня, дейлик отменён"
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    text = "Сегодня ведёт {}".format(leader.get_display_name(today_s))
    vacationers = [m for m in members if m.is_on_vacation(today_s)]
    if vacationers:
        parts = ", ".join(
            "{} (до {})".format(m.plain_name, format_ru_date(m.vacation_until))
            for m in vacationers
        )
        text += "\nВ отпуске: {}".format(parts)
    if members:
        tomorrow_leader = await _schedule_leader(
            schedule, members, str(today + datetime.timedelta(days=1))
        )
        if tomorrow_leader is not None:
            text += "\nЗавтра ведёт {}".format(tomorrow_leader.get_display_name(today_s))
    markup = build_reminder_markup(leader, show_menu=True)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


GREETING_HELP = (
    "🤖 Дейлик-бот\n\n"
    "Планирование покера:\n"
    "/poker задача — начать голосование\n"
    "/voteban @имя — голосование за бан\n\n"
    "Дейлики:\n"
    "/daily — главное меню\n"
    "/team — состав команды\n"
    "/time — время дейлика (ЧЧ:ММ)\n"
    "/who — кто сегодня ведёт\n"
    "/substitute — подменить ведущего\n"
    "/setleader @ник — назначить ведущего на сегодня\n"
    "/vacation @ник ДД.ММ.ГГГГ-ДД.ММ.ГГГГ — отпуск с по («снять» — вернуть)\n"
    "/costremind — напоминания о списании трудозатрат (вкл/выкл, время)\n"
    "/vacplan — запланированные отпуска\n"
    "/reset — сбросить историю бота для чата (двойное подтверждение)\n"
    "/help — эта справка\n\n"
    "В сообщении «Кто сегодня ведёт» доступны кнопки:\n"
    "• Подмените меня — сегодня ведёт ведущий следующего рабочего дня, в следующий рабочий день — вы (порядок состава не меняется)\n"
    "• Пропуск — разовый пропуск без подмены\n\n"
    "Рабочие дни синхронизируются с производственным календарём РФ (isdayoff.ru)."
)
