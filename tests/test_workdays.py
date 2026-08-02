"""WorkdayClient tests with a mocked aiohttp session."""
import datetime
from typing import Optional

import aiohttp
import pytest

from ppbot.workdays import WorkdayClient


class FakeAiohttpResponse:
    def __init__(self, text, status=200, raises=False):
        self._text = text
        self.status = status
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self._raises:
            raise aiohttp.ClientError("boom")

    async def text(self):
        return self._text


class FakeAiohttpSession:
    """Stands in for aiohttp.ClientSession.get()."""

    def __init__(self):
        self.responses = []  # queue of FakeAiohttpResponse
        self.requests = []

    def get(self, url, **kwargs):
        # aiohttp's ClientSession.get is a coroutine; our client uses
        # `async with session.get(url)`, so the fake returns the CM directly.
        self.requests.append(url)
        if self.responses:
            return self.responses.pop(0)
        raise aiohttp.ClientConnectionError("no route")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def client():
    return WorkdayClient(session=FakeAiohttpSession())


@pytest.mark.asyncio
async def test_is_workday_parses_31_char_string():
    # 2026-08-01 (Sat) is non-workday '1', 2026-08-03 (Mon) is '0'
    data = "1100000110000011000001100000110"
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse(data))
    client = WorkdayClient(session=session)

    assert await client.is_workday(datetime.date(2026, 8, 3)) is True
    assert await client.is_workday(datetime.date(2026, 8, 1)) is False
    # cached - no second request
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_error_falls_back_to_weekday():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("", status=500, raises=True))
    client = WorkdayClient(session=session)

    # 2026-08-03 is a Monday -> workday by fallback
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True
    # 2026-08-02 is a Sunday -> not workday by fallback
    assert await client.is_workday(datetime.date(2026, 8, 2)) is False


@pytest.mark.asyncio
async def test_connection_error_falls_back():
    session = FakeAiohttpSession()  # no responses queued -> raises
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True  # Monday


@pytest.mark.asyncio
async def test_short_string_falls_back_for_missing_days():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("1"))  # 1 char for day 1 only
    client = WorkdayClient(session=session)

    # day 1: '1' -> not a workday
    assert await client.is_workday(datetime.date(2026, 8, 1)) is False
    # day 3 missing -> weekday fallback (Monday -> workday)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True


@pytest.mark.asyncio
async def test_cache_ttl_behavior():
    import time

    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("1100000110000011000001100000110"))
    client = WorkdayClient(session=session, ttl=1000)

    assert await client.is_workday(datetime.date(2026, 8, 3)) is True
    assert len(session.requests) == 1
    # force cache expiry
    client._cache[(2026, 8)] = (time.time() - 5000, "1100000110000011000001100000110")
    session.responses.append(FakeAiohttpResponse("1100000110000011000001100000110"))
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_non_200_falls_back():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("", status=404, raises=True))
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True  # Monday fallback


@pytest.mark.asyncio
async def test_is_today_workday_integration():
    from zoneinfo import ZoneInfo

    from ppbot.daily import is_today_workday, today_in_tz

    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("1100000110000011000001100000110"))
    client = WorkdayClient(session=session)
    tz = ZoneInfo("Europe/Moscow")

    today = today_in_tz(tz)
    expected = today.weekday() < 5
    if len("1100000110000011000001100000110") >= today.day:
        expected = "1100000110000011000001100000110"[today.day - 1] == "0"

    assert await is_today_workday(client, tz) is expected
