"""Daily standup team model and pure rotation logic (no DB, no aiogram)."""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

VACATION_DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")
ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
VACATION_RANGE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})-(\d{1,2})[./](\d{1,2})[./](\d{4})$")

SCHEDULE_DAYS = 14


@dataclass
class DailyMember:
    chat_id: int
    position: int
    first_name: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    skip_date: Optional[str] = None
    vacation_until: Optional[str] = None
    vacation_start: Optional[str] = None

    def is_skipped(self, today: str) -> bool:
        return self.skip_date == today

    def is_on_vacation(self, today: str) -> bool:
        if not self.vacation_until:
            return False
        if self.vacation_start is None:
            return today <= self.vacation_until
        return self.vacation_start <= today <= self.vacation_until

    def is_unavailable(self, today: str) -> bool:
        return self.is_skipped(today) or self.is_on_vacation(today)

    def _strip_username(self, text: str) -> str:
        """Strip @username from a name string."""
        if self.username:
            return text.replace("@{}".format(self.username), "").strip()
        return text

    @property
    def display_name(self) -> str:
        return self.first_name

    def get_display_name(self, today=None) -> str:
        if today is not None and self.is_on_vacation(today):
            return self._strip_username(self.first_name)
        return self.display_name

    def get_mention(self, today=None) -> str:
        if today is not None and self.is_on_vacation(today):
            return self._strip_username(self.first_name)
        return self.mention

    @property
    def plain_name(self) -> str:
        if self.username:
            stripped = self.first_name.replace("@{}".format(self.username), "").strip()
            return stripped if stripped else self.username
        return self.first_name

    @property
    def mention(self) -> str:
        """Telegram-parseable mention: @username, tg://user link, or plain name.

        The bot sends with HTML parse mode, so members without a username get
        a clickable user link instead of a bare name.
        """
        if self.user_id is not None and not self.username:
            return '<a href="tg://user?id={}">{}</a>'.format(
                self.user_id, escape(self.first_name)
            )
        if self.username:
            return "@{}".format(self.username)
        return self.first_name

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "position": self.position,
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "skip_date": self.skip_date,
            "vacation_until": self.vacation_until,
            "vacation_start": self.vacation_start,
        }

    @classmethod
    def from_dict(cls, dct: dict) -> "DailyMember":
        return cls(
            chat_id=dct["chat_id"],
            position=dct["position"],
            first_name=dct["first_name"],
            user_id=dct.get("user_id"),
            username=dct.get("username"),
            skip_date=dct.get("skip_date"),
            vacation_until=dct.get("vacation_until"),
            vacation_start=dct.get("vacation_start"),
        )


@dataclass
class DailyChat:
    chat_id: int
    daily_time: str = "10:00"
    next_index: int = 0
    last_reminder_date: Optional[str] = None
    last_start_date: Optional[str] = None
    last_catchup_date: Optional[str] = None
    last_advance_date: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "daily_time": self.daily_time,
            "next_index": self.next_index,
            "last_reminder_date": self.last_reminder_date,
            "last_start_date": self.last_start_date,
            "last_catchup_date": self.last_catchup_date,
            "last_advance_date": self.last_advance_date,
        }

    @classmethod
    def from_dict(cls, dct: dict) -> "DailyChat":
        return cls(
            chat_id=dct["chat_id"],
            daily_time=dct.get("daily_time", "10:00"),
            next_index=dct.get("next_index", 0),
            last_reminder_date=dct.get("last_reminder_date"),
            last_start_date=dct.get("last_start_date"),
            last_catchup_date=dct.get("last_catchup_date"),
            last_advance_date=dct.get("last_advance_date"),
        )


def _next_non_skipped(members: List[DailyMember], start_pos: int, today: str) -> Optional[DailyMember]:
    """Scan cyclically from start_pos for the first available member.

    Available = not skipped today and not on vacation. Returns the member
    itself (positions are 0..n-1 in sorted order).
    """
    if not members:
        return None
    n = len(members)
    for offset in range(n):
        member = members[(start_pos + offset) % n]
        if not member.is_unavailable(today):
            return member
    return None


def next_leader(members: List[DailyMember], next_index: int, today: str) -> Optional[DailyMember]:
    """Return the member who leads today, skipping those with skip_date == today."""
    if not members:
        return None
    return _next_non_skipped(members, next_index % len(members), today)


def today_leader(
    members: List[DailyMember],
    next_index: int,
    today: str,
) -> Optional[DailyMember]:
    """Today's leader.

    In the 23:59-advance model next_index points AT today's leader for the
    whole day: the scheduler's nightly pass (23:59) moves it to the next
    workday's leader, so no post-reminder adjustment is needed. Used by /who,
    /daily and /substitute to stay consistent with the announced reminder.
    """
    return next_leader(members, next_index, today)


def advance_next(members: List[DailyMember], leader_pos: int) -> int:
    """New next_index after leader at leader_pos leads: move past them."""
    if not members:
        return 0
    return (leader_pos + 1) % len(members)


async def build_schedule(
    members: List[DailyMember],
    start_date: "datetime.date",
    start_pos: int,
    days: int = SCHEDULE_DAYS,
    *,
    workdays: Optional[Callable[["datetime.date"], Awaitable[bool]]] = None,
) -> List[Tuple["datetime.date", Optional[int]]]:
    """Precompute a rolling leader plan for `days` consecutive days.

    Walks the rotation circle from `start_pos`, one step per workday,
    skipping members that are unavailable on that specific date (vacation
    end-date or skip_date). A day where nobody is available yields a
    (date, None) row so callers can announce "the daily is cancelled".

    A non-workday (per the async `workdays` predicate, duck-typed as
    WorkdayClient.is_workday) yields a (date, None) row and does NOT
    consume a rotation slot — нерабочий день не занимает слот ротации.
    Without a calendar (workdays=None) the fallback is Mon-Fri
    (weekday() < 5), matching the documented README fallback.

    Returns a list of (date, position) in chronological order; the position
    is stable (0..n-1) even for members added by @username (user_id=None).
    """
    if not members:
        return [(start_date + datetime.timedelta(days=i), None) for i in range(days)]
    n = len(members)
    cursor = start_pos % n
    rows = []
    for i in range(days):
        day = start_date + datetime.timedelta(days=i)
        ok = await workdays(day) if workdays is not None else day.weekday() < 5
        if not ok:
            rows.append((day, None))
            continue
        day_s = day.isoformat()
        leader = _next_non_skipped(members, cursor, day_s)
        if leader is None:
            rows.append((day, None))
        else:
            rows.append((day, leader.position))
            cursor = (leader.position + 1) % n
    return rows


def next_scheduled_date(schedule: Dict[str, Optional[int]], after_s: str) -> Optional[str]:
    """Earliest schedule date key strictly greater than `after_s` with a
    non-None position; None when no such date exists."""
    later = [d for d in schedule if d > after_s and schedule[d] is not None]
    return min(later) if later else None


def set_leader(
    members: List[DailyMember],
    next_index: int,
    leader_pos: int,
    today: str,
) -> Tuple[int, Optional[str]]:
    """Manually set today's leader to the member at leader_pos.

    Semantics agreed with the user: the chosen member leads TODAY, the
    rotation continues from the member after them — no queue reordering.
    Implemented by pointing next_index AT the chosen member (same invariant
    as the scheduler's model, where next_index points at today's leader and
    the nightly 23:59 pass advances past them).

    Rejects unavailable members (skipped today or on vacation).

    Returns (new_next_index, error_message|None).
    """
    if not members:
        return next_index, "Команда пуста"
    chosen = None
    for member in members:
        if member.position == leader_pos:
            chosen = member
            break
    if chosen is None:
        return next_index, "Участник не найден"
    if chosen.is_unavailable(today):
        return next_index, "Недоступен сегодня (отпуск или пропуск)"
    return chosen.position, None


def apply_substitute(
    schedule: Dict[str, Optional[int]],
    members: List[DailyMember],
    today: str,
    tomorrow: str,
) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[str]]:
    """Plan a substitute: today's scheduled leader (A) yields to tomorrow's
    scheduled leader (B) by swapping the today/tomorrow schedule rows.

    The schedule is the single source of truth: A = schedule[today],
    B = schedule[tomorrow]. After the swap (the caller persists it via
    swap_schedule_dates), today's leader is B and tomorrow's is A.
    `members` order NEVER changes — positions stay 0..n-1.

    Guards (in order): empty team; no today row (all skipped today); no
    tomorrow row; today and tomorrow scheduled the same member (covers
    n==1 teams); B unavailable today (skip_date == today or vacation
    covering today) — a substitute must be able to lead today.

    Returns (b_pos, a_pos, message, error). On success error is None and
    message is "Сегодня ведёт {B}, в следующий рабочий день {A}" (display
    names). On failure returns (None, None, None, error_message).
    """
    if not members:
        return None, None, None, "Команда пуста"
    a_pos = schedule.get(today)
    if a_pos is None:
        return None, None, None, "Все пропущены сегодня, некого подменять"
    b_pos = schedule.get(tomorrow)
    if b_pos is None:
        return None, None, None, "Некого подменять"
    if a_pos == b_pos:
        return None, None, None, "Некого подменять"
    b_member = next((m for m in members if m.position == b_pos), None)
    if b_member is None or b_member.is_unavailable(today):
        return None, None, None, "Некого подменять"
    a_member = next((m for m in members if m.position == a_pos), None)
    if a_member is None:
        return None, None, None, "Некого подменять"
    return (
        b_pos,
        a_pos,
        "Сегодня ведёт {}, в следующий рабочий день {}".format(b_member.get_display_name(today), a_member.get_display_name(today)),
        None,
    )


def apply_skip(
    members: List[DailyMember],
    next_index: int,
    target_pos: int,
    today: str,
) -> Tuple[List[DailyMember], int, Optional[DailyMember], Optional[str]]:
    """One-time skip: mark the member at the given POSITION skip_date = today.

    The skip button payload carries the leader's position (works even for
    members with user_id=None, added by @username). If the skipped member
    would be today's leader, re-pick the leader from the remaining members
    and point next_index AT them (the nightly 23:59 pass advances past the
    leader, so next_index must keep pointing at today's leader all day).

    Returns (new members, new next_index, new leader|None, error|None).
    """
    if not members:
        return members, next_index, None, "Команда пуста"

    target = None
    for member in members:
        if member.position == target_pos:
            target = member
            break
    if target is None:
        # fallback: allow skipping today's leader by rotation position alone
        leader = next_leader(members, next_index, today)
        if leader is not None:
            target = leader
    if target is None:
        return members, next_index, None, "Участник не найден в команде"
    if target.is_on_vacation(today):
        return members, next_index, None, "Участник в отпуске, пропуск не нужен"

    target.skip_date = today
    new_leader = next_leader(members, next_index, today)
    if new_leader is None:
        return members, next_index, None, None  # all unavailable
    new_next = new_leader.position
    return members, new_next, new_leader, None


def member_list_text(members: List[DailyMember], today: str) -> str:
    if not members:
        return "Команда пуста. Добавьте участников через /team"
    lines = []
    for m in members:
        line = "{}. {}".format(m.position + 1, m.get_display_name(today))
        if m.is_on_vacation(today):
            line += " (в отпуске до {})".format(format_ru_date(m.vacation_until))
        lines.append(line)
    return "\n".join(lines)


def parse_vacation_date(text: str) -> Optional[str]:
    """Parse a vacation end date: 'ДД.ММ.ГГГГ' or 'ГГГГ-ММ-ДД' -> ISO.

    Returns None for anything unparseable. The date is inclusive: the member
    is on vacation while today <= vacation_until.
    """
    if not text:
        return None
    match = VACATION_DATE_RE.match(text.strip())
    if match:
        day, month, year = (int(g) for g in match.groups())
    else:
        match = ISO_DATE_RE.match(text.strip())
        if not match:
            return None
        year, month, day = (int(g) for g in match.groups())
    if not (1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2100):
        return None
    return "{:04d}-{:02d}-{:02d}".format(year, month, day)


def parse_vacation_range(text: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Parse vacation input: 'ДД.ММ.ГГГГ-ДД.ММ.ГГГГ' -> (start_iso, end_iso).

    A single date (RU or ISO) is a legacy end-only vacation -> (None, end_iso).
    Returns None for anything unparseable or when start > end. Both ends are
    inclusive. Only the RU format is accepted for ranges.
    """
    if not text:
        return None
    stripped = text.strip()
    if VACATION_RANGE_RE.match(stripped):
        start_raw, end_raw = stripped.split("-", 1)
        start = parse_vacation_date(start_raw)
        end = parse_vacation_date(end_raw)
        if start is None or end is None or start > end:
            return None
        return start, end
    if not VACATION_DATE_RE.fullmatch(stripped) and not ISO_DATE_RE.fullmatch(stripped):
        return None
    end = parse_vacation_date(stripped)
    if end is None:
        return None
    return None, end


def format_ru_date(iso: str) -> str:
    """ISO 'YYYY-MM-DD' -> human-readable 'ДД.ММ.ГГГГ'."""
    year, month, day = iso.split("-")
    return "{}.{}.{}".format(day, month, year)


async def is_today_workday(workday_client, tz) -> bool:
    """True if today (in tz) is a workday per the client's calendar."""
    today = today_in_tz(tz)
    return await workday_client.is_workday(today)


def today_in_tz(tz) -> "datetime.date":
    """Today's date in the given timezone (datetime.date)."""
    import datetime
    return datetime.datetime.now(tz).date()
