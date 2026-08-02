"""Offline test helpers: FakeSession records outgoing API calls without network."""
import types

from aiogram.client.session.base import BaseSession


def _default_payload(method):
    """Minimal valid payload for methods whose response is a Message."""
    chat = {"id": 1, "type": "private"}
    sender = {"id": 1, "is_bot": True, "first_name": "testbot"}
    if method.__api_method__ == "sendMessage":
        return {
            "message_id": 1,
            "date": 0,
            "chat": chat,
            "from": sender,
        }
    if method.__api_method__ == "editMessageText":
        return {
            "message_id": 1,
            "date": 0,
            "chat": chat,
            "from": sender,
        }
    if method.__api_method__ == "getMe":
        return {"id": 1, "is_bot": True, "first_name": "testbot", "username": "testbot"}
    return None


class FakeSession(BaseSession):
    """aiohttp-free session; records (method_name, payload) into ``calls``."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(
        self,
        bot,
        method,
        timeout=None,
    ):
        self.calls.append((method.__api_method__, method.model_dump(exclude_unset=True)))
        payload = _default_payload(method)
        returning = method.__returning__
        if payload is not None:
            if isinstance(returning, types.UnionType):
                for member in returning.__args__:
                    if member is not bool:
                        return member.model_validate(payload)
            return returning.model_validate(payload)
        if returning is bool:
            return True
        if isinstance(returning, types.UnionType):
            for member in returning.__args__:
                if member is not bool:
                    return member.model_construct()
        return returning.model_construct()

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield b""

    async def close(self):
        pass
