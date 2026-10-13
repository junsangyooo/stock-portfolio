"""The background job runner behind 다시 분석 and every chat turn."""
import time

from src.core import jobs


def _wait(r, job_id, until=("done", "error")):
    for _ in range(200):
        j = r.get(job_id)
        if j["state"] in until:
            return j
        time.sleep(0.01)
    raise AssertionError("job never settled")


def test_nothing_started_is_none():
    r = jobs.Runner(stale_s=60)
    assert r.latest("KR:000001") is None and not r.running("KR:000001")


def test_done_keeps_the_result():
    r = jobs.Runner(stale_s=60)
    jid, started = r.start("KR:000002", lambda: {"date": "2026-06-13"}, account_id="a")
    j = _wait(r, jid)
    assert started and j["state"] == "done" and j["result"] == {"date": "2026-06-13"}
    assert j["account_id"] == "a" and r.latest("KR:000002")["job_id"] == jid


def test_error_carries_the_message():
    r = jobs.Runner(stale_s=60)

    def boom():
        raise RuntimeError("claude CLI exit 1: overloaded")
    j = _wait(r, r.start("KR:000003", boom)[0])
    assert j["state"] == "error" and "overloaded" in j["error"]


def test_a_live_key_is_not_started_twice():
    r = jobs.Runner(stale_s=60)
    gate = {"go": False}

    def slow():
        while not gate["go"]:
            time.sleep(0.01)
        return "ok"
    first, s1 = r.start("KR:000004", slow)
    second, s2 = r.start("KR:000004", slow)
    assert s1 and not s2 and first == second and r.running("KR:000004")
    gate["go"] = True
    assert _wait(r, first)["state"] == "done"


def test_a_job_past_stale_reads_stale_and_frees_its_key():
    r = jobs.Runner(stale_s=0)
    gate = {"go": False}
    jid, _ = r.start("t", lambda: gate["go"] or time.sleep(0.2))
    time.sleep(0.01)
    assert r.get(jid)["state"] == "stale" and not r.running("t")
    assert r.start("t", lambda: "x")[1]          # a new run may start


def test_forget_drops_a_keys_jobs():
    r = jobs.Runner(stale_s=60)
    jid, _ = r.start("thread", lambda: "x")
    _wait(r, jid)
    r.forget("thread")
    assert r.get(jid) is None and r.latest("thread") is None
