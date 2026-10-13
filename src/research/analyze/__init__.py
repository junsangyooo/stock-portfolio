"""Comprehensive symbol analysis — one deterministic pass, then one LLM call.

    from src.research import analyze

    for sid in analyze.due_among(symbols):
        analyze.run(sid)
    result = analyze.latest(sid)

Inside:

    inputs.py   assemble the envelope (chart facts, financials, peers, news)
                — no LLM, no account. Computes the chart once per trading day
                and reuses the stored row after that.
    prompt.py   the single call's prompt and output contract.
    engine.py   execute, validate, persist.
    read.py     everything already stored.
    __main__.py `python -m src.research.analyze KR:005930`

The module is `engine`, the exported function is `run` — naming the module
`run` too would have shadowed it the moment this package exported anything.

Deliberately not a fan-out. See prompt.py for why the domains have to meet
before the judgment rather than after.

Nothing here reads portfolio.db. The analysis is about the symbol; what to
do about it depends on a position, and that belongs to whichever account is
asking. That is what lets one stored result serve every account.
"""
from .inputs import build as assemble          # noqa: F401 — deterministic half
from .read import (                            # noqa: F401
    KIND, latest, latest_date, on, summary,
)
from . import score                             # noqa: F401 — the scorecard
from .due import due_among, due_detail          # noqa: F401 — what changed
from .engine import analyze as run, batch_root  # noqa: F401

__all__ = ["run", "assemble", "latest", "on", "latest_date", "summary",
           "due_among", "due_detail", "batch_root", "score", "KIND"]
