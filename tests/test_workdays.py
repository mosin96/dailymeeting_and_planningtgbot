"""WorkdayClient tests with a mocked aiohttp session (Nager.Date API)."""
import datetime
import json

import aiohttp
import pytest

from ppbot.workdays import WorkdayClient

RU_2026 = [
    {"date": "2026-01-01", "countryCode": "RU", "global": True},
    {"date": "2026-01-02", "countryCode": "RU", "global": True},
    {"date": "2026-01-07", "countryCode": "RU", "global": True},
    {"date": "2026-02-23", "countryCode": "RU", "global": True},
    {"date": "2026-03-08", "countryCode": "RU", "global": True},
    {"date": "2026-05-01", "countryCode": "RU", "global": True},
    {"date": "2026-05-09", "countryCode": "RU", "global": True},
    {"date": "2026-06-12", "countryCode": "RU", "global": True},
    {"date": "2026-11-04", "countryCode": "RU", "global": True},
]
RU_2026_JSON = json.dumps(RU_2026)


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


@pytest.fixture
def client():
    return WorkdayClient(session=FakeAiohttpSession())


@pytest.mark.asyncio
async def test_is_workday_uses_public_holidays():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse(RU_2026_JSON))
    client = WorkdayClient(session=session)

    # 2026-01-01 (Thu) and 2026-01-02 (Fri) are public holidays
    assert await client.is_workday(datetime.date(2026, 1, 1)) is False
    assert await client.is_workday(datetime.date(2026, 1, 2)) is False
    # 2026-01-05 (Mon) is a regular weekday
    assert await client.is_workday(datetime.date(2026, 1, 5)) is True
    # 2026-01-10 is a Saturday
    assert await client.is_workday(datetime.date(2026, 1, 10)) is False
    # 2026-11-04 (Wed) is Unity Day
    assert await client.is_workday(datetime.date(2026, 11, 4)) is False
    # cached - no second request
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_request_targets_nager_with_country_and_year():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse(RU_2026_JSON))
    client = WorkdayClient(session=session)

    await client.is_workday(datetime.date(2026, 1, 5))
    assert session.requests == [
        "https://date.nager.at/api/v3/PublicHolidays/2026/RU"
    ]


@pytest.mark.asyncio
async def test_country_comes_from_argument_and_env(monkeypatch):
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("[]"))
    client = WorkdayClient(session=session, country="by")
    await client.is_workday(datetime.date(2026, 1, 5))
    assert session.requests[-1].endswith("/2026/BY")

    monkeypatch.setenv("PP_WORKDAY_COUNTRY", "kz")
    session2 = FakeAiohttpSession()
    session2.responses.append(FakeAiohttpResponse("[]"))
    client2 = WorkdayClient(session=session2)
    await client2.is_workday(datetime.date(2026, 1, 5))
    assert session2.requests[-1].endswith("/2026/KZ")


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
async def test_connection_error_falls_back_and_caches_failure():
    session = FakeAiohttpSession()  # no responses queued -> raises
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True  # Monday
    # negative cache: the next call within FAILURE_RETRY_SECONDS does not retry
    assert await client.is_workday(datetime.date(2026, 8, 4)) is True
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_invalid_json_falls_back():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("<html>maintenance</html>"))
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True  # Monday


@pytest.mark.asyncio
async def test_unexpected_payload_shape_falls_back():
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse('{"error": "not found"}'))
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True


@pytest.mark.asyncio
async def test_empty_holiday_list_is_valid():
    """Some countries/years legitimately have no public holidays."""
    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse("[]"))
    client = WorkdayClient(session=session)
    assert await client.is_workday(datetime.date(2026, 8, 3)) is True
    assert await client.is_workday(datetime.date(2026, 8, 2)) is False


@pytest.mark.asyncio
async def test_cache_ttl_behavior():
    import time

    session = FakeAiohttpSession()
    session.responses.append(FakeAiohttpResponse(RU_2026_JSON))
    client = WorkdayClient(session=session, ttl=1000)

    assert await client.is_workday(datetime.date(2026, 1, 5)) is True
    assert len(session.requests) == 1
    # force cache expiry
    client._cache[(2026, "RU")] = (time.time() - 5000, frozenset())
    session.responses.append(FakeAiohttpResponse(RU_2026_JSON))
    assert await client.is_workday(datetime.date(2026, 1, 1)) is False
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
    session.responses.append(FakeAiohttpResponse(RU_2026_JSON))
    client = WorkdayClient(session=session)
    tz = ZoneInfo("Europe/Moscow")

    today = today_in_tz(tz)
    holidays = {item["date"] for item in RU_2026}
    expected = today.weekday() < 5 and today.isoformat() not in holidays

    assert await is_today_workday(client, tz) is expected
