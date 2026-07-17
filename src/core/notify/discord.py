"""Discord webhook notifier — `send`, `send_embed`, `alert`, `heartbeat`.

Env: DISCORD_WEBHOOK_URL  (silent no-op when missing)
Rate-limited per `key` to prevent spam.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_LAST_SENT: dict[str, float] = {}


def _url() -> str | None:
    u = os.getenv("DISCORD_WEBHOOK_URL")
    if u:
        return u
    # File fallback for non-systemd contexts (e.g. cron has a minimal env).
    # Local mac keeps secrets in ~/.claude/secrets; Oracle in ~/dotclaude/secrets.
    for env in (Path.home() / ".claude" / "secrets" / ".env",
                Path.home() / "dotclaude" / "secrets" / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                m = re.match(r"\s*DISCORD_WEBHOOK_URL\s*=\s*(\S+)", line)
                if m:
                    return m.group(1).strip()
    return None


def send(text: str, key: str = "default", min_interval_s: float = 0.0,
         username: str = "portfolio-dashboard") -> bool:
    """Send plain-text message. Discord max 2000 chars.
    `key` + `min_interval_s` provide per-bucket rate limiting (0 = no limit)."""
    u = _url()
    if not u:
        log.debug("discord: webhook not set, skipping")
        return False
    if min_interval_s > 0:
        now = time.time()
        if (now - _LAST_SENT.get(key, 0)) < min_interval_s:
            log.debug(f"discord: rate-limited key={key}")
            return False
    try:
        r = httpx.post(u, json={"content": text[:1990], "username": username},
                       timeout=10)
        ok = r.status_code in (200, 204)
    except Exception as e:
        log.warning(f"discord: send error {e}")
        return False
    if ok and min_interval_s > 0:
        _LAST_SENT[key] = time.time()
    return ok


def heartbeat(svc: str, every_s: float = 3600.0) -> bool:
    """Send a periodic heartbeat (silent if rate-limited)."""
    return send(f"💚 **{svc}** heartbeat ok", key=f"hb_{svc}",
                min_interval_s=every_s)


def alert(svc: str, msg: str) -> bool:
    """Send an alert with 5-min cooldown per (svc, msg) bucket."""
    return send(f"⚠️ **{svc}**\n{msg}", key=f"alert_{svc}_{msg[:30]}",
                min_interval_s=300.0)


def send_embed(title: str, description: str = "",
               fields: list[dict] | None = None,
               color: int = 0x00E5FF,
               username: str = "portfolio-dashboard",
               footer: str | None = None) -> bool:
    """Send a rich embed. fields = [{name, value, inline?}, ...]"""
    u = _url()
    if not u:
        return False
    embed: dict = {"title": title[:256], "description": description[:4000],
                   "color": color, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if fields:
        embed["fields"] = [{"name": f["name"][:256],
                            "value": str(f["value"])[:1024],
                            "inline": bool(f.get("inline", False))}
                           for f in fields[:25]]
    if footer:
        embed["footer"] = {"text": footer[:2048]}
    try:
        r = httpx.post(u, json={"embeds": [embed], "username": username}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        log.warning(f"discord: embed error {e}")
        return False
