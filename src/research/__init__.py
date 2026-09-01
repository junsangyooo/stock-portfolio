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

  verdict.py    the shared score/label vocabulary and the write path
  llm_backend.py  claude -p subprocess + the on-host open-weight server
  verdict_job.py  warm a given list of symbols (takes --symbols; it does not
                  know accounts exist)
  chat_tools.py   read-only lookups for the chat agent

Shared: portfolio and quant both read this and neither owns it. Nothing here
imports an account book — tests/test_layering.py enforces that.
"""
