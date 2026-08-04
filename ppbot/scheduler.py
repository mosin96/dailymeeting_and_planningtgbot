"""Reminder scheduler: asyncio loop that runs the daily standup events.

Model: a permanently stored 14-day leader schedule (daily_schedule table)
pre-computes who leads which date by walking the rotation circle and skipping
vacation/skip per date. Each loop keeps the window covering today..today+14:
adds the new 14th-day leader and trims leaders older than 14 days. Per
workday two messages go out:
  1. 15 minutes before the daily: tag the leader ("Сегодня ведущий - @nick");
  2. at the daily time: announce the daily has started.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Callable, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from ppbot.daily import (
    SCHEDULE_DAYS,
    DailyChat,
    build_schedule,
    format_ru_date,
    today_in_tz,
)
from ppbot.daily_storage import DailyRegistry

logger = logging.getLogger(__name__)

REMIND_BEFORE_MINUTES = 15
START_GRACE_MINUTES = 60
META_SCHEDULE_V1 = "daily_schedule_v1"

START_TEXT = "Дейлик начинается, всех ждем!"


def parse_time(value: str) -> datetime.time:
    hour, minute = value.split(":")
    return datetime.time(int(hour), int(minute))


def reminder_text(leader, members, today) -> str:
    text = "Сегодня ведущий - {}".format(leader.mention)
    vacationers = [m for m in members if m.is_on_vacation(today)]
    if vacationers:
        parts = ", ".join(
            "{} (до {})".format(m.plain_name, format_ru_date(m.vacation_until))
            for m in vacationers
        )
        text += "\nВ отпуске: {}".format(parts)
    return text


def should_send_reminder(chat: DailyChat, now: datetime.datetime, today: datetime.date, is_workday: bool) -> bool:
    """True when the 15-minutes-before tag is due right now."""
    if not is_workday or chat.last_reminder_date == str(today):
        return False
    daily_dt = datetime.datetime.combine(today, parse_time(chat.daily_time))
    remind_dt = daily_dt - datetime.timedelta(minutes=REMIND_BEFORE_MINUTES)
    return remind_dt <= now < daily_dt


def should_send_start(chat: DailyChat, now: datetime.datetime, today: datetime.date, is_workday: bool) -> bool:
    """True when the 'daily starts now' message is due (within a grace window)."""
    if not is_workday or chat.last_start_date == str(today):
        return False
    daily_dt = datetime.datetime.combine(today, parse_time(chat.daily_time))
    return daily_dt <= now < daily_dt + datetime.timedelta(minutes=START_GRACE_MINUTES)


async def _process_chat(bot: Bot, storage: DailyRegistry, chat: DailyChat, now: datetime.datetime, today: datetime.date, is_workday: bool):
    today_s = str(today)

    members = await storage.get_members(chat.chat_id)
    await storage.ensure_schedule(chat, members, today)
    schedule = await storage.get_schedule(chat.chat_id)
    position = schedule.get(today_s)
    leader = next((m for m in members if m.position == position), None) if position is not None else None

    if should_send_reminder(chat, now, today, is_workday):
        try:
            if leader is None:
                await bot.send_message(chat_id=chat.chat_id, text="Все пропущены сегодня, дейлик отменён")
            else:
                from ppbot.daily_ui import build_reminder_markup

                await bot.send_message(
                    chat_id=chat.chat_id,
                    text=reminder_text(leader, members, today_s),
                    reply_markup=build_reminder_markup(leader),
                )
        except TelegramBadRequest as exc:
            logger.warning("chat %s unavailable: %s", chat.chat_id, exc)
        chat.last_reminder_date = today_s
        await storage.upsert_chat(chat)

    if should_send_start(chat, now, today, is_workday):
        try:
            if leader is not None:
                await bot.send_message(chat_id=chat.chat_id, text=START_TEXT)
        except TelegramBadRequest as exc:
            logger.warning("chat %s unavailable: %s", chat.chat_id, exc)
        chat.last_start_date = today_s
        await storage.upsert_chat(chat)


async def reminder_loop(
    bot: Bot,
    storage: DailyRegistry,
    workday_client,
    tz,
    interval: int = 30,
    now: Optional[Callable[[], datetime.datetime]] = None,
):
    """Loop over chats every `interval` seconds and run due daily events.

    `now` is injectable for tests (fake time).
    """
    if now is None:
        now = lambda: datetime.datetime.now(tz)  # noqa: E731

    while True:
        try:
            current = now()
            # Production clock (datetime.now(tz)) is tz-aware, but the
            # should_send_* decision functions build naive windows via
            # datetime.combine(). Normalize once here so the comparisons
            # never raise the aware-vs-naive TypeError (which the broad
            # except below would otherwise swallow, silently killing the
            # reminder/start/advance in production).
            current = current.replace(tzinfo=None)
            today = current.date()
            is_workday = await workday_client.is_workday(today)
            for chat in await storage.list_chats():
                try:
                    await _process_chat(bot, storage, chat, current, today, is_workday)
                except Exception:
                    logger.exception("chat %s processing failed", chat.chat_id)
        except Exception:
            logger.exception("reminder loop iteration failed")
        await asyncio.sleep(interval)


async def migrate_schedule_model(daily: DailyRegistry, tz) -> None:
    """One-time seeding of the 14-day leader schedule (idempotent via daily_meta).

    Old chats carry only next_index (pointing at today's leader). Seed a fresh
    daily_schedule window from today so the schedule table becomes the source
    of truth; afterwards the scheduler only extends/trims it.
    """
    if await daily.get_meta(META_SCHEDULE_V1) is not None:
        return
    today = today_in_tz(tz)
    for chat in await daily.list_chats():
        members = await daily.get_members(chat.chat_id)
        if not members:
            continue
        rows = build_schedule(members, today, chat.next_index, SCHEDULE_DAYS + 1)
        await daily.set_schedule(chat.chat_id, [(d.isoformat(), p) for d, p in rows])
    await daily.set_meta(META_SCHEDULE_V1, str(today))
