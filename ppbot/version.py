"""Build information for the running bot.

The Docker image has no ``.git`` directory, so ``run.sh`` bakes the current
commit into the image via ``PP_BUILD_COMMIT``. When the bot is started from a
plain checkout (``python ppbot/bot.py``), the commit is read from git instead.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_commit() -> str:
    """Best-effort short hash of the current checkout, or "" if unavailable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_commit() -> str:
    """Commit the running bot was built from (or "unknown")."""
    return (os.environ.get("PP_BUILD_COMMIT") or _git_commit() or "unknown").strip()
