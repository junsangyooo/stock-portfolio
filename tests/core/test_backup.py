"""Backups: VACUUM INTO of the account and news books (weekday rotation) and
of market.db (one slot).

Data paths are redirected the same way conftest does for market/news —
monkeypatch ``core.paths`` so nothing touches data/.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.core.paths as paths
from src.core import backup


def _make_book(path, rows: int) -> sqlite3.Connection:
    """WAL book with `rows` rows; the connection is returned open."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=wal")
    con.execute("CREATE TABLE t(x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    return con


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "portfolio.db")
    monkeypatch.setattr(paths, "NEWS_DB_PATH", tmp_path / "news.db")
    monkeypatch.setattr(paths, "MARKET_DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(paths, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(backup, "_weekday", lambda: "mon")
    # portfolio stays open so its -wal is still on disk when the backup runs;
    # news/market are closed (checkpointed) so tests can corrupt the main file
    # without an unflushed WAL quietly supplying the good pages instead.
    con = _make_book(tmp_path / "portfolio.db", 3)
    _make_book(tmp_path / "news.db", 7).close()
    _make_book(tmp_path / "market.db", 9).close()
    yield tmp_path
    con.close()


def _rows(path) -> int:
    return sqlite3.connect(path).execute("SELECT count(*) FROM t").fetchone()[0]


def test_backup_all_copies_committed_wal_state(books):
    assert (books / "portfolio.db-wal").exists()   # WAL not yet checkpointed
    out = backup.backup_all()

    assert set(out) == {"portfolio", "news", "market"}
    for name, n in (("portfolio", 3), ("news", 7)):
        dst = books / "backup" / f"{name}.db.mon"
        assert out[name]["path"] == dst
        assert out[name]["bytes"] == dst.stat().st_size > 0
        assert _rows(dst) == n
        assert sqlite3.connect(dst).execute(
            "PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list((books / "backup").glob("*.tmp"))


def test_rerun_same_weekday_overwrites(books):
    backup.backup_all()
    sqlite3.connect(books / "news.db").execute("DELETE FROM t").connection.commit()
    backup.backup_all()
    assert _rows(books / "backup" / "news.db.mon") == 0
    assert len(list((books / "backup").iterdir())) == 3


def test_only_selects_books(books):
    out = backup.backup_all(only=["news"])
    assert set(out) == {"news"}
    assert [p.name for p in (books / "backup").iterdir()] == ["news.db.mon"]


def test_corrupt_source_raises_but_others_still_backed_up(books):
    (books / "news.db").write_bytes(b"not a database" * 64)
    with pytest.raises(RuntimeError, match="news"):
        backup.backup_all()
    assert _rows(books / "backup" / "portfolio.db.mon") == 3
    assert _rows(books / "backup" / "market.db.latest") == 9
    assert not (books / "backup" / "news.db.mon").exists()


def test_empty_file_raises(books):
    # VACUUM INTO on a 0-byte file "succeeds" with an empty db — that is a
    # backup of nothing and must not pass as a backup.
    (books / "news.db").write_bytes(b"")
    with pytest.raises(RuntimeError, match="news"):
        backup.backup_all()


def test_missing_source_raises(books):
    (books / "portfolio.db").unlink()
    with pytest.raises(RuntimeError, match="portfolio"):
        backup.backup_all()


def test_only_takes_several_names(books):
    """deploy.sh passes `--only portfolio news` — market is excluded."""
    assert backup.main(["--only", "portfolio", "news"]) == 0
    made = {p.name for p in (books / "backup").iterdir()}
    assert made == {"portfolio.db.mon", "news.db.mon"}


def test_main_exit_codes(books, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("src.core.notify.send", lambda text, **kw: sent.append(text))

    assert backup.main(["--only", "news"]) == 0
    assert sent == []

    (books / "news.db").write_bytes(b"garbage" * 64)
    assert backup.main(["--only", "news"]) == 1
    assert len(sent) == 1 and "news" in sent[0]


def test_market_single_slot_overwritten(books):
    out = backup.backup_all()
    dst = books / "backup" / "market.db.latest"          # no weekday suffix
    assert out["market"]["path"] == dst and _rows(dst) == 9
    sqlite3.connect(books / "market.db").execute("DELETE FROM t").connection.commit()
    backup.backup_all()
    assert _rows(dst) == 0
    assert [p.name for p in (books / "backup").glob("market*")] == ["market.db.latest"]
