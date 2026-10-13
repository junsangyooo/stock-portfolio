"""Research — what is true about a symbol and about the world it lives in.

Named for what it does, not for how. Two of the four subpackages contain no
agent at all:

  news/         collection pipeline → news.db. No LLM in collect; the enrich
                and summary steps call one.
  chart/        deterministic indicator + structure extraction from OHLCV.
                No LLM, ever — it reports facts and refuses to judge them.
  fundamental/  statements and derived metrics, assembled for reading.
                No LLM.
  analyze/      the one agent: all three domains in a single call.
                store.py (label vocabulary, write path), batch_job.py (warm a
                given list of symbols — takes --symbols; it does not know
                accounts exist).

The model backends are in core/llm.py: news, analysis and chat all call them.

Shared: the portfolio app reads this and does not own it. Nothing here
imports an account book — tests/test_layering.py enforces that.
"""
