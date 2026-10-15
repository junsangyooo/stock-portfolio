"""Tests for the comprehensive analysis.

Two things matter here and both are about not letting the model's output pass
as fact: prices are checked against levels the chart pipeline actually
detected, and a failed call produces an honest blank rather than a synthesized
number.
"""
from __future__ import annotations

import json

import pytest

from src.research.analyze import engine as run, prompt


def _envelope(structure=None, domains=("chart", "fundamental", "news")):
    data = {"structure": structure} if structure is not None else {"structure": {}}
    return {
        "symbol": {"symbol_id": "KR:005930", "name": "삼성전자", "market": "KR"},
        "asof": "2026-09-01",
        "domains": [
            {"id": d, "title_ko": d, "reference": f"# {d} 사전",
             "data": (data if d == "chart" else {"x": 1}),
             "status": "ok", "note_ko": ""}
            for d in domains
        ],
        "context": {"peer": {"metrics": {}}},
        "available": list(domains),
    }


_STRUCT = {
    "support_resistance": {"levels": [{"price": 70000, "tests": 4},
                                      {"price": 64000, "tests": 2}]},
    "fibonacci": {"levels": {"0.382": 72500, "0.618": 68000}},
    "pivots": {"PP": 71000, "R1": 73000, "S1": 69000},
}


# ── price anchoring ─────────────────────────────────────────────────────────

def test_anchors_are_pulled_from_every_structure_source():
    a = run.anchors_from(_envelope(_STRUCT))
    prices = sorted(p for p, _ in a)
    assert prices == [64000, 68000, 69000, 70000, 71000, 72500, 73000]
    assert dict(a)[70000] == 4          # test count survives


def test_a_quoted_price_snaps_to_the_detected_level():
    a = run.anchors_from(_envelope(_STRUCT))
    got = run._anchor(70200, a)          # 0.29% away
    assert got["price"] == 70000 and got["anchored"] is True
    assert got["anchor_tests"] == 4 and got["quoted"] == 70200


def test_a_price_from_nowhere_is_kept_but_flagged():
    """Never dropped — the reader wants to see it, and needs to know it is
    not grounded."""
    a = run.anchors_from(_envelope(_STRUCT))
    got = run._anchor(51234, a)
    assert got["price"] == 51234
    assert got["anchored"] is False and got["llm_only"] is True


def test_without_structure_nothing_claims_to_be_anchored():
    got = run._anchor(70000, [])
    assert got["price"] == 70000 and got["anchored"] is False
    assert "llm_only" not in got        # unverifiable is not the same as invented


def test_levels_anchor_both_the_bands_and_the_invalidation():
    a = run.anchors_from(_envelope(_STRUCT))
    out = run._levels({
        "current": 71200,
        "support": [{"price": 69980, "basis_ko": "S/R", "strength": "strong"}],
        "resistance": [{"price": 99999, "basis_ko": "지어냄"}],
        "invalidation": {"price": 64050, "rationale_ko": "이탈 시"},
        "entry_zone": {"low": 68000, "high": 70000, "rationale_ko": ""},
        "targets": [{"price": 73000, "basis_ko": "R1"}],
    }, a)
    assert out["support"][0]["price"] == 70000 and out["support"][0]["anchored"]
    assert out["resistance"][0]["llm_only"] is True
    # the flat number stays readable while the anchoring rides alongside
    assert out["invalidation"]["price"] == 64000
    assert out["invalidation"]["anchoring"]["price"]["anchored"] is True
    assert out["entry_zone"]["low"] == 68000 and out["entry_zone"]["high"] == 70000
    assert out["targets"][0]["price"] == 73000


def test_a_non_numeric_price_is_dropped_not_coerced():
    out = run._levels({"support": [{"price": "약 7만원"}, {"price": None},
                                   {"price": 70000}]},
                      run.anchors_from(_envelope(_STRUCT)))
    assert [s["price"] for s in out["support"]] == [70000]


# ── the output contract ─────────────────────────────────────────────────────

class _Backend:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call(self, system, user, model=None):
        self.calls.append(user)
        return json.dumps(self.payload)


_OK = {
    "observations": [{"domain": "chart", "fact_ko": "RSI 71", "cite": "rsi"}],
    "readings": [{"claim_ko": "과열", "from": [0], "confidence": 0.7}],
    "counter": {"case_ko": "추세장에서는 과매수가 지속된다",
                "what_would_show_it": ["ADX 상승 지속"]},
    "verdict": {"score": 0.4, "label": "buy", "confidence": 0.55,
                "summary_ko": "요약",
                "drivers": ["a"], "risks": ["b"],
                "domain_view": {"chart": {"one_line_ko": "x", "lean": "bullish"}}},
    "outlook": {"horizon": "near", "base_ko": "기본",
                "scenarios": [{"case": "bull", "narrative_ko": "n",
                               "confirm": ["c"], "invalidate": ["i"]}]},
    "levels": {"current": 71200},
    "reassess_when": ["실적 발표"], "limitations": ["월봉 부족"],
}


def test_an_empty_envelope_never_reaches_the_model():
    """No data at all is not a question worth asking."""
    out = run.analyze("KR:000000", store=False)
    assert out["status"] == "facts_only"
    assert "전혀 없" in out["limitations"][0]


def test_a_good_response_passes_through_with_its_shape_intact():
    out = run.normalize(_OK, _envelope(_STRUCT))
    assert out["score"] == 0.4 and out["label"] == "buy"
    assert out["readings"][0]["from"] == [0]
    assert out["outlook"]["scenarios"][0]["case"] == "bull"
    assert out["domain_view"]["news"]["lean"] == "neutral"   # absent → filled flat


def test_a_reading_citing_an_observation_that_does_not_exist_loses_the_citation():
    bad = {**_OK, "readings": [{"claim_ko": "근거 없음", "from": [7, 0]}]}
    out = run.normalize(bad, _envelope(_STRUCT))
    assert out["readings"][0]["from"] == [0]


@pytest.mark.parametrize("label,expect", [("buy", "buy"), ("무엇", "buy")])
def test_an_unknown_label_falls_back_to_the_score(label, expect):
    out = run.normalize({**_OK, "verdict": {**_OK["verdict"], "label": label}},
                        _envelope(_STRUCT))
    assert out["label"] == expect


@pytest.fixture
def assembled(monkeypatch):
    """Skip the deterministic pass — it reads the real stores."""
    env = _envelope(_STRUCT)
    monkeypatch.setattr(run.inputs, "build", lambda *a, **kw: env)
    return env


def test_a_failed_call_returns_facts_only_and_invents_nothing(assembled):
    """The old judge averaged cached domain verdicts. With one call there is
    nothing to average, and a number from nowhere is worse than a blank."""
    class _Boom:
        def call(self, *a, **kw):
            raise RuntimeError("claude CLI timeout")

    out = run.analyze("KR:005930", "삼성전자", backend=_Boom(), store=False)
    assert out["status"] == "facts_only"
    assert out["score"] == 0.0 and out["confidence"] == 0.0
    assert out["levels"]["invalidation"] is None
    assert any("timeout" in x for x in out["limitations"])


def test_the_call_is_retried_once_before_giving_up(assembled):
    calls = []

    class _Flaky:
        def call(self, system, user, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return json.dumps(_OK)

    out = run.analyze("KR:005930", "삼성전자", backend=_Flaky(), store=False)
    assert len(calls) == 2 and out["status"] == "ok"


# ── the prompt envelope ─────────────────────────────────────────────────────

def test_each_domain_arrives_paired_with_its_own_dictionary():
    text = prompt.build(_envelope(_STRUCT))
    for d in ("chart", "fundamental", "news"):
        assert f"# {d} 사전" in text
    assert text.index("# chart 사전") < text.index("# fundamental 사전")


def test_the_invariant_half_of_the_prompt_comes_first():
    """The dictionaries and the output contract are ~38,000 characters that
    never change; only the symbol's data does. With the symbol name on line 1
    no two of the ~70 daily calls shared a prefix, so a cache had nothing to
    hold onto."""
    text = prompt.build(_envelope(_STRUCT))
    head = text.index("여기부터가 이 종목이다")
    assert text.index("# chart 사전") < head          # dictionaries lead
    assert text.index('"observations"') < head        # so does the contract
    assert head < text.index("삼성전자")               # then this symbol
    assert head < text.index("데이터: chart")          # and its numbers


def test_a_missing_domain_says_so_instead_of_vanishing():
    env = _envelope(_STRUCT)
    env["domains"][1].update(status="missing", data=None, note_ko="재무 없음")
    text = prompt.build(env)
    assert "[missing]" in text and "재무 없음" in text
    assert "# fundamental 사전" not in text      # no dictionary without data
    assert "신뢰도에만 반영" in text


# ── the read side: what an orchestrator calls ───────────────────────────────

@pytest.fixture
def stored(tmp_path, monkeypatch):
    """Point the analysis store at a temp tree and write two days for one
    symbol."""
    import json as _json
    import src.core.paths as paths
    monkeypatch.setattr(paths, "ANALYSIS_DIR", tmp_path)
    from src.research.analyze import read as rd
    d = tmp_path / "KR_005930"
    d.mkdir(parents=True)
    for day, label in (("20260830", "neutral"), ("20260901", "buy")):
        (d / f"{day}.json").write_text(_json.dumps(
            {"symbol_id": "KR:005930", "label": label, "score": 0.4,
             "confidence": 0.6, "status": "ok", "summary_ko": f"{day} 요약"}),
            encoding="utf-8")
    return rd


def test_latest_takes_the_newest_day(stored):
    got = stored.latest("KR:005930")
    assert got["label"] == "buy" and got["date"] == "2026-09-01"


def test_a_symbol_never_analyzed_reads_as_none(stored):
    assert stored.latest("KR:000000") is None
    assert stored.latest_date("KR:000000") is None
    assert stored.on("KR:000000", "2026-09-01") is None


def test_latest_date_does_not_parse_the_body(stored, monkeypatch):
    """A result carries the whole chart fact set; a badge row must not pay to
    load a hundred of them."""
    monkeypatch.setattr(stored, "_read",
                        lambda p: pytest.fail("body was parsed"))
    assert stored.latest_date("KR:005930") == "2026-09-01"


def test_on_accepts_either_date_spelling(stored):
    assert stored.on("KR:005930", "2026-08-30")["label"] == "neutral"
    assert stored.on("KR:005930", "20260830")["label"] == "neutral"


def test_a_truncated_file_reads_as_missing_not_as_a_crash(stored, tmp_path):
    (tmp_path / "KR_005930" / "20260902.json").write_text(
        "{not json", encoding="utf-8")
    assert stored.latest("KR:005930") is None      # newest is unreadable


def test_summary_answers_for_every_symbol_asked(stored):
    got = stored.summary(["KR:005930", "KR:000000"])
    assert set(got) == {"KR:005930", "KR:000000"}
    assert got["KR:005930"]["label"] == "buy"
    # never analyzed is an answer, not a missing key
    assert got["KR:000000"]["date"] is None


def test_summary_carries_the_coordinates_a_card_reads(stored, tmp_path):
    """The human book keeps no plans: a card states the analysis's own stop,
    target and entry zone against the holding, so the batch carries them."""
    (tmp_path / "KR_005930" / "20260903.json").write_text(json.dumps({
        "date": "2026-09-03", "label": "sell", "status": "ok",
        "levels": {"current": 7520, "invalidation": {"price": 7930},
                   "targets": [{"price": 6563}, {"price": 6000}],
                   "entry_zone": {"low": 7000, "high": 7100}}}), encoding="utf-8")
    got = stored.summary(["KR:005930", "KR:000000"])
    assert got["KR:005930"]["coords"] == {"stop": 7930, "target": 6563,
                                          "entry_low": 7000, "entry_high": 7100}
    assert got["KR:000000"]["coords"] is None


def test_the_package_exposes_one_entry_point():
    """A caller should not have to know which file a function lives in."""
    from src.research import analyze
    for name in ("run", "assemble", "latest", "on", "due_among", "summary"):
        assert callable(getattr(analyze, name)), name
