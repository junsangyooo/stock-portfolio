"""The ceiling on one tool answer.

An agent loop re-sends every earlier tool result on every later turn, so an
uncapped answer is not paid for once. What matters here is that the cut is
announced — a silent trim reads as "that is all there is".
"""
import json

from src.portfolio.chat import tools as chat_tools


def test_a_normal_answer_passes_through_untouched():
    out = {"symbol_id": "KR:005930", "price": 71000}
    assert json.loads(chat_tools.dump_capped(out)) == out


def test_a_flood_is_trimmed_and_says_so():
    out = {"market": "NASDAQ", "rows": [{"sym": f"S{i}", "name": "x" * 50}
                                        for i in range(20_000)]}
    s = chat_tools.dump_capped(out)
    got = json.loads(s)
    assert len(s) <= chat_tools._MAX_OUT_CHARS
    assert 0 < len(got["rows"]) < 20_000
    assert got["_truncated"]["rows"].endswith("/20000")
    assert got["market"] == "NASDAQ"          # the shape survives the cut


def test_the_longest_list_is_the_one_that_gives():
    out = {"note": ["한 줄"], "bars": [{"c": 1.0, "v": 2.0} for _ in range(50_000)]}
    got = json.loads(chat_tools.dump_capped(out))
    assert got["note"] == ["한 줄"]
    assert "bars" in got["_truncated"] and "note" not in got["_truncated"]


def test_a_single_huge_value_degrades_to_a_head_that_still_parses():
    """Nothing structural to trim — the answer must still be JSON."""
    s = chat_tools.dump_capped({"blob": "가" * 100_000})
    assert len(s) <= chat_tools._MAX_OUT_CHARS
    got = json.loads(s)
    assert got["_truncated"].endswith("질의를 좁혀라") and got["head"]
