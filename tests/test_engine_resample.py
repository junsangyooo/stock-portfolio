"""C0 — engine: daily->weekly/monthly resample correctness + look-ahead guard (D6)."""
import unittest
from datetime import datetime, timezone

import pandas as pd

from src.research.chart import engine


def _ts(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _daily_two_weeks() -> pd.DataFrame:
    # 2024-01-01(Mon)..01-05(Fri)  and  01-08(Mon)..01-12(Fri)
    rows = []
    week1 = [(1, 10, 12, 9, 11, 100), (2, 11, 13, 10, 12, 110),
             (3, 12, 14, 11, 13, 120), (4, 13, 15, 12, 14, 130),
             (5, 14, 16, 13, 15, 140)]
    week2 = [(8, 20, 22, 19, 21, 200), (9, 21, 23, 20, 22, 210),
             (10, 22, 24, 21, 23, 220), (11, 23, 25, 22, 24, 230),
             (12, 24, 26, 23, 25, 240)]
    for d, o, h, l, c, v in week1 + week2:
        rows.append({"ts": _ts(2024, 1, d), "open": o, "high": h,
                     "low": l, "close": c, "volume": v})
    return pd.DataFrame(rows)


class TestResample(unittest.TestCase):
    def setUp(self):
        self.daily = _daily_two_weeks()

    def test_daily_passthrough(self):
        out = engine.to_timeframe(self.daily, "1d")
        self.assertEqual(len(out), 10)

    def test_weekly_ohlc_aggregation(self):
        wk = engine.to_timeframe(self.daily, "1w")
        self.assertEqual(len(wk), 2)
        # week1: open=first(10), high=max(16), low=min(9), close=last(15), vol=sum(600)
        r1 = wk.iloc[0]
        self.assertEqual(r1["open"], 10)
        self.assertEqual(r1["high"], 16)
        self.assertEqual(r1["low"], 9)
        self.assertEqual(r1["close"], 15)
        self.assertEqual(r1["volume"], 600)
        # week2
        r2 = wk.iloc[1]
        self.assertEqual(r2["open"], 20)
        self.assertEqual(r2["high"], 26)
        self.assertEqual(r2["low"], 19)
        self.assertEqual(r2["close"], 25)
        self.assertEqual(r2["volume"], 1100)

    def test_weekly_ts_is_epoch_seconds(self):
        # W-FRI labels the two weeks 2024-01-05 and 2024-01-12. Pinning the
        # absolute value guards resampler.resample's unit-agnostic conversion:
        # a nanosecond/second mix-up puts every bar on 1970-01-01 instead.
        wk = engine.to_timeframe(self.daily, "1w")
        self.assertEqual(list(wk["ts"]), [_ts(2024, 1, 5), _ts(2024, 1, 12)])

    def test_monthly_aggregation(self):
        mo = engine.to_timeframe(self.daily, "1mo")
        self.assertEqual(len(mo), 1)
        r = mo.iloc[0]
        self.assertEqual(r["open"], 10)
        self.assertEqual(r["high"], 26)
        self.assertEqual(r["low"], 9)
        self.assertEqual(r["close"], 25)
        self.assertEqual(r["volume"], 1700)

    def test_unknown_tf_raises(self):
        with self.assertRaises(ValueError):
            engine.to_timeframe(self.daily, "1h")


class TestLookAheadGuard(unittest.TestCase):
    def setUp(self):
        self.daily = _daily_two_weeks()
        self.weekly = engine.to_timeframe(self.daily, "1w")   # last bar ts = 2024-01-12 (Fri)

    def test_drop_when_now_in_same_week(self):
        # now in the SAME iso-week as the last weekly bar -> in progress -> drop
        now = datetime(2024, 1, 11, tzinfo=timezone.utc)      # Thu of week2
        out = engine.drop_incomplete_bar(self.weekly, "1w", now=now)
        self.assertEqual(len(out), 1)                         # last (in-progress) dropped

    def test_keep_when_now_next_week(self):
        now = datetime(2024, 1, 15, tzinfo=timezone.utc)      # Mon of week3
        out = engine.drop_incomplete_bar(self.weekly, "1w", now=now)
        self.assertEqual(len(out), 2)                         # both closed -> kept

    def test_daily_never_dropped(self):
        now = datetime(2024, 1, 5, tzinfo=timezone.utc)
        out = engine.drop_incomplete_bar(self.daily, "1d", now=now)
        self.assertEqual(len(out), 10)

    def test_monthly_drop_in_same_month(self):
        monthly = engine.to_timeframe(self.daily, "1mo")      # ts = 2024-01-01
        same = datetime(2024, 1, 20, tzinfo=timezone.utc)
        later = datetime(2024, 2, 1, tzinfo=timezone.utc)
        self.assertEqual(len(engine.drop_incomplete_bar(monthly, "1mo", now=same)), 0)
        self.assertEqual(len(engine.drop_incomplete_bar(monthly, "1mo", now=later)), 1)

    def test_last_closed_date(self):
        self.assertEqual(engine.last_closed_date(self.weekly), "2024-01-12")
        self.assertIsNone(engine.last_closed_date(self.weekly.iloc[0:0]))



if __name__ == "__main__":
    unittest.main()
