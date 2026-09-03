"""deploy/drop_pre_ledger.py: whitelist-only cleanup of the two account books.

Paths are redirected the way test_backup.py does — monkeypatch ``core.paths``
so nothing touches data/.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.paths as paths
from src.ledger import apply as apply_ledger

_spec = importlib.util.spec_from_file_location(
    "drop_pre_ledger",
    Path(__file__).resolve().parents[1] / "deploy" / "drop_pre_ledger.py")
drop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drop)


def _tables(path) -> set:
    return {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _holdings(path) -> set:
    return {tuple(r) for r in sqlite3.connect(path).execute(
        "SELECT account_id, symbol_id FROM holdings")}


def _cols(path, table) -> set:
    return {r[1] for r in sqlite3.connect(path).execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "portfolio.db")
    monkeypatch.setattr(paths, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(paths, "BACKUP_DIR", tmp_path / "backup")

    p = sqlite3.connect(tmp_path / "portfolio.db")
    apply_ledger(p)
    p.executescript("""
        ALTER TABLE accounts ADD COLUMN auto_trade INTEGER DEFAULT 0;
        CREATE TABLE balances(x);            INSERT INTO balances VALUES (1);
        CREATE TABLE holding_themes(x);      INSERT INTO holding_themes VALUES (1);
        CREATE TABLE cash_pre_ledger(x);     INSERT INTO cash_pre_ledger VALUES (1);
        CREATE TABLE my_notes(x);            INSERT INTO my_notes VALUES (1);
        INSERT INTO accounts(account_id, name, created_at) VALUES ('isa', 'ISA', 0);
        INSERT INTO holdings(account_id, symbol_id, qty, added_at, updated_at)
            VALUES ('isa', 'KR:005930', 10, 0, 0),
                   ('robo', 'US:NVDA', 3, 0, 0);
    """)
    p.commit(); p.close()

    q = sqlite3.connect(tmp_path / "quant.db")
    apply_ledger(q)
    q.executescript("""
        CREATE TABLE account(x);             INSERT INTO account VALUES (1);
        CREATE TABLE positions(x);           INSERT INTO positions VALUES (1);
        CREATE TABLE plans_pre_ledger(x);    INSERT INTO plans_pre_ledger VALUES (1);
        CREATE TABLE cycles(x);              INSERT INTO cycles VALUES (1);
        INSERT INTO accounts(account_id, name, kind, created_at) VALUES ('robo', 'Q', 'auto', 0);
        INSERT INTO holdings(account_id, symbol_id, qty, added_at, updated_at)
            VALUES ('robo', 'US:NVDA', 3, 0, 0);
    """)
    q.commit(); q.close()
    return tmp_path


def test_dry_run_changes_nothing(books, capsys):
    before = {b: (_tables(books / f"{b}.db"), _holdings(books / f"{b}.db"))
              for b in ("portfolio", "quant")}
    assert drop.main([]) == 0
    for b in ("portfolio", "quant"):
        assert (_tables(books / f"{b}.db"), _holdings(books / f"{b}.db")) == before[b]
    assert "auto_trade" in _cols(books / "portfolio.db", "accounts")
    assert not (books / "backup").exists()
    out = capsys.readouterr().out
    assert "balances" in out and "robo" in out and "US:NVDA" in out
    assert "dry-run" in out


def test_apply_drops_only_whitelisted(books):
    assert drop.main(["--apply"]) == 0

    pt = _tables(books / "portfolio.db")
    assert not pt & {"balances", "holding_themes", "cash_pre_ledger"}
    assert {"accounts", "holdings", "trades", "cash", "snapshots", "plans",
            "watch", "my_notes"} <= pt
    assert _holdings(books / "portfolio.db") == {("isa", "KR:005930")}
    assert "auto_trade" not in _cols(books / "portfolio.db", "accounts")
    assert sqlite3.connect(books / "portfolio.db").execute(
        "SELECT count(*) FROM my_notes").fetchone()[0] == 1

    qt = _tables(books / "quant.db")
    assert not qt & {"account", "positions", "plans_pre_ledger"}
    assert {"accounts", "holdings", "cycles"} <= qt
    assert _holdings(books / "quant.db") == {("robo", "US:NVDA")}


def test_apply_backs_up_first(books, monkeypatch):
    calls: list = []
    real = drop.backup.backup_all

    def spy(only=None):
        calls.append(("backup", tuple(only), "balances" in _tables(books / "portfolio.db")))
        return real(only=only)

    monkeypatch.setattr(drop.backup, "backup_all", spy)
    assert drop.main(["--book", "portfolio", "--apply"]) == 0
    # one call, for the selected book only, while the old tables still exist
    assert calls == [("backup", ("portfolio",), True)]
    assert list((books / "backup").glob("portfolio.db.*"))
    assert not list((books / "backup").glob("quant.db.*"))
    assert "account" in _tables(books / "quant.db")        # untouched book


def test_backup_failure_aborts(books, monkeypatch):
    def boom(only=None):
        raise RuntimeError("disk full")
    monkeypatch.setattr(drop.backup, "backup_all", boom)
    assert drop.main(["--apply"]) == 1
    assert "balances" in _tables(books / "portfolio.db")
    assert ("robo", "US:NVDA") in _holdings(books / "portfolio.db")


def test_live_tables_never_targeted(books):
    c = sqlite3.connect(books / "quant.db")
    assert "cycles" not in drop.targets(c, "quant")
    assert "accounts" not in drop.targets(c, "quant")
    assert drop.targets(c, "quant") == ["account", "plans_pre_ledger", "positions"]
