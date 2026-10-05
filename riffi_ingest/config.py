"""Settings from the environment. Secrets never live in code or in the repo, only in the environment."""

from __future__ import annotations

import os

DEFAULT_RSSHUB_BASE = ""  # empty: Telegram channels are read from their public t.me/s/ page


def rsshub_base() -> str:
    """Where Telegram channels are read from. By default their public page, t.me/s/<channel> (the founder's
    choice on 5 Oct 2026, after rsshub.app refused them); set RSSHUB_BASE_URL to use a self-hosted RSSHub."""
    return (os.environ.get("RSSHUB_BASE_URL") or DEFAULT_RSSHUB_BASE).rstrip("/")
