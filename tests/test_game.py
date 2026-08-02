"""Game model roundtrip and GameRegistry tests."""
import json

import pytest

from ppbot.game import Game, GameRegistry, Vote


def make_initiator(user_id=1, username="alice", first_name="Alice"):
    return {"id": user_id, "username": username, "first_name": first_name}


class TestVote:
    def test_set_increments_version(self):
        v = Vote()
        assert v.version == -1
        v.set("5")
        assert v.point == "5"
        assert v.version == 0
        v.set("8")
        assert v.point == "8"
        assert v.version == 1

    def test_masked_cycles_marks(self):
        v = Vote()
        v.set("5")
        assert v.masked == "♥"
        v.set("5")
        assert v.masked == "♦"

    def test_roundtrip(self):
        v = Vote()
        v.set("13")
        v2 = Vote.from_dict(v.to_dict())
        assert v2.point == "13"
        assert v2.version == 0


class TestGame:
    def test_add_vote_keyed_by_initiator_str(self):
        g = Game(1, "100", make_initiator(), "task")
        g.add_vote({"id": 5, "username": "bob", "first_name": "Bob"}, "8")
        assert "@bob (Bob)" in g.votes

    def test_get_text_revealed_shows_points_and_avg(self):
        g = Game(1, "100", make_initiator(), "task")
        g.add_vote({"id": 1, "username": "a", "first_name": "A"}, "5")
        g.add_vote({"id": 2, "username": "b", "first_name": "B"}, "8")
        text_hidden = g.get_text()
        assert "♥" in text_hidden
        g.revealed = True
        text = g.get_text()
        assert "Результаты для задачи" in text
        assert "5" in text and "8" in text
        assert "Средняя оценка" in text

    def test_roundtrip(self):
        g = Game(7, "200", make_initiator(), "some task")
        g.add_vote({"id": 1, "username": "a", "first_name": "A"}, "3")
        g.add_vote({"id": 2, "username": "b", "first_name": "B"}, "5")
        g.reply_message_id = 42
        g.revealed = True

        dct = g.to_dict()
        g2 = Game.from_dict(7, "200", dct)
        assert g2.chat_id == 7
        assert g2.vote_id == "200"
        assert g2.initiator == g.initiator
        assert g2.text == "some task"
        assert g2.reply_message_id == 42
        assert g2.revealed is True
        assert dict(g2.votes)["@a (A)"].point == "3"
        assert dict(g2.votes)["@b (B)"].point == "5"

    def test_restart_clears(self):
        g = Game(1, "1", make_initiator(), "t")
        g.add_vote({"id": 1, "username": "a", "first_name": "A"}, "1")
        g.revealed = True
        g.restart()
        assert not g.votes
        assert not g.revealed

    def test_get_markup_shape(self):
        g = Game(1, "5", make_initiator(), "t")
        markup = g.get_markup()
        assert markup["type"] == "InlineKeyboardMarkup"
        rows = markup["inline_keyboard"]
        assert len(rows) == 4
        assert len(rows[0]) == 5 and len(rows[1]) == 5
        assert rows[0][0]["callback_data"] == "vote-click-5-1"
        assert rows[2][0]["callback_data"].startswith("restart-new-click-5")
        assert rows[3][0]["callback_data"].startswith("reveal-new-click-5")


@pytest.fixture
async def registry(tmp_path):
    from ppbot.game import GameRegistry

    db = tmp_path / "game.db"
    r = GameRegistry()
    await r.init_db(str(db))
    yield r
    await r._db.close()


class TestGameRegistry:
    async def test_new_game_creates(self, registry):
        g = registry.new_game(1, "100", make_initiator(), "task")
        assert isinstance(g, Game)

    async def test_save_get_roundtrip(self, registry):
        g = registry.new_game(1, "100", make_initiator(), "task")
        g.add_vote({"id": 1, "username": "a", "first_name": "A"}, "8")
        g.reply_message_id = 10
        await registry.save_game(g)

        loaded = await registry.get_game(1, "100")
        assert loaded is not None
        assert loaded.text == "task"
        assert loaded.reply_message_id == 10
        assert dict(loaded.votes)["@a (A)"].point == "8"

    async def test_get_missing_returns_none(self, registry):
        assert await registry.get_game(1, "nope") is None

    async def test_save_overwrites(self, registry):
        g = registry.new_game(1, "100", make_initiator(), "task")
        await registry.save_game(g)
        g2 = Game.from_dict(1, "100", json.loads(json.dumps(g.to_dict())))
        g2.text = "updated"
        g2.reply_message_id = 99
        await registry.save_game(g2)

        loaded = await registry.get_game(1, "100")
        assert loaded.text == "updated"
        assert loaded.reply_message_id == 99
