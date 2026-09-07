"""In-process async job runner for the on-demand comprehensive analysis.

WHY: the analysis (one claude -p call over chart + fundamental + news and
their dictionaries) takes ~570s on average and longer at p95. The web path can
never wait that long — nginx cuts at 30s, Cloudflare at ~100s. So a synchronous
POST /run always 504s before the model finishes. This runner decouples
generation from the HTTP request: POST /run spawns a background thread and
returns immediately; the browser polls GET /status until the result is stored,
then loads it via GET /analysis/{m}/{c}.

Single uvicorn worker (server.py runs uvicorn.run with no workers arg), so a
plain in-memory registry is shared across requests. A duplicate start while
one is running is a no-op.

Keys are opaque strings, not symbol ids: the plan preview runs here too and
namespaces itself as "plan:{account_id}" so an account and a symbol can never
collide.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

# key -> {state, started_at, error, date, status, _t}
#   state: running | done | error      (_t = monotonic-ish wall time for staleness)
_jobs: dict = {}
_lock = threading.Lock()

# Display rule, not a timeout: the analysis call itself is unbounded. A job
# 'running' past this is reported 'stale' so the UI stops waiting. 2x the
# measured p95 (~1,070s; n=26, 343–1,068s, mean 570s — docs/planning-00-brief.md
# §4). Below the real p95 the UI gave up on live jobs.
_STALE_S = 2 * 1070


def get_status(key: str) -> dict:
    """Current job state for a key. 'idle' when never started; a
    'running' job past _STALE_S is reported 'stale' so the UI stops waiting."""
    with _lock:
        j = _jobs.get(key)
        if not j:
            return {"state": "idle"}
        if j["state"] == "running" and (time.time() - j["_t"]) > _STALE_S:
            return {"state": "stale", "started_at": j.get("started_at")}
        return {k: v for k, v in j.items() if not k.startswith("_")}


def start(key: str, fn: Callable[[], dict]) -> dict:
    """Spawn `fn` (a blocking model call) on a daemon thread unless an
    identical job is already running. Returns {state: started|running}. The
    thread records done/error + the stored result's date into the registry.
    """
    k = key
    with _lock:
        j = _jobs.get(k)
        if j and j["state"] == "running" and (time.time() - j["_t"]) <= _STALE_S:
            return {"state": "running", "started_at": j.get("started_at")}
        _jobs[k] = {
            "state": "running",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "error": None, "_t": time.time(),
        }

    def _run() -> None:
        try:
            res = fn() or {}
            with _lock:
                _jobs[k] = {
                    "state": "done", "_t": time.time(), "error": None,
                    "date": res.get("date"),
                    "result_status": res.get("status"),
                    "started_at": _jobs.get(k, {}).get("started_at"),
                }
        except Exception as e:  # noqa: BLE001 — surface to the poller, never crash
            with _lock:
                _jobs[k] = {"state": "error", "_t": time.time(),
                            "error": str(e)[:300],
                            "started_at": _jobs.get(k, {}).get("started_at")}

    threading.Thread(target=_run, daemon=True, name=f"job:{k}").start()
    return {"state": "started"}
