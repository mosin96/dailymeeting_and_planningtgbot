"""Telegram mini-app HTTP server for the team vacation calendar.

Serves the static web UI (ppbot/webapp_static/) and a small JSON API.
Every request must carry the Telegram WebApp ``initData`` payload in the
``X-Telegram-Init-Data`` header; the signature is checked against the bot
token and the sender must be a member of the daily_members roster of the
requested chat (the chat id comes from the query string, so membership --
not the query -- is the authorization decision).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import ssl
from typing import Optional, Tuple

from aiohttp import web

from ppbot.daily import DailyMember, today_in_tz
from ppbot.daily_service import (
    add_member,
    build_overview,
    get_chat_or_default,
    move_member,
    planned_vacations,
    remove_member,
    set_leader_today,
    skip_today,
    substitute_today,
    update_settings,
)
from ppbot.daily_storage import DailyRegistry, refresh_schedule_window
from ppbot.daily_ui import GREETING_HELP

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "webapp_static")


def _asset_version(filename: str) -> str:
    """Cache-busting token for a static file (mtime + size)."""
    try:
        stat = os.stat(os.path.join(STATIC_DIR, filename))
    except OSError:
        return "0"
    return "{:x}-{:x}".format(stat.st_mtime_ns, stat.st_size)


@web.middleware
async def _no_cache_static(request: web.Request, handler):
    """Never let Telegram's WebView keep an outdated mini-app shell/assets."""
    response = await handler(request)
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# initData can be replayed; accept only recent payloads.
INIT_DATA_MAX_AGE = datetime.timedelta(hours=24)
INIT_DATA_FUTURE_TOLERANCE = datetime.timedelta(minutes=5)
ISO_DATE_LEN = 10


def _is_valid_iso_date(value: str) -> bool:
    if not isinstance(value, str) or len(value) != ISO_DATE_LEN:
        return False
    try:
        y, m, d = value.split("-")
        datetime.date(int(y), int(m), int(d))
        return True
    except (ValueError, TypeError):
        return False


def _init_data_age(data) -> Optional[datetime.timedelta]:
    auth_date = getattr(data, "auth_date", None)
    if not isinstance(auth_date, datetime.datetime):
        return None
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) - auth_date


async def _telegram_is_chat_member(token: str, chat_id: int, user_id: int) -> bool:
    """Ask Telegram whether *user_id* currently belongs to *chat_id*.

    Used only by the "add me" flow: a new roster member has no record yet, so
    the roster itself cannot authorize them. Any other mini-app endpoint still
    requires an existing roster row.
    """
    try:
        import aiohttp

        url = "https://api.telegram.org/bot{}/getChatMember".format(token)
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, params={"chat_id": chat_id, "user_id": user_id}
            ) as response:
                payload = await response.json(content_type=None)
    except Exception:
        logger.warning("could not verify chat membership", exc_info=True)
        return False
    if not payload.get("ok"):
        logger.warning("getChatMember failed: %s", payload.get("description"))
        return False
    result = payload.get("result") or {}
    status = result.get("status")
    if status in ("creator", "administrator", "member"):
        return True
    if status == "restricted":
        return bool(result.get("is_member"))
    return False


def create_app(
    daily: DailyRegistry,
    tz,
    token: str,
    workdays=None,
    membership_checker=None,
) -> web.Application:
    app = web.Application(middlewares=[_no_cache_static], client_max_size=64 * 1024)

    async def index(request: web.Request) -> web.Response:
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        # Versioned URLs guarantee that a new app.js/style.css is fetched even
        # if the WebView cached the shell or the assets themselves.
        html = html.replace(
            "/static/app.js", "/static/app.js?v={}".format(_asset_version("app.js"))
        )
        html = html.replace(
            "/static/style.css", "/static/style.css?v={}".format(_asset_version("style.css"))
        )
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def api_state(request: web.Request) -> web.Response:
        chat_id, member, _ = await _authenticate(request, daily, request.query.get("chat"))
        members = await daily.get_members(chat_id)
        today = today_in_tz(tz)
        settings = await get_chat_or_default(daily, chat_id)
        overview = await build_overview(daily, chat_id, today, workdays=workdays)
        payload = {
            "chat": chat_id,
            "today": str(today),
            "me": {"position": member.position, "name": member.username},
            "settings": {
                "daily_time": settings.daily_time,
                "cost_reminder_enabled": settings.cost_reminder_enabled,
                "cost_reminder_time": settings.cost_reminder_time,
            },
            "daily": overview,
            "planned_vacations": await planned_vacations(daily, chat_id, today),
            "help": GREETING_HELP,
            "members": [
                {
                    "position": m.position,
                    "name": m.username,
                    "user_id": m.user_id,
                    "vacations": [
                        {"id": v.id, "start": v.start, "end": v.end}
                        for v in m.vacations
                    ],
                }
                for m in members
            ],
        }
        return web.json_response(payload)

    async def api_add(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Нужен JSON")
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        position = body.get("position")
        start = body.get("start")
        end = body.get("end")
        if not isinstance(position, int):
            raise web.HTTPBadRequest(text="Не указан участник")
        if not _is_valid_iso_date(start) or not _is_valid_iso_date(end):
            raise web.HTTPBadRequest(text="Неверная дата")
        if start > end:
            raise web.HTTPBadRequest(text="Дата начала позже даты конца")
        today_s = str(today_in_tz(tz))
        if end < today_s:
            raise web.HTTPBadRequest(text="Отпуск уже закончился")
        if start < today_s:
            raise web.HTTPBadRequest(text="Нельзя задать отпуск в прошлом")
        members = await daily.get_members(chat_id)
        if not any(m.position == position for m in members):
            raise web.HTTPBadRequest(text="Участник не найден")
        vacation = await daily.add_vacation(chat_id, position, start, end)
        if vacation is None:
            raise web.HTTPBadRequest(text="Участник не найден")
        await _refresh(chat_id)
        return web.json_response(
            {"id": vacation.id, "start": vacation.start, "end": vacation.end}
        )

    async def api_delete(request: web.Request) -> web.Response:
        chat_id, _, _ = await _authenticate(request, daily, request.query.get("chat"))
        raw_id = request.match_info.get("vacation_id", "")
        try:
            vacation_id = int(raw_id)
        except ValueError:
            raise web.HTTPBadRequest(text="Неверный id отпуска")
        deleted = await daily.delete_vacation(chat_id, vacation_id)
        if not deleted:
            raise web.HTTPNotFound(text="Отпуск не найден")
        await _refresh(chat_id)
        return web.json_response({"deleted": vacation_id})

    async def _read_json(request: web.Request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Нужен JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Нужен JSON-объект")
        return body

    def _member_payload(member: DailyMember) -> dict:
        return {
            "position": member.position,
            "name": member.username,
            "user_id": member.user_id,
        }

    def _user_name(user) -> str:
        first_name = (
            getattr(user, "first_name", None)
            or getattr(user, "username", None)
            or str(user.id)
        )
        username = getattr(user, "username", None)
        return "{} @{}".format(first_name, username) if username else first_name

    async def api_members_add(request: web.Request) -> web.Response:
        body = await _read_json(request)
        mode = body.get("mode")
        if mode == "self":
            chat_id, user = await _authenticate_self_add(request, body.get("chat"))
            name = (body.get("name") or _user_name(user)).strip()
            user_id = user.id
        else:
            chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
            name = body.get("name")
            user_id = None
        result = await add_member(
            daily,
            chat_id,
            name,
            user_id,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        return web.json_response({"member": _member_payload(result.member)})

    async def api_members_delete(request: web.Request) -> web.Response:
        chat_id, _, _ = await _authenticate(request, daily, request.query.get("chat"))
        try:
            position = int(request.match_info.get("position", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="Неверный участник")
        result = await remove_member(
            daily,
            chat_id,
            position,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPNotFound(text=result.message)
        return web.json_response({"deleted": position})

    async def api_members_move(request: web.Request) -> web.Response:
        body = await _read_json(request)
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        try:
            position = int(request.match_info.get("position", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="Неверный участник")
        direction = body.get("direction")
        result = await move_member(
            daily,
            chat_id,
            position,
            direction,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        return web.json_response({"moved": result.moved, "message": result.message})

    async def api_settings(request: web.Request) -> web.Response:
        body = await _read_json(request)
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        enabled = body.get("cost_reminder_enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text="Неверное значение переключателя")
        result = await update_settings(
            daily,
            chat_id,
            daily_time=body.get("daily_time"),
            cost_reminder_enabled=enabled,
            cost_reminder_time=body.get("cost_reminder_time"),
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        chat = await get_chat_or_default(daily, chat_id)
        return web.json_response(
            {
                "settings": {
                    "daily_time": chat.daily_time,
                    "cost_reminder_enabled": chat.cost_reminder_enabled,
                    "cost_reminder_time": chat.cost_reminder_time,
                }
            }
        )

    async def api_daily_leader(request: web.Request) -> web.Response:
        body = await _read_json(request)
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        position = body.get("position")
        if not isinstance(position, int):
            raise web.HTTPBadRequest(text="Не указан участник")
        result = await set_leader_today(
            daily,
            chat_id,
            position,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        payload = {"message": result.message}
        if result.member is not None:
            payload["member"] = _member_payload(result.member)
        return web.json_response(payload)

    async def api_daily_substitute(request: web.Request) -> web.Response:
        body = await _read_json(request)
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        result = await substitute_today(
            daily,
            chat_id,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        return web.json_response({"message": result.message})

    async def api_daily_skip(request: web.Request) -> web.Response:
        body = await _read_json(request)
        chat_id, _, _ = await _authenticate(request, daily, body.get("chat"))
        position = body.get("position")
        if position is not None and not isinstance(position, int):
            raise web.HTTPBadRequest(text="Неверный участник")
        result = await skip_today(
            daily,
            chat_id,
            position,
            today_in_tz(tz),
            workdays=workdays,
        )
        if not result.ok:
            raise web.HTTPBadRequest(text=result.message)
        return web.json_response({"message": result.message})

    async def _refresh(chat_id: int) -> None:
        predicate = workdays.is_workday if workdays is not None else None
        await refresh_schedule_window(daily, chat_id, today_in_tz(tz), workdays=predicate)

    async def _parse_init_data(request: web.Request, raw_chat) -> Tuple[object, int]:
        """Validate initData and resolve the signed chat id."""
        raw = request.headers.get("X-Telegram-Init-Data", "")
        if not raw:
            logger.warning(
                "mini-app request without initData (page opened as a plain web link instead of a Mini App?)"
            )
            raise web.HTTPUnauthorized(text="Нет initData")
        try:
            from aiogram.utils.web_app import safe_parse_webapp_init_data

            data = safe_parse_webapp_init_data(token, raw)
        except ValueError:
            raise web.HTTPUnauthorized(text="Подпись initData не совпала")
        if data.user is None:
            raise web.HTTPUnauthorized(text="Нет данных пользователя")
        age = _init_data_age(data)
        if age is None:
            logger.warning("initData without parsable auth_date rejected")
            raise web.HTTPUnauthorized(text="Некорректная initData")
        if age > INIT_DATA_MAX_AGE or age < -INIT_DATA_FUTURE_TOLERANCE:
            raise web.HTTPUnauthorized(text="initData устарела")
        start_param = (getattr(data, "start_param", None) or "").strip()
        if start_param:
            raw_chat = start_param
        try:
            chat_id = int(raw_chat)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Не указан чат")
        return data, chat_id

    async def _resolve_member(chat_id: int, user) -> Optional[DailyMember]:
        members = await daily.get_members(chat_id)
        member = next((m for m in members if m.user_id == user.id), None)
        if member is None:
            # Members added manually via /team have no numeric id stored, only
            # an @username: match the signed initData username and bind the id
            # so future checks are stable.
            for candidate in members:
                if candidate.matches_telegram_account(user.id, user.username):
                    await daily.bind_member_user_id(chat_id, candidate.position, user.id)
                    candidate.user_id = user.id
                    logger.info(
                        "bound telegram id %s to member position %s in chat %s",
                        user.id,
                        candidate.position,
                        chat_id,
                    )
                    member = candidate
                    break
        return member

    async def _authenticate(
        request: web.Request, daily_: DailyRegistry, raw_chat
    ) -> Tuple[int, DailyMember, object]:
        """Validate initData and require an existing roster member.

        The chat comes from the signed `start_param` payload (Mini App deep
        link `t.me/<bot>?startapp=<chat_id>`) and only falls back to the
        query/body value for local debugging. Membership is what actually
        authorizes the request either way.
        """
        data, chat_id = await _parse_init_data(request, raw_chat)
        member = await _resolve_member(chat_id, data.user)
        if member is None:
            raise web.HTTPForbidden(
                text="Вы не участник этой команды. Администратор должен добавить вас "
                "в /team с вашим @username или ответом на ваше сообщение."
            )
        return chat_id, member, data.user

    async def _authenticate_self_add(
        request: web.Request, raw_chat
    ) -> Tuple[int, object]:
        """Authorize the "add me" flow for a chat member who is not in /team yet.

        The roster cannot authorize a not-yet-added person, so membership in
        the Telegram chat is checked through the Bot API instead.
        """
        data, chat_id = await _parse_init_data(request, raw_chat)
        member = await _resolve_member(chat_id, data.user)
        if member is None:
            checker = membership_checker or (
                lambda cid, uid: _telegram_is_chat_member(token, cid, uid)
            )
            if not await checker(chat_id, data.user.id):
                raise web.HTTPForbidden(text="Вы не участник этого чата")
        return chat_id, data.user

    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/vacations", api_add)
    app.router.add_delete("/api/vacations/{vacation_id}", api_delete)
    app.router.add_post("/api/members", api_members_add)
    app.router.add_delete("/api/members/{position}", api_members_delete)
    app.router.add_post("/api/members/{position}/move", api_members_move)
    app.router.add_put("/api/settings", api_settings)
    app.router.add_post("/api/daily/leader", api_daily_leader)
    app.router.add_post("/api/daily/substitute", api_daily_substitute)
    app.router.add_post("/api/daily/skip", api_daily_skip)
    app.router.add_static("/static/", STATIC_DIR)
    return app


async def start_webapp(
    daily: DailyRegistry,
    tz,
    token: str,
    workdays=None,
    membership_checker=None,
    port: int = 8443,
    cert_file: str = "",
    key_file: str = "",
) -> None:
    """Run the mini-app server until cancelled.

    Errors in the HTTP layer (port taken, broken cert) are logged and the
    task returns: polling and the reminder loop keep running.
    """
    runner = None
    scheme = "https"
    try:
        ssl_context: Optional[ssl.SSLContext] = None
        if cert_file and key_file:
            try:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(cert_file, key_file)
            except (FileNotFoundError, ssl.SSLError, OSError):
                logger.exception("mini-app certificates not loaded, falling back to http")
                ssl_context = None
        if ssl_context is None:
            scheme = "http"
        app = create_app(
            daily,
            tz,
            token,
            workdays=workdays,
            membership_checker=membership_checker,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_context)
        await site.start()
        logger.info("mini-app server listening on %s://0.0.0.0:%s", scheme, port)
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("mini-app server failed to start (port %s)", port)
    finally:
        if runner is not None:
            try:
                await asyncio.wait_for(runner.cleanup(), timeout=5)
            except Exception:
                logger.warning("mini-app server cleanup failed", exc_info=True)
