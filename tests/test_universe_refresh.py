"""A partial universe build must not overwrite a good master.

Every listing source can fail on its own — the KRX endpoints and the ETF feed
are three separate services. A build that loses one of them still returns
thousands of rows, so "did it return anything" cannot tell a healthy master
from one missing a whole exchange. The build reports source health and refresh
keeps the previous file whenever that health is not clean.
"""
import json

from src.universe import builder


def _write(path, n):
    path.write_text(json.dumps(
        {"region": "kr", "n": n,
         "items": [{"symbol_id": f"KR:{i:06d}"} for i in range(n)]}))


def test_partial_build_keeps_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(builder, "ensure_dirs", lambda: None)
    good = tmp_path / "kr.json"
    _write(good, 4000)

    # one source answered, another did not
    monkeypatch.setattr(builder, "build_kr",
                        lambda: ([{"symbol_id": "KR:069500"}], False))
    summary = builder.refresh(region="kr")

    assert summary["kept_previous"] == ["kr"]
    assert "kr" not in summary["regions"]
    assert json.loads(good.read_text())["n"] == 4000


def test_healthy_build_replaces(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(builder, "ensure_dirs", lambda: None)
    good = tmp_path / "kr.json"
    _write(good, 4000)

    monkeypatch.setattr(builder, "build_kr",
                        lambda: ([{"symbol_id": "KR:005930"}], True))
    summary = builder.refresh(region="kr")

    assert summary["regions"]["kr"] == 1
    assert not summary["kept_previous"]
    assert json.loads(good.read_text())["items"] == [{"symbol_id": "KR:005930"}]
