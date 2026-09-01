"""Comprehensive symbol analysis — one deterministic pass, then one LLM call.

    inputs.py   assemble the envelope (chart facts, financials, news, peers)
                — no LLM, no account. Computes the chart once per trading day
                and reuses the stored row after that.
    prompt.py   the single call's prompt and output contract.
    run.py      execute, validate, persist.

Deliberately not a fan-out. See prompt.py for why the domains have to meet
before the judgment rather than after.

Nothing here reads portfolio.db or quant.db. The analysis is about the symbol;
what to do about it depends on a position, and that belongs to whichever book
is asking. That is what lets one stored result serve both.
"""
