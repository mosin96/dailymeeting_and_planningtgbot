"""Shared daily standup actions for chat handlers and the mini-app.

The bot handlers and the mini-app HTTP API must behave identically: the same
validation, the same schedule refresh and the same messages. This module is
the single place where those multi-step operations live; presentation (chat
texts/buttons vs JSON) stays in the callers.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ppbot.daily import (
    DailyChat,
    DailyMember,
    apply_skip,
    apply_substitute,
    next_scheduled_date,
    set_leader,
)
from ppbot.daily_storage import DailyRegistry, refresh_schedule_window

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

WorkdayPredicate = Optional[Callable[[datetime.date], Awaitable[bool]]]


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    member: Optional[DailyMember] = None
    moved: bool = False
    position: Optional[int] = None


def is_valid_time(value: str) -> bool:
    return bool(TIME_RE.match((value or "").strip()))


def workday_predicate(workdays) -> WorkdayPredicate:
    """Turn the injected WorkdayClient (or a callable) into a predicate."""
    if workdays is None:
        return None
    return getattr(workdays, "is_workday", workdays)


async def is_workday(today: datetime.date, workdays=None) -> bool:
    predicate = workday_predicate(workdays)
    if predicate is None:
        return today.weekday() < 5
    return bool(await predicate(today))


async def ensure_schedule_for_day(
    daily: DailyRegistry,
    chat: DailyChat,
    members: List[DailyMember],
    today: datetime.date,
    workdays=None,
) -> Dict[str, Optional[int]]:
    await daily.ensure_schedule(
        chat, members, today, workdays=workday_predicate(workdays)
    )
    return await daily.get_schedule(chat.chat_id)


def leader_for_day(
    schedule: Dict[str, Optional[int]],
    members: List[DailyMember],
    day_s: str,
) -> Optional[DailyMember]:
    position = schedule.get(day_s)
    if position is None:
        return None
    return next((m for m in members if m.position == position), None)


async def refresh_schedule(
    daily: DailyRegistry,
    chat_id: int,
    today: datetime.date,
    workdays=None,
) -> None:
    await refresh_schedule_window(
        daily, chat_id, today, workdays=workday_predicate(workdays)
    )


async def get_chat_or_default(daily: DailyRegistry, chat_id: int) -> DailyChat:
    chat = await daily.get_chat(chat_id)
    if chat is None:
        chat = DailyChat(chat_id=chat_id)
    return chat


async def build_overview(
    daily: DailyRegistry,
    chat_id: int,
    today: datetime.date,
    workdays=None,
) -> Dict[str, Any]:
    """Structured view of today's daily standup (used by the mini-app)."""
    members = await daily.get_members(chat_id)
    chat = await get_chat_or_default(daily, chat_id)
    today_s = str(today)
    schedule = await ensure_schedule_for_day(
        daily, chat, members, today, workdays=workdays
    )
    leader = leader_for_day(schedule, members, today_s)
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_leader = leader_for_day(schedule, members, str(tomorrow))
    daily_is_workday = await is_workday(today, workdays)

    next_date = None
    next_leader = None
    if not daily_is_workday:
        next_date = next_scheduled_date(schedule, today_s)
        if next_date is not None:
            next_leader = leader_for_day(schedule, members, next_date)

    vacationers = [m for m in members if m.is_on_vacation(today_s)]
    available = len(members) - len(vacationers)

    return {
        "today": today_s,
        "is_workday": daily_is_workday,
        "leader_position": leader.position if leader is not None else None,
        "leader_name": leader.get_display_name(today_s) if leader is not None else None,
        "tomorrow_position": (
            tomorrow_leader.position if tomorrow_leader is not None else None
        ),
        "tomorrow_name": (
            tomorrow_leader.get_display_name(today_s)
            if tomorrow_leader is not None
            else None
        ),
        "next_daily_date": next_date,
        "next_daily_leader_position": (
            next_leader.position if next_leader is not None else None
        ),
        "next_daily_leader_name": (
            next_leader.get_display_name(today_s) if next_leader is not None else None
        ),
        "members_count": len(members),
        "available_count": available,
        "vacations_today": [
            {
                "position": m.position,
                "name": m.plain_name,
                "until": m.active_vacation(today_s).end,
            }
            for m in vacationers
        ],
    }


async def planned_vacations(
    daily: DailyRegistry,
    chat_id: int,
    today: datetime.date,
) -> List[Dict[str, Any]]:
    """Future vacations (start date strictly after today), sorted like /vacplan."""
    members = await daily.get_members(chat_id)
    today_s = str(today)
    entries = []
    for member in members:
        for vacation in member.vacations:
            if vacation.start is not None and vacation.start > today_s:
                entries.append((vacation.start, vacation.end, member))
    entries.sort(key=lambda item: (item[0], item[1], item[2].position))
    return [
        {
            "position": member.position,
            "name": member.get_display_name(),
            "start": start,
            "end": end,
        }
        for start, end, member in entries
    ]


async def add_member(
    daily: DailyRegistry,
    chat_id: int,
    name: str,
    user_id: Optional[int],
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    text = (name or "").strip()
    if not text:
        return ActionResult(
            False, "Пришлите имя участника (например: «Иван @ivanov»)"
        )

    members = await daily.get_members(chat_id)
    if any(m.username == text for m in members):
        return ActionResult(False, "Уже в команде")
    if user_id is not None and any(m.user_id == user_id for m in members):
        return ActionResult(False, "Уже в команде")

    member = DailyMember(
        chat_id=chat_id,
        position=len(members),
        username=text,
        user_id=user_id,
    )
    await daily.add_member(member)
    chat = await daily.get_chat(chat_id)
    if chat is None:
        await daily.upsert_chat(DailyChat(chat_id=chat_id))
    await refresh_schedule(daily, chat_id, today, workdays=workdays)
    return ActionResult(True, member=member)


async def remove_member(
    daily: DailyRegistry,
    chat_id: int,
    position: int,
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    members = await daily.get_members(chat_id)
    if not any(m.position == position for m in members):
        return ActionResult(False, "Участник не найден")
    await daily.remove_member(chat_id, position)
    chat = await daily.get_chat(chat_id)
    if chat is None:
        await daily.upsert_chat(DailyChat(chat_id=chat_id))
    await refresh_schedule(daily, chat_id, today, workdays=workdays)
    return ActionResult(True)


async def move_member(
    daily: DailyRegistry,
    chat_id: int,
    position: int,
    direction: str,
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    if direction not in ("up", "down"):
        return ActionResult(False, "Неверное направление")
    delta = -1 if direction == "up" else 1
    moved = await daily.move_member(chat_id, position, delta)
    if moved:
        await refresh_schedule(daily, chat_id, today, workdays=workdays)
        return ActionResult(True, "Порядок обновлён", moved=True)
    if direction == "up":
        return ActionResult(True, "Уже в начале", moved=False)
    return ActionResult(True, "Уже в конце", moved=False)


async def set_leader_today(
    daily: DailyRegistry,
    chat_id: int,
    position: int,
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    members = await daily.get_members(chat_id)
    chat = await get_chat_or_default(daily, chat_id)
    today_s = str(today)
    new_next, err = set_leader(members, chat.next_index, position, today_s)
    if err is not None:
        return ActionResult(False, err)

    chat.next_index = new_next
    await daily.upsert_chat(chat)
    await refresh_schedule(daily, chat_id, today, workdays=workdays)
    chosen = next((m for m in members if m.position == position), None)
    message = "Сегодня ведёт {}".format(
        chosen.get_display_name(today_s) if chosen is not None else ""
    )
    return ActionResult(True, message, member=chosen, position=position)


async def substitute_today(
    daily: DailyRegistry,
    chat_id: int,
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    members = await daily.get_members(chat_id)
    if not members:
        return ActionResult(False, "Команда пуста. Добавьте участников через /team")
    chat = await get_chat_or_default(daily, chat_id)
    today_s = str(today)
    schedule = await ensure_schedule_for_day(
        daily, chat, members, today, workdays=workdays
    )
    other_s = next_scheduled_date(schedule, today_s)
    if other_s is None:
        return ActionResult(False, "Некого подменять")

    b_pos, a_pos, message, err = apply_substitute(
        schedule, members, today_s, other_s
    )
    if err is not None:
        return ActionResult(False, err)

    await daily.swap_schedule_dates(chat_id, today_s, other_s)
    chat.next_index = b_pos
    await daily.upsert_chat(chat)
    member = next((m for m in members if m.position == b_pos), None)
    return ActionResult(True, message, member=member, position=b_pos)


async def skip_today(
    daily: DailyRegistry,
    chat_id: int,
    position: Optional[int],
    today: datetime.date,
    workdays=None,
) -> ActionResult:
    chat = await daily.get_chat(chat_id)
    members = await daily.get_members(chat_id)
    if chat is None or not members:
        return ActionResult(False, "Команда пуста")
    today_s = str(today)

    target_position = position
    if target_position is None:
        schedule = await ensure_schedule_for_day(
            daily, chat, members, today, workdays=workdays
        )
        leader = leader_for_day(schedule, members, today_s)
        if leader is None:
            return ActionResult(False, "Все пропущены сегодня, дейлик отменён")
        target_position = leader.position

    new_members, new_next, new_leader, err = apply_skip(
        members, chat.next_index, target_position, today_s
    )
    if err is not None:
        return ActionResult(False, err)

    await daily.replace_members(chat_id, new_members)
    chat.next_index = new_next
    await daily.upsert_chat(chat)
    await refresh_schedule(daily, chat_id, today, workdays=workdays)

    if new_leader is None:
        return ActionResult(True, "Все пропущены сегодня, дейлик отменён")
    return ActionResult(
        True,
        "Пропуск принят. Сегодня ведёт {}".format(
            new_leader.get_display_name(today_s)
        ),
        member=new_leader,
        position=new_leader.position,
    )


async def set_daily_time(
    daily: DailyRegistry,
    chat_id: int,
    value: str,
) -> ActionResult:
    text = (value or "").strip()
    if not is_valid_time(text):
        return ActionResult(False, "Неверный формат. Пришлите время в формате ЧЧ:ММ")
    chat = await get_chat_or_default(daily, chat_id)
    chat.daily_time = text
    await daily.upsert_chat(chat)
    return ActionResult(True, "Время дейлика: {}".format(text))


async def update_settings(
    daily: DailyRegistry,
    chat_id: int,
    *,
    daily_time: Optional[str] = None,
    cost_reminder_enabled: Optional[bool] = None,
    cost_reminder_time: Optional[str] = None,
) -> ActionResult:
    if daily_time is not None and not is_valid_time(daily_time):
        return ActionResult(False, "Неверный формат. Пришлите время в формате ЧЧ:ММ")
    if cost_reminder_time is not None and not is_valid_time(cost_reminder_time):
        return ActionResult(False, "Неверный формат. Пришлите время в формате ЧЧ:ММ")

    chat = await get_chat_or_default(daily, chat_id)
    if daily_time is not None:
        chat.daily_time = daily_time.strip()
    if cost_reminder_enabled is not None:
        chat.cost_reminder_enabled = bool(cost_reminder_enabled)
    if cost_reminder_time is not None:
        chat.cost_reminder_time = cost_reminder_time.strip()
    await daily.upsert_chat(chat)
    return ActionResult(True)
