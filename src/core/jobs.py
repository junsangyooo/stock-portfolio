"""In-process background jobs for model calls a request cannot wait for.

A comprehensive analysis runs ~570s and a chat turn as long as its tool loop
needs; nginx cuts at 30s and Cloudflare at ~100s. So the route starts the
call on a daemon thread, answers with a job id at once, and the browser polls.

One runner per kind of job, each with its own staleness rule. A `key` names
what a job is about (a symbol, a chat thread): while a job for that key is
running, starting another returns the running one instead. Past `stale_s` a
running job is reported `stale` and no longer blocks the key — a display
rule, not a timeout: the call itself is never cut (ops notes §7).

Single uvicorn worker (dashboard_web/server.py), so an in-memory registry is
shared by every request. Nothing here outlives a restart, and nothing needs
to: each job's result is stored by the job itself before it reports done.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional


class Runner:
    def __init__(self, stale_s: float, keep_s: float = 3600):
        self.stale_s = stale_s      # running past this reads as 'stale'
        self.keep_s = keep_s        # finished jobs are forgotten after this
        self._jobs: dict = {}       # job_id -> {key, state, result, error, started_at, _t, **meta}
        self._lock = threading.Lock()

    def _live(self, j: dict, now: float) -> bool:
        return j["state"] == "running" and now - j["_t"] <= self.stale_s

    def _view(self, job_id: str, j: dict) -> dict:
        out = {k: v for k, v in j.items() if not k.startswith("_")}
        if j["state"] == "running" and time.time() - j["_t"] > self.stale_s:
            out["state"] = "stale"
        return {"job_id": job_id, **out}

    def start(self, key: str, fn: Callable[[], object], **meta) -> tuple[str, bool]:
        """(job_id, started). When a job for `key` is still live, its id and
        False — nothing new runs. `fn`'s return value becomes `result`."""
        now = time.time()
        with self._lock:
            for jid in [k for k, j in self._jobs.items()
                        if j["state"] != "running" and now - j["_t"] > self.keep_s]:
                del self._jobs[jid]
            for jid, j in self._jobs.items():
                if j["key"] == key and self._live(j, now):
                    return jid, False
            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "key": key, "state": "running", "result": None, "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(), "_t": now,
                **meta}

        def _run() -> None:
            try:
                res, state, err = fn(), "done", None
            except Exception as e:  # noqa: BLE001 — surface to the poller, never crash
                res, state, err = None, "error", str(e)[:300]
            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id].update(state=state, result=res, error=err,
                                              _t=time.time())

        threading.Thread(target=_run, daemon=True, name=f"job:{key}").start()
        return job_id, True

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            j = self._jobs.get(job_id)
            return self._view(job_id, j) if j else None

    def latest(self, key: str) -> Optional[dict]:
        """The newest job for `key`, or None."""
        with self._lock:
            mine = [(jid, j) for jid, j in self._jobs.items() if j["key"] == key]
            if not mine:
                return None
            jid, j = max(mine, key=lambda x: x[1]["_t"])
            return self._view(jid, j)

    def running(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            return any(j["key"] == key and self._live(j, now)
                       for j in self._jobs.values())

    def forget(self, key: str) -> None:
        """Drop every job for `key` (a deleted chat thread)."""
        with self._lock:
            for jid in [k for k, j in self._jobs.items() if j["key"] == key]:
                del self._jobs[jid]
