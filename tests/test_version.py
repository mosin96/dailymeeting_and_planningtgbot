"""Build-info helper: the startup log must show which commit is running."""
from ppbot import version


def test_build_commit_prefers_env(monkeypatch):
    monkeypatch.setenv("PP_BUILD_COMMIT", "abc1234")
    assert version.build_commit() == "abc1234"


def test_build_commit_strips_env(monkeypatch):
    monkeypatch.setenv("PP_BUILD_COMMIT", "  abc1234 \n")
    assert version.build_commit() == "abc1234"


def test_build_commit_falls_back_to_git(monkeypatch):
    monkeypatch.delenv("PP_BUILD_COMMIT", raising=False)
    monkeypatch.setattr(version, "_git_commit", lambda: "def5678")
    assert version.build_commit() == "def5678"


def test_build_commit_unknown_without_env_and_git(monkeypatch):
    monkeypatch.delenv("PP_BUILD_COMMIT", raising=False)
    monkeypatch.setattr(version, "_git_commit", lambda: "")
    assert version.build_commit() == "unknown"
