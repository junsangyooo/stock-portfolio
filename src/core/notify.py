"""Discord webhook notifier — `send`, `send_embed`.

Env: DISCORD_WEBHOOK_URL  (silent no-op when missing). When unset — cron runs
with a minimal env — the URL is read from the secrets .env file instead.
"""
from __future__ import annotations

import logging
import time

import httpx

from . import secrets

log = logging.getLogger(__name__)


def _url() -> str | None:
    return secrets.get("DISCORD_WEBHOOK_URL")


def _post(payload: dict, what: str) -> bool:
    u = _url()
    if not u:
        log.debug("discord: webhook not set, skipping")
        return False
    try:
        r = httpx.post(u, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        log.warning(f"discord: {what} error {e}")
        return False


def send(text: str, username: str = "stock-portfolio") -> bool:
    """Send plain-text message. Discord max 2000 chars."""
    return _post({"content": text[:1990], "username": username}, "send")


def send_embed(title: str, description: str = "",
               fields: list[dict] | None = None,
               color: int = 0x00E5FF,
               username: str = "stock-portfolio",
               footer: str | None = None) -> bool:
    """Send a rich embed. fields = [{name, value, inline?}, ...]"""
    embed: dict = {"title": title[:256], "description": description[:4000],
                   "color": color, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if fields:
        embed["fields"] = [{"name": f["name"][:256],
                            "value": str(f["value"])[:1024],
                            "inline": bool(f.get("inline", False))}
                           for f in fields[:25]]
    if footer:
        embed["footer"] = {"text": footer[:2048]}
    return _post({"embeds": [embed], "username": username}, "embed")
