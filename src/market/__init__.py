"""Market data: sources (adapters), stores (market.db), pipelines (jobs).

Deliberately does NOT import its subpackages. Reading a stored row should not
pull in pykrx and yfinance — importing `src.market.store.theme_store` used to
drag in every network adapter in the tree. Import what you need explicitly:
``from ..market.store import ohlcv_store``.
"""
