"""Token bucket — `rate` tokens per second, `burst` at most (default: rate).

`take()` blocks until a token is available. Thread-safe; one instance per
limit (Toss keeps one per API group, KIS one global).
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class TokenBucket:
    def __init__(self, rate: float, burst: Optional[float] = None):
        self.rate = rate
        self.burst = rate if burst is None else burst
        self.level = self.burst
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.level = min(self.burst,
                                 self.level + (now - self.t) * self.rate)
                self.t = now
                if self.level >= 1.0:
                    self.level -= 1.0
                    return
                wait = (1.0 - self.level) / self.rate
            time.sleep(wait)
