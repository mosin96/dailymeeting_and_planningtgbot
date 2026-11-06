"""Workday calendar client: public holidays from date.nager.at with fallback."""
from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Dict, FrozenSet, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h
FAILURE_RETRY_SECONDS = 5 * 60  # don't hammer a failing API every 30s
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_COUNTRY = "RU"


class WorkdayClient:
    """Public-holiday calendar backed by the international Nager.Date API.

    A day is a workday when it is Mon-Fri and not a public holiday of the
    configured country (ISO 3166-1 alpha-2 code, `PP_WORKDAY_COUNTRY`,
    default `RU`). Holiday sets are cached per year with a 24h TTL; API
    failures fall back to plain Mon-Fri and are negatively cached for a few
    minutes so the 30-second scheduler loop does not flood the logs/API.

    Note: public-holiday services know official holidays but not ad-hoc
    government transfers of days off and working weekends ("переносы"), so
    such days are treated as regular weekdays.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        ttl: int = CACHE_TTL_SECONDS,
        country: Optional[str] = None,
    ):
        self._session = session
        self._owns_session = session is None
        self._ttl = ttl
        self._country = (country or os.environ.get("PP_WORKDAY_COUNTRY", DEFAULT_COUNTRY)).strip().upper()
        # key: (year, country) -> (fetched_at_epoch, {holiday ISO dates})
        self._cache: Dict[Tuple[int, str], Tuple[float, FrozenSet[str]]] = {}
        # key: (year, country) -> epoch of the last failed attempt
        self._failed_at: Dict[Tuple[int, str], float] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            )
        return self._session

    async def _fetch_year(self, year: int) -> Optional[FrozenSet[str]]:
        """Public holiday dates for the configured country/year.

        Returns None when the API is unavailable or returned unusable data
        (callers fall back to Mon-Fri).
        """
        key = (year, self._country)
        now = datetime.datetime.now().timestamp()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        failed_at = self._failed_at.get(key)
        if failed_at is not None and now - failed_at < FAILURE_RETRY_SECONDS:
            return None

        session = await self._ensure_session()
        url = NAGER_URL.format(year=year, country=self._country)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            ) as resp:
                resp.raise_for_status()
                raw = await resp.text()
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("unexpected payload shape")
            holidays = frozenset(
                item["date"]
                for item in payload
                if isinstance(item, dict) and isinstance(item.get("date"), str)
            )
        except (aiohttp.ClientError, ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning(
                "public holidays request failed for %s (%s): %s", year, self._country, exc
            )
            self._failed_at[key] = now
            return None

        self._cache[key] = (now, holidays)
        self._failed_at.pop(key, None)
        return holidays

    async def is_workday(self, date: datetime.date) -> bool:
        """True if the given date is a workday (Mon-Fri, not a public holiday).

        Fallback: Mon-Fri when the API is unavailable.
        """
        if date.weekday() >= 5:
            return False
        holidays = await self._fetch_year(date.year)
        if holidays is None:
            return True
        return date.isoformat() not in holidays

    async def close(self) -> None:
        """Close the internally created session (a shared one is left alone)."""
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None
