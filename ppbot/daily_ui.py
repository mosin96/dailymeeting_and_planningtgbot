"""Daily standup UI helpers: markup builders, /daily status, /team list, /who reply, help text."""
from __future__ import annotations

import datetime
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ppbot.daily import (
    DailyChat,
    DailyMember,
    format_ru_date,
    member_list_text,
    next_leader,
    today_in_tz,
    today_leader,
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


def build_reminder_markup(leader: DailyMember) -> InlineKeyboardMarkup:
    """Buttons on the 'who leads today' message: substitute and skip.

    Payload carries the leader's POSITION (always an int, stable) instead of
    user_id: members added by @username have user_id=None, which would make
    the callback_data lack the trailing digits the handlers' regex requires.
    """
    sub_data = "{}{}".format(PREFIX_SUB, leader.position)
    skip_data = "{}{}".format(PREFIX_SKIP, leader.position)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подмените меня", callback_data=sub_data),
                InlineKeyboardButton(text="Пропуск", callback_data=skip_data),
            ]
        ]
    )


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
        ]
    )


def build_member_picker_markup(members: List[DailyMember], prefix: str) -> InlineKeyboardMarkup:
    """One button per member carrying its position, plus a back button.

    `prefix` is the callback prefix (e.g. PREFIX_LEAD or PREFIX_VAC); the
    payload carries the member POSITION (stable even for user_id=None members).
    """
    rows = [
        [
            InlineKeyboardButton(
                text="{} {}".format(("👑" if prefix == PREFIX_LEAD else "🏖"), m.display_name),
                callback_data="{}{}".format(prefix, m.position),
            )
        ]
        for m in members
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_remove_markup(members: List[DailyMember]) -> InlineKeyboardMarkup:
    """Buttons for the dedicated removal list: one member per row (vertically),
    each prefixed with a cross, plus a back button to the team view."""
    rows = [
        [
            InlineKeyboardButton(
                text="❌ {}".format(m.display_name),
                callback_data="{}{}".format(PREFIX_REMOVE, m.position),
            )
        ]
        for m in members
    ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=PREFIX_TEAM)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_menu(message: Message, daily: DailyRegistry, tz, edit: bool = False) -> None:
    chat_id = message.chat.id
    chat = await daily.get_chat(chat_id)
    members = await daily.get_members(chat_id)
    if chat is None:
        chat = DailyChat(chat_id=chat_id)
    today = str(today_in_tz(tz))
    leader = today_leader(members, chat.next_index, today)
    leader_str = leader.display_name if leader else "—"
    text = (
        "📋 Дейлик\n"
        "🕙 Время: {time}\n"
        "👥 Участников: {count}\n"
        "👤 Сегодня ведёт: {leader}"
    ).format(time=chat.daily_time, count=len(members), leader=leader_str)
    if edit:
        await message.edit_text(text, reply_markup=build_menu_markup())
    else:
        await message.answer(text, reply_markup=build_menu_markup())


async def show_team(message: Message, daily: DailyRegistry, edit: bool = False) -> None:
    chat_id = message.chat.id
    members = await daily.get_members(chat_id)
    text = "Состав команды:\n" + member_list_text(members)
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


async def show_remove_list(message: Message, daily: DailyRegistry, edit: bool = False) -> None:
    chat_id = message.chat.id
    members = await daily.get_members(chat_id)
    if not members:
        text = "Команда пуста. Добавьте участников через /team"
    else:
        text = "Удаление участников:\n" + member_list_text(members)
    markup = build_remove_markup(members)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def who_reply(message: Message, daily: DailyRegistry, tz, edit: bool = False) -> None:
    chat_id = message.chat.id
    chat = await daily.get_chat(chat_id)
    members = await daily.get_members(chat_id)
    today = today_in_tz(tz)
    today_s = str(today)
    if chat is None:
        chat = DailyChat(chat_id=chat_id)
    leader = today_leader(members, chat.next_index, today_s)
    if leader is None:
        if not members:
            text = "Команда пуста. Добавьте участников через /team"
        else:
            text = "Все пропущены сегодня, дейлик отменён"
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    text = "Сегодня ведёт {}".format(leader.display_name)
    vacationers = [m for m in members if m.is_on_vacation(today_s)]
    if vacationers:
        parts = ", ".join(
            "{} (до {})".format(m.plain_name, format_ru_date(m.vacation_until))
            for m in vacationers
        )
        text += "\nВ отпуске: {}".format(parts)
    if members:
        tomorrow_s = str(today + datetime.timedelta(days=1))
        tomorrow_leader = next_leader(
            members, (leader.position + 1) % len(members), tomorrow_s
        )
        if tomorrow_leader is not None:
            text += "\nЗавтра ведёт {}".format(tomorrow_leader.display_name)
    markup = build_reminder_markup(leader)
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
    "/vacation @ник ДД.ММ.ГГГГ — отпуск до даты («снять» — вернуть)\n"
    "/reset — сбросить историю бота для чата (двойное подтверждение)\n"
    "/help — эта справка\n\n"
    "В сообщении «Кто сегодня ведёт» доступны кнопки:\n"
    "• Подмените меня — сегодня ведёт следующий, завтра вы\n"
    "• Пропуск — разовый пропуск без подмены\n\n"
    "Рабочие дни синхронизируются с производственным календарём РФ (isdayoff.ru)."
)
