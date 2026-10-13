"""The account ledger schema.

Not a book itself — it defines the tables, and the portfolio app applies them
to its file (portfolio.db). See schema.py for what each table holds.
"""
from .schema import apply  # noqa: F401
