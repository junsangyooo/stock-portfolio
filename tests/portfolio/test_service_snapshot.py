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


def _rows(aid):
    return {r["date"]: r["total_assets"] for r in snapshot_store.read(aid, "KRW", 3)}


def _one_holding(monkeypatch, price):
    monkeypatch.setattr(service.uni_search, "get", lambda sid: {"currency": "KRW"})
    monkeypatch.setattr(quote_store, "_cache", {})
    account_store.create("bob", "bob", "GENERAL")
    holding_store.upsert("bob", "KR:000001", qty=10, avg_cost=100)
    _quote("KR:000001", price)


def test_a_view_writes_the_day_once_and_never_moves_it(monkeypatch):
    """The scheduled closes are the day's record; a page view only fills a
    day they have not written (it used to rewrite the row on every read)."""
    _one_holding(monkeypatch, 110)
    service.aggregate("bob")
    assert _rows("bob") == {kst.today(): 1100}
    _quote("KR:000001", 130)
    r = service.aggregate("bob")
    assert r["totals"]["KRW"]["eval"] == 1300            # the page shows the new price
    assert _rows("bob") == {kst.today(): 1100}           # the record does not move


def test_the_scheduled_close_overwrites_the_day(monkeypatch):
    _one_holding(monkeypatch, 110)
    service.aggregate("bob")
    _quote("KR:000001", 130)
    assert service.record_snapshot("bob") == {"written": ["KRW"], "missing": []}
    assert _rows("bob") == {kst.today(): 1300}


def test_the_scheduled_close_skips_a_currency_without_every_price(monkeypatch):
    monkeypatch.setattr(service.uni_search, "get",
                        lambda sid: {"currency": "USD" if sid.startswith("US:") else "KRW"})
    monkeypatch.setattr(quote_store, "_cache", {})
    account_store.create("bob", "bob", "GENERAL")
    holding_store.upsert("bob", "KR:000001", qty=10, avg_cost=100)
    holding_store.upsert("bob", "US:AAA", qty=1, avg_cost=10)
    _quote("KR:000001", 110)
    assert service.record_snapshot("bob") == {"written": ["KRW"], "missing": ["USD"]}
    assert snapshot_store.read("bob", "USD", 3) == []


def test_the_job_records_every_account_and_names_unpriced_symbols(monkeypatch, capsys):
    import json
    from src.portfolio import snapshot_job
    _one_holding(monkeypatch, 110)
    service.aggregate("bob")                              # a view wrote 1,100 first
    monkeypatch.setattr(snapshot_job.gap_filler, "fill_many", lambda sids: {s: 0 for s in sids})
    holding_store.upsert("bob", "KR:000002", qty=1, avg_cost=1)

    def warm(sids, max_age_s, *, failed=None):
        failed["KR:000002"] = "source returned nothing"
        return {"KR:000001": quote_store.get("KR:000001"), "KR:000002": None}
    monkeypatch.setattr(snapshot_job.quotes, "warm", warm)
    assert snapshot_job.main() == 1                       # no account fully priced
    out = json.loads(capsys.readouterr().out)
    assert out["accounts_without_snapshot"] == ["bob"]
    assert out["quotes_stale"] == ["KR:000002: source returned nothing"]
    assert _rows("bob") == {kst.today(): 1100}            # a partial sum is not written

    holding_store.remove("bob", "KR:000002")
    _quote("KR:000001", 130)
    monkeypatch.setattr(snapshot_job.quotes, "warm",
                        lambda sids, max_age_s, *, failed=None: {s: quote_store.get(s) for s in sids})
    assert snapshot_job.main() == 0
    assert json.loads(capsys.readouterr().out)["accounts_ok"] == ["bob"]
    assert _rows("bob") == {kst.today(): 1300}            # the close replaces the view's row
