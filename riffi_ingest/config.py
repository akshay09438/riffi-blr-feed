"""Settings from the environment. Secrets never live in code or in the repo, only in the environment."""

from __future__ import annotations

import os

DEFAULT_RSSHUB_BASE = "https://rsshub.app"


def rsshub_base() -> str:
    """Where Telegram channels are read from. Public rsshub.app by default; set RSSHUB_BASE_URL to
    point at a self-hosted RSSHub (BRIEF.md step 1)."""
    return (os.environ.get("RSSHUB_BASE_URL") or DEFAULT_RSSHUB_BASE).rstrip("/")
