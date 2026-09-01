"""The account ledger schema, shared by both books.

Not a book itself — it defines the tables and each app applies them to its own
file (portfolio.db, quant.db). See schema.py for why the two were the same
thing all along and what still differs.
"""
from .schema import SCHEMA, apply  # noqa: F401
