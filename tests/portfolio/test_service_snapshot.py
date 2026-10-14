"""A day's snapshot is never a partial sum (인계 §4 #8): a held position
without a price left the asset curve with a fall that never happened."""
import time

from src.core import kst
from src.market.source.base import Quote
from src.market.store import quote_store
from src.portfolio import service
from src.portfolio.store import account_store, holding_store, snapshot_store


def _quote(sid, price):
    quote_store.set_quote(Quote(symbol_id=sid, price=price, prev_close=price, ts=time.time()))


def test_a_currency_with_a_priceless_position_is_not_snapshotted(monkeypatch):
    monkeypatch.setattr(service.uni_search, "get", lambda sid: {"currency": "KRW"})
    monkeypatch.setattr(quote_store, "_cache", {})
    account_store.create("alice", "alice", "GENERAL")
    holding_store.upsert("alice", "KR:000001", qty=1, avg_cost=100)
    holding_store.upsert("alice", "KR:000002", qty=1, avg_cost=100)
    _quote("KR:000001", 110)
    r = service.aggregate("alice")
    assert r["totals"]["KRW"]["n_missing"] == 1
    assert not snapshot_store.has_for(kst.today(), "alice")
    _quote("KR:000002", 90)
    service.aggregate("alice")
    assert snapshot_store.has_for(kst.today(), "alice")
