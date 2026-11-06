"""Mini-app HTTP API tests: auth, membership gate, vacation add/delete."""
import datetime
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ppbot.daily import DailyMember
from ppbot.daily_storage import DailyRegistry
from ppbot.webapp import create_app

TOKEN = "12345:test-token"
TZ = ZoneInfo("UTC")


def make_init_data(user_id=777, token=TOKEN, age_seconds=0, start_param=None):
    """Build a properly signed Telegram WebApp initData string."""
    user = {"id": user_id, "first_name": "Test"}
    ts = int(
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_seconds)).timestamp()
    )
    params = {"query_id": "AAA", "user": json.dumps(user), "auth_date": str(ts)}
    if start_param is not None:
        params["start_param"] = str(start_param)
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(sorted({**params, "hash": signature}.items()))


@pytest.fixture
async def client(tmp_path):
    db = tmp_path / "daily.db"
    storage = DailyRegistry()
    await storage.init_db(str(db))
    app = create_app(storage, TZ, TOKEN, workdays=None)
    server = TestServer(app)
    await server.start_server()
    client = TestClient(server)
    yield client, storage
    await client.close()
    await server.close()
    await storage.close()


def _seed_member(storage, chat_id=-1001, position=0, user_id=777, name="Иван"):
    return storage.add_member(DailyMember(chat_id=chat_id, position=position, user_id=user_id, username=name))


def _headers(init_data):
    return {"X-Telegram-Init-Data": init_data}


@pytest.mark.asyncio
async def test_static_files_exist():
    from ppbot.webapp import STATIC_DIR

    names = {m.name for m in os.scandir(STATIC_DIR)}
    assert {"index.html", "app.js", "style.css"} <= names
    index = open(STATIC_DIR + "/index.html", encoding="utf-8").read()
    assert "app.js" in index


@pytest.mark.asyncio
async def test_index_served(client):
    c, storage = client
    resp = await c.get("/")
    assert resp.status == 200
    body = await resp.text()
    assert "Отпуска" in body


@pytest.mark.asyncio
async def test_state_returns_members_and_vacations(client):
    c, storage = client
    await _seed_member(storage)
    await storage.add_vacation(-1001, 0, "2026-12-01", "2026-12-10")
    resp = await c.get(
        "/api/state?chat=-1001",
        headers=_headers(make_init_data(user_id=777)),
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["chat"] == -1001
    assert "today" in data
    assert len(data["members"]) == 1
    member = data["members"][0]
    assert member["position"] == 0 and member["name"] == "Иван"
    assert member["vacations"] == [{"id": member["vacations"][0]["id"], "start": "2026-12-01", "end": "2026-12-10"}]


@pytest.mark.asyncio
async def test_state_works_with_start_param_and_no_query_chat(client):
    """Mini App deep link carries the chat in the signed start_param
    (t.me/<bot>?startapp=<chat_id>); the page URL has no ?chat= then."""
    c, storage = client
    await _seed_member(storage)
    await storage.add_vacation(-1001, 0, "2026-12-01", "2026-12-10")
    resp = await c.get(
        "/api/state",
        headers=_headers(make_init_data(user_id=777, start_param="-1001")),
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["chat"] == -1001
    assert data["members"][0]["vacations"][0]["end"] == "2026-12-10"


@pytest.mark.asyncio
async def test_start_param_takes_precedence_over_query(client):
    """A forged ?chat= cannot redirect the request away from the signed chat."""
    c, storage = client
    await _seed_member(storage, chat_id=-1001, user_id=777)
    await _seed_member(storage, chat_id=-2002, position=0, user_id=42, name="Друзья")
    resp = await c.get(
        "/api/state?chat=-2002",
        headers=_headers(make_init_data(user_id=777, start_param="-1001")),
    )
    assert resp.status == 200
    assert (await resp.json())["chat"] == -1001


@pytest.mark.asyncio
async def test_post_with_start_param_chat(client):
    """POST /api/vacations works from the deep-link flow: the signed
    start_param supplies the chat even when the body has no chat field."""
    c, storage = client
    await _seed_member(storage)
    from ppbot.daily import today_in_tz

    today = today_in_tz(TZ)
    start = today + datetime.timedelta(days=10)
    end = today + datetime.timedelta(days=15)
    resp = await c.post(
        "/api/vacations",
        json={"position": 0, "start": start.isoformat(), "end": end.isoformat()},
        headers=_headers(make_init_data(user_id=777, start_param="-1001")),
    )
    assert resp.status == 200
    assert len(await storage.get_vacations(-1001)) == 1


@pytest.mark.asyncio
async def test_missing_init_data_unauthorized(client):
    c, storage = client
    await _seed_member(storage)
    resp = await c.get("/api/state?chat=-1001")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_bad_signature_unauthorized(client):
    c, storage = client
    await _seed_member(storage)
    resp = await c.get("/api/state?chat=-1001", headers=_headers("user=%7B%7D&hash=deadbeef"))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_stale_init_data_unauthorized(client):
    c, storage = client
    await _seed_member(storage)
    old = make_init_data(user_id=777, age_seconds=25 * 3600)
    resp = await c.get("/api/state?chat=-1001", headers=_headers(old))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_non_member_forbidden(client):
    """initData valid, but 999 is not in the roster -> 403."""
    c, storage = client
    await _seed_member(storage, user_id=777)
    resp = await c.get("/api/state?chat=-1001", headers=_headers(make_init_data(user_id=999)))
    assert resp.status == 403


@pytest.mark.asyncio
async def test_missing_chat_param_rejected(client):
    c, storage = client
    await _seed_member(storage)
    resp = await c.get("/api/state", headers=_headers(make_init_data(user_id=777)))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_add_vacation(client):
    c, storage = client
    await _seed_member(storage)
    from ppbot.daily import today_in_tz

    today = today_in_tz(TZ)
    start = today + datetime.timedelta(days=10)
    end = today + datetime.timedelta(days=15)
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 0, "start": start.isoformat(), "end": end.isoformat()},
        headers=_headers(make_init_data(user_id=777)),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["start"] == start.isoformat()
    members = await storage.get_members(-1001)
    assert members[0].is_on_vacation((start + datetime.timedelta(days=1)).isoformat()) is True


@pytest.mark.asyncio
async def test_add_vacation_rebuilds_schedule(client):
    """After adding a vacation the persisted schedule window is rebuilt (the
    vacationer is not scheduled while away)."""
    c, storage = client
    # Ivan (position 0) would lead today; seed schedule without vacations
    from ppbot.daily import DailyChat
    from ppbot.daily import today_in_tz

    await storage.upsert_chat(DailyChat(chat_id=-1001))
    await _seed_member(storage)

    today = today_in_tz(TZ)
    start = today.isoformat()
    end = (today + datetime.timedelta(days=5)).isoformat()
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 0, "start": start, "end": end},
        headers=_headers(make_init_data(user_id=777)),
    )
    assert resp.status == 200
    schedule = await storage.get_schedule(-1001)
    # Monday-first fallback: Ivan is on vacation the whole window -> nobody
    # available (single-member team), so every workday row is None.
    day = today
    checked = 0
    while day <= today + datetime.timedelta(days=4):
        if day.weekday() < 5:
            assert schedule[day.isoformat()] is None, day
            checked += 1
        day += datetime.timedelta(days=1)
    assert checked > 0


@pytest.mark.asyncio
async def test_add_vacation_date_validation(client):
    c, storage = client
    await _seed_member(storage)
    headers = _headers(make_init_data(user_id=777))
    good_start = "2026-12-01"

    # bad format
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 0, "start": "01.12.2026", "end": "2026-12-10"},
        headers=headers,
    )
    assert resp.status == 400

    # inverted range
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 0, "start": "2026-12-10", "end": good_start},
        headers=headers,
    )
    assert resp.status == 400

    # past end
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 0, "start": "2020-01-01", "end": "2020-01-05"},
        headers=headers,
    )
    assert resp.status == 400

    # unknown position
    resp = await c.post(
        "/api/vacations",
        json={"chat": -1001, "position": 9, "start": good_start, "end": "2026-12-10"},
        headers=headers,
    )
    assert resp.status == 400

    assert await storage.get_vacations(-1001) == []


@pytest.mark.asyncio
async def test_delete_vacation(client):
    c, storage = client
    await _seed_member(storage)
    from ppbot.daily import today_in_tz

    today = today_in_tz(TZ)
    v = await storage.add_vacation(-1001, 0, today.isoformat(), (today + datetime.timedelta(days=4)).isoformat())
    resp = await c.delete(
        f"/api/vacations/{v.id}?chat=-1001",
        headers=_headers(make_init_data(user_id=777)),
    )
    assert resp.status == 200
    assert await storage.get_vacations(-1001) == []

    # deleting an id from another chat is a 404
    await storage.add_member(DailyMember(chat_id=-2002, position=0, user_id=777, username="И"))
    v2 = await storage.add_vacation(-2002, 0, today.isoformat(), today.isoformat())
    resp = await c.delete(
        f"/api/vacations/{v2.id}?chat=-1001",
        headers=_headers(make_init_data(user_id=777)),
    )
    assert resp.status == 404
    assert len(await storage.get_vacations(-2002)) == 1


@pytest.mark.asyncio
async def test_api_rejects_chat_of_other_member(client):
    """The requester must belong to the requested chat, even when the chat id
    in the query points elsewhere."""
    c, storage = client
    await _seed_member(storage, chat_id=-1001, user_id=777)
    await _seed_member(storage, chat_id=-2002, position=0, user_id=42, name="Друзья")
    resp = await c.get("/api/state?chat=-2002", headers=_headers(make_init_data(user_id=777)))
    assert resp.status == 403


@pytest.mark.asyncio
async def test_static_index_and_assets(client):
    c, storage = client
    for path, needle in (
        ("/", "app.js"),
        ("/static/app.js", "loadState"),
        ("/static/style.css", "month-title"),
    ):
        resp = await c.get(path)
        assert resp.status == 200, path
        body = await resp.text()
        assert needle in body, path
