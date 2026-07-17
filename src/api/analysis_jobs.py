"""In-process async job runner for the on-demand analysis verdicts.

WHY: a chart / judge verdict (claude -p applying a long rulebook to a rich
schema) takes ~300s. The web path can never wait that long — nginx cuts at 30s,
Cloudflare at ~100s. So a synchronous POST /run always 504s before the model
finishes. This runner decouples generation from the HTTP request: POST /run
spawns a background thread and returns immediately; the browser polls GET
/status until the verdict file is written, then loads it via GET /verdict.

Single uvicorn worker (server.py runs uvicorn.run with no workers arg), so a
plain in-memory registry is shared across requests. Jobs are keyed by
(kind, symbol_id); a duplicate start while one is running is a no-op.
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

# A "running" job older than this is treated as dead (thread crashed / restart).
_STALE_S = 600.0


def _key(kind: str, symbol_id: str) -> str:
    return f"{kind}:{symbol_id}"


def get_status(kind: str, symbol_id: str) -> dict:
    """Current job state for (kind, symbol). 'idle' when never started; a
    'running' job past _STALE_S is reported 'stale' so the UI stops waiting."""
    with _lock:
        j = _jobs.get(_key(kind, symbol_id))
        if not j:
            return {"state": "idle"}
        if j["state"] == "running" and (time.time() - j["_t"]) > _STALE_S:
            return {"state": "stale", "started_at": j.get("started_at")}
        return {k: v for k, v in j.items() if not k.startswith("_")}


def start(kind: str, symbol_id: str, fn: Callable[[], dict]) -> dict:
    """Spawn `fn` (a blocking evaluate_symbol) on a daemon thread unless an
    identical job is already running. Returns {state: started|running}. The
    thread records done/error + the persisted verdict's date into the registry.
    """
    k = _key(kind, symbol_id)
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

    threading.Thread(target=_run, daemon=True, name=f"analysis:{k}").start()
    return {"state": "started"}
