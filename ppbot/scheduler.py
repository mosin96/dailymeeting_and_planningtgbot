"""Reminder scheduler: asyncio loop that runs the daily standup events.

Model: next_index points AT the current day's leader. A nightly pass at 23:59
advances the rotation (once per workday). Per workday two messages go out:
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
    DailyChat,
    advance_next,
    format_ru_date,
    next_leader,
    today_in_tz,
)
from ppbot.daily_storage import DailyRegistry

logger = logging.getLogger(__name__)

REMIND_BEFORE_MINUTES = 15
START_GRACE_MINUTES = 60
META_ADVANCE_V2 = "daily_advance_v2"

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


def should_advance_index(chat: DailyChat, now: datetime.datetime, today: datetime.date, is_workday: bool) -> bool:
    """True when the 23:59 rotation advance is due for this chat today."""
    if not is_workday or chat.last_advance_date == str(today):
        return False
    return now >= datetime.datetime.combine(today, datetime.time(23, 59))


async def missing_advances(workday_client, last_advance_date: Optional[str], today: datetime.date) -> int:
    """Workdays strictly between last_advance_date and today (missed 23:59 passes)."""
    if last_advance_date is None:
        return 0
    start = datetime.date.fromisoformat(last_advance_date)
    count = 0
    d = start + datetime.timedelta(days=1)
    guard = 0
    while d < today and guard < 400:
        if await workday_client.is_workday(d):
            count += 1
        d += datetime.timedelta(days=1)
        guard += 1
    return count


async def _process_chat(bot: Bot, storage: DailyRegistry, workday_client, chat: DailyChat, now: datetime.datetime, today: datetime.date, is_workday: bool):
    today_s = str(today)

    if chat.last_catchup_date != today_s:
        missing = await missing_advances(workday_client, chat.last_advance_date, today)
        if missing:
            members = await storage.get_members(chat.chat_id)
            if members:
                chat.next_index = (chat.next_index + missing) % len(members)
        chat.last_catchup_date = today_s
        await storage.upsert_chat(chat)

    members = await storage.get_members(chat.chat_id)
    leader = next_leader(members, chat.next_index, today_s)

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

    if should_advance_index(chat, now, today, is_workday):
        if leader is not None:
            chat.next_index = advance_next(members, leader.position)
        chat.last_advance_date = today_s
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
                    await _process_chat(bot, storage, workday_client, chat, current, today, is_workday)
                except Exception:
                    logger.exception("chat %s processing failed", chat.chat_id)
        except Exception:
            logger.exception("reminder loop iteration failed")
        await asyncio.sleep(interval)


async def migrate_advance_semantics(daily: DailyRegistry, tz) -> None:
    """One-time migration to the 23:59-advance model (idempotent via daily_meta).

    Old chats have next_index in the post-reminder semantics: once today's
    reminder had fired, next_index pointed PAST today's leader. Step such
    chats back to today's leader; the next 23:59 pass converges them to the
    new model. last_advance_date = yesterday marks a chat as migrated while
    still allowing tonight's advance.
    """
    if await daily.get_meta(META_ADVANCE_V2) is not None:
        return
    today = today_in_tz(tz)
    yesterday = today - datetime.timedelta(days=1)
    for chat in await daily.list_chats():
        if chat.last_advance_date is not None:
            continue
        if chat.last_reminder_date == str(today):
            members = await daily.get_members(chat.chat_id)
            if members:
                chat.next_index = (chat.next_index - 1) % len(members)
        chat.last_advance_date = str(yesterday)
        chat.last_catchup_date = str(yesterday)
        await daily.upsert_chat(chat)
    await daily.set_meta(META_ADVANCE_V2, str(today))
