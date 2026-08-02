"""Workday calendar client: isdayoff.ru (RU production calendar) with fallback."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

ISDAYOFF_URL = "https://isdayoff.ru/api/getdata"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h


class WorkdayClient:
    """Fetches isdayoff.ru data per month; caches with TTL; falls back to Mon-Fri."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None, ttl: int = CACHE_TTL_SECONDS):
        self._session = session
        self._ttl = ttl
        # key: (year, month) -> (fetched_at_epoch, data_string)
        self._cache: Dict[Tuple[int, int], Tuple[float, str]] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        return self._session

    async def _fetch_month(self, year: int, month: int) -> str:
        """Return isdayoff string ('0' workday / '1' non-workday), one char per day."""
        key = (year, month)
        now = datetime.datetime.now().timestamp()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        session = await self._ensure_session()
        url = "{url}?year={year}&month={month}&cc=ru".format(
            url=ISDAYOFF_URL, year=year, month=month
        )
        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                text = (await resp.text()).strip()
        except (aiohttp.ClientError, ValueError, OSError) as exc:
            logger.warning("isdayoff request failed for %s-%s: %s", year, month, exc)
            text = ""

        if not text or not all(c in "01" for c in text):
            logger.warning("isdayoff returned unusable data for %s-%s", year, month)
            return ""

        self._cache[key] = (now, text)
        return text

    async def is_workday(self, date: datetime.date) -> bool:
        """True if the given date is a workday per the RU production calendar.

        Fallback: Mon-Fri when the API is unavailable or data is incomplete.
        """
        text = await self._fetch_month(date.year, date.month)
        if len(text) >= date.day:
            return text[date.day - 1] == "0"
        # data missing/short for this day -> weekday fallback
        return date.weekday() < 5
