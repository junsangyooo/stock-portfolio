"""Whole-universe backfill into data/market.db.

The per-symbol path (`gap_filler.fill_one`, `fundamental_backfill.fill_one`) is
one network round-trip per symbol. That was right for the ~50 symbols somebody
held; for 11,000 it is 11,000 calls. Every source here has a bulk form, and
using it is the difference between minutes and days:

    KR bars + valuation  pykrx, whole market for one date   ~740 calls (3y)
    KR statements        DART fnlttMultiAcnt, 100 corps/call  ~400 calls
    US bars              yfinance batch download              ~140 calls
    US statements        SEC companyfacts.zip                    1 download

Every step is resumable: it asks the database what it already has and skips
that unit, so a killed run is restarted, not repeated.

    python -m src.market.pipeline.universe_backfill kr-daily [--years 3]
    python -m src.market.pipeline.universe_backfill kr-financials [--years 4]
    python -m src.market.pipeline.universe_backfill us-daily [--years 3]
    python -m src.market.pipeline.universe_backfill us-financials
    python -m src.market.pipeline.universe_backfill all
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ...core.kst import KST
from ...core.paths import MARKET_DIR, UNIVERSE_DIR
from ..store.db import connect

log = logging.getLogger(__name__)

_NY = "America/New_York"

# A real KRX session writes ~2,800 stock rows + ~900 ETF rows. Anything under
# this on a given date means only the per-symbol path has touched it.
_BULK_DAY_MIN = 1000


def _log(msg: str) -> None:
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


# ── universe ────────────────────────────────────────────────────────────────
def _universe(market: str) -> list[dict]:
    p = UNIVERSE_DIR / f"{market.lower()}.json"
    if not p.exists():
        raise RuntimeError(f"universe missing: {p} — run src.universe.refresh_job")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else (raw.get("symbols") or raw.get("items") or [])


def _kst_midnight(d) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())


def _business_days(years: int) -> list:
    """Weekdays from today back `years`, newest first. Market holidays are not
    filtered — the source returns nothing for them and the day is skipped."""
    today = datetime.now(KST).date()
    days = []
    for i in range(int(years * 365.25) + 1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            days.append(d)
    return days


# ── KR: bars + valuation, one call per day for the whole market ─────────────
def kr_daily(years: int = 3, sleep_s: float = 0.15) -> dict:
    """pykrx returns every ticker's bar (and every ticker's PER/PBR) for a
    single date in one call, so the loop is over dates, not symbols."""
    from pykrx import stock as pkx

    done = skipped = holidays = 0
    bars = vals = 0
    for d in _business_days(years):
        ymd = d.strftime("%Y%m%d")
        ts = _kst_midnight(d)
        with connect() as c:
            # Not "any row exists": the ~50 held symbols already have recent
            # bars from gap_filler, and that would skip the whole market for
            # every recent day. A bulk day writes thousands of rows.
            # ponytail: a row count is the cheap stand-in for a progress table;
            # add one if a step ever needs finer resumability than per-day.
            n = c.execute("SELECT COUNT(*) FROM ohlcv WHERE ts = ?",
                          (ts,)).fetchone()[0]
        if n > _BULK_DAY_MIN:
            skipped += 1
            continue
        try:
            ohlcv = pkx.get_market_ohlcv(ymd, market="ALL")
        except Exception as e:  # noqa: BLE001 — pykrx scrapes KRX
            log.warning("kr_daily ohlcv %s failed: %s", ymd, e)
            continue
        if ohlcv is None or ohlcv.empty or float(ohlcv["종가"].abs().sum()) == 0:
            holidays += 1
            continue
        try:
            fund = pkx.get_market_fundamental_by_ticker(ymd, market="ALL")
        except Exception as e:  # noqa: BLE001 — bars are still worth keeping
            log.warning("kr_daily fundamental %s failed: %s", ymd, e)
            fund = None

        bar_rows = [
            (f"KR:{code}", ts, float(r["시가"]), float(r["고가"]),
             float(r["저가"]), float(r["종가"]), float(r["거래량"]))
            for code, r in ohlcv.iterrows()
        ]
        # ETFs are a separate listing to pykrx and absent from market="ALL";
        # without this every KR ETF would have an empty chart.
        try:
            etf = pkx.get_etf_ohlcv_by_ticker(ymd)
        except Exception as e:  # noqa: BLE001
            log.warning("kr_daily etf %s failed: %s", ymd, e)
            etf = None
        if etf is not None and not etf.empty:
            bar_rows.extend(
                (f"KR:{code}", ts, float(r["시가"]), float(r["고가"]),
                 float(r["저가"]), float(r["종가"]), float(r["거래량"]))
                for code, r in etf.iterrows())
        # market_cap rides along with the bars; the ratios need the second call
        caps = {f"KR:{code}": float(r["시가총액"])
                for code, r in ohlcv.iterrows()} if "시가총액" in ohlcv else {}
        val_rows = []
        if fund is not None and not fund.empty:
            for code, r in fund.iterrows():
                sid = f"KR:{code}"
                val_rows.append((
                    sid, ts, _f(r.get("PER")), _f(r.get("PBR")), None,
                    _f(r.get("EPS")), _f(r.get("BPS")), _f(r.get("DPS")),
                    _f(r.get("DIV")), caps.get(sid)))

        with connect() as c:
            c.executemany(
                "INSERT OR IGNORE INTO ohlcv "
                "(symbol_id, ts, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?)", bar_rows)
            if val_rows:
                c.executemany(
                    "INSERT OR REPLACE INTO valuation (symbol_id, ts, per, pbr, "
                    "psr, eps, bps, dps, div_yield, market_cap) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", val_rows)
            c.commit()
        bars += len(bar_rows)
        vals += len(val_rows)
        done += 1
        if done % 25 == 0:
            _log(f"  kr-daily {ymd}: {done} days, {bars:,} bars, {vals:,} valuations")
        time.sleep(sleep_s)
    return {"step": "kr-daily", "days": done, "skipped": skipped,
            "holidays": holidays, "bars": bars, "valuations": vals}


def _f(v) -> Optional[float]:
    """Number or None. pykrx uses 0 for 'not applicable' in the ratio table,
    and a stored 0 PER would read as 'extremely cheap' rather than 'unknown'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f == 0.0 else f


# ── KR: statements, 100 companies per call ─────────────────────────────────
def kr_financials(years: int = 4, chunk: int = 100,
                  sleep_s: float = 0.2) -> dict:
    """DART's 다중회사 주요계정 takes up to `chunk` corp_codes at a time.

    It carries only the headline accounts (매출액/영업이익/순이익/자산·부채·자본
    …), not the full statement — no gross profit, EPS or cash flows. That is
    the trade for doing 2,600 companies in ~400 calls instead of ~40,000; the
    symbols anybody actually holds still get the full `finstate_all` treatment
    from fundamental_backfill.
    """
    import requests
    from ..source.dart_source import DartSource, REPRT, REPRT_ORDER, \
        _ACCOUNT_RULES, _parse_amount
    from ..source.base import FinancialRow

    from ..source.dart_source import _api_key
    ds = DartSource()
    key = _api_key()
    if not key:
        raise RuntimeError("DART API key not found")

    codes = [s["code"] for s in _universe("kr") if not s.get("is_etf")]
    corp_of: dict[str, str] = {}
    for code in codes:
        cc = ds.corp_code(code)
        if cc:
            corp_of[cc] = code
    _log(f"  kr-financials: {len(corp_of):,} companies resolved to corp_code")

    this_year = datetime.now(KST).year
    written = calls = 0
    for year in range(this_year, this_year - years - 1, -1):
        for reprt in REPRT_ORDER:
            period = f"{year}{REPRT[reprt]}"
            corps = _pending_corps(corp_of, period)
            if not corps:
                continue
            for i in range(0, len(corps), chunk):
                batch = corps[i:i + chunk]
                try:
                    r = requests.get(
                        "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",
                        params={"crtfc_key": key, "corp_code": ",".join(batch),
                                "bsns_year": str(year), "reprt_code": reprt},
                        timeout=60)
                    payload = r.json()
                except Exception as e:  # noqa: BLE001
                    log.warning("dart multi %s %s failed: %s", year, reprt, e)
                    continue
                calls += 1
                if payload.get("status") not in ("000", "013"):
                    log.warning("dart multi %s %s status=%s %s", year, reprt,
                                payload.get("status"), payload.get("message"))
                    time.sleep(sleep_s)
                    continue
                by_corp: dict[str, list] = {}
                for row in (payload.get("list") or []):
                    by_corp.setdefault(row.get("corp_code"), []).append(row)
                rows = []
                for corp, entries in by_corp.items():
                    code = corp_of.get(corp)
                    if not code:
                        continue
                    fields = _match_accounts(entries, _ACCOUNT_RULES,
                                             _parse_amount)
                    if not fields:
                        continue
                    fr = FinancialRow(
                        period=period, fs_div="CFS",
                        report_date=f"{year}-12-31" if reprt == "11011" else "",
                        source="dart-multi", **fields)
                    rows.append((f"KR:{code}", fr))
                written += _insert_financials(rows)
                time.sleep(sleep_s)
            _log(f"  kr-financials {period}: {written:,} rows so far "
                 f"({calls} calls)")
    return {"step": "kr-financials", "rows": written, "calls": calls}


def _match_accounts(entries: list, rules, parse_amount) -> dict:
    """Run the shared account-name rules over one company's rows."""
    fields: dict = {}
    for r in entries:
        sj = str(r.get("sj_div") or "").strip()
        name = str(r.get("account_nm") or "").strip()
        amount = parse_amount(r.get("thstrm_amount"))
        if amount is None:
            continue
        for sj_set, field, subs in rules:
            if field in fields or sj not in sj_set:
                continue
            if any(sub in name for sub in subs):
                fields[field] = amount
    return fields


def _pending_corps(corp_of: dict[str, str], period: str) -> list[str]:
    """corp_codes whose symbol has no row for this period yet (resumability)."""
    with connect() as c:
        have = {r[0] for r in c.execute(
            "SELECT symbol_id FROM financials WHERE period = ? "
            "AND source = 'dart-multi'", (period,)).fetchall()}
    return [corp for corp, code in corp_of.items() if f"KR:{code}" not in have]


def _insert_financials(rows: list, filed_at: Optional[str] = None) -> int:
    """rows = [(symbol_id, FinancialRow)].

    OR IGNORE, not OR REPLACE: these carry only the headline accounts, and a
    held symbol already has the full finstate_all row for the same period.
    Replacing would quietly strip gross profit, EPS and cash flows from exactly
    the symbols that matter.
    """
    if not rows:
        return 0
    from dataclasses import asdict
    from ..store.financials_store import COLS
    with connect() as c:
        c.executemany(
            f"INSERT OR IGNORE INTO financials (symbol_id, {', '.join(COLS)}, "
            f"filed_at) VALUES ({','.join('?' * (len(COLS) + 2))})",
            [(sid, *[asdict(fr)[k] for k in COLS], filed_at)
             for sid, fr in rows])
        c.commit()
    return len(rows)


# ── US: bars, batched ──────────────────────────────────────────────────────
def us_daily(years: int = 3, chunk: int = 60, sleep_s: float = 1.0) -> dict:
    """yfinance downloads many tickers in one request.

    The bar timestamp must match what `Ticker.history` produces or every US
    bar lands twice: history returns a New-York-localized index, download
    returns a naive one, and on a UTC host those are 4-5 hours apart. The
    naive index is localized here before it becomes a ts.
    """
    import pandas as pd
    import yfinance as yf

    syms = _universe("us")
    end = datetime.now(KST).date() + timedelta(days=1)
    start = end - timedelta(days=int(years * 365.25))

    pending = _pending_us(syms)
    _log(f"  us-daily: {len(pending):,} of {len(syms):,} symbols need bars")
    written = batches = failed = 0
    for i in range(0, len(pending), chunk):
        batch = pending[i:i + chunk]
        tick_of = {(s.get("yf_ticker") or s["code"]): f"US:{s['code']}"
                   for s in batch}
        try:
            df = yf.download(" ".join(tick_of), start=str(start), end=str(end),
                             interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception as e:  # noqa: BLE001 — yfinance is scrape-flaky
            log.warning("us_daily batch %d failed: %s", i, e)
            failed += len(batch)
            continue
        if df is None or df.empty:
            failed += len(batch)
            time.sleep(sleep_s)
            continue
        idx = df.index
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize(_NY, nonexistent="shift_forward",
                                  ambiguous="NaT")
        stamps = [int(t.timestamp()) if not pd.isna(t) else None for t in idx]

        rows = []
        for ticker, sid in tick_of.items():
            try:
                sub = df[ticker] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            for ts, (_, r) in zip(stamps, sub.iterrows()):
                if ts is None or pd.isna(r.get("Close")):
                    continue
                rows.append((sid, ts, _fv(r.get("Open")), _fv(r.get("High")),
                             _fv(r.get("Low")), _fv(r.get("Close")),
                             _fv(r.get("Volume")) or 0.0))
        if rows:
            with connect() as c:
                c.executemany(
                    "INSERT OR IGNORE INTO ohlcv "
                    "(symbol_id, ts, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?,?)", rows)
                c.commit()
            written += len(rows)
        batches += 1
        if batches % 10 == 0:
            _log(f"  us-daily {i + len(batch):,}/{len(pending):,} symbols, "
                 f"{written:,} bars")
        time.sleep(sleep_s)
    return {"step": "us-daily", "batches": batches, "bars": written,
            "failed_symbols": failed}


def _fv(v) -> Optional[float]:
    import pandas as pd
    if v is None or pd.isna(v):
        return None
    return float(v)


def _pending_us(syms: list[dict]) -> list[dict]:
    with connect() as c:
        have = {r[0] for r in c.execute(
            "SELECT DISTINCT symbol_id FROM ohlcv "
            "WHERE symbol_id LIKE 'US:%'").fetchall()}
    return [s for s in syms if f"US:{s['code']}" not in have]


# ── US: statements, one bundle for the whole market ────────────────────────
_FACTS_ZIP = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"


def us_financials(years: int = 6, zip_path: Optional[Path] = None) -> dict:
    """SEC publishes every filer's XBRL facts as one bundle.

    Never unzip it: ~1.4GB compressed is tens of GB expanded, and each member
    is read, parsed and dropped here instead.
    """
    import requests
    from ..source.edgar_source import _cik_map, _UA, rows_from_facts, latest_filed

    # under data/market/ because that directory is already gitignored;
    # a 1.4GB blob one `git add -A` away from a commit is not a risk
    # worth taking for a cache file.
    zip_path = zip_path or (MARKET_DIR / "companyfacts.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if not zip_path.exists():
        _log(f"  us-financials: downloading {_FACTS_ZIP} → {zip_path}")
        tmp = zip_path.with_suffix(".zip.part")
        with requests.get(_FACTS_ZIP, headers=_UA, stream=True,
                          timeout=1800) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for block in r.iter_content(chunk_size=1 << 20):
                    f.write(block)
        tmp.replace(zip_path)
        _log(f"  us-financials: downloaded {zip_path.stat().st_size / 1e9:.2f}GB")

    # One CIK can carry several tickers (share classes); the statements are
    # the filer's, so whichever ticker wins here gets them and the sibling
    # class keeps whatever yfinance gave it.
    sid_of_cik = {cik: f"US:{tic}" for tic, cik in _cik_map().items()}
    wanted = {f"US:{s['code']}" for s in _universe("us")}
    seen = written = missing = 0
    batch: list = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue
            try:
                cik = int(name[3:-5])
            except ValueError:
                continue
            sid = sid_of_cik.get(cik)
            if sid is None or sid not in wanted:
                missing += 1
                continue
            seen += 1
            try:
                facts = json.loads(z.read(name))
            except (json.JSONDecodeError, KeyError):
                continue
            rows = rows_from_facts(facts, years=years)
            if not rows:
                continue
            filed = latest_filed(facts)
            batch.extend((sid, fr, filed) for fr in rows)
            if len(batch) >= 5000:
                written += _flush_facts(batch)
                batch = []
                _log(f"  us-financials: {seen:,} filers, {written:,} rows")
    written += _flush_facts(batch)
    return {"step": "us-financials", "filers": seen, "rows": written,
            "not_in_universe": missing}


def _flush_facts(batch: list) -> int:
    """batch = [(symbol_id, FinancialRow, filed_at)]."""
    if not batch:
        return 0
    from dataclasses import asdict
    from ..store.financials_store import COLS
    with connect() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO financials (symbol_id, {', '.join(COLS)}, "
            f"filed_at) VALUES ({','.join('?' * (len(COLS) + 2))})",
            [(sid, *[asdict(fr)[k] for k in COLS], filed)
             for sid, fr, filed in batch])
        c.commit()
    return len(batch)


# ── CLI ────────────────────────────────────────────────────────────────────
STEPS = {
    "kr-daily": lambda a: kr_daily(years=a.years),
    "kr-financials": lambda a: kr_financials(years=a.fin_years),
    "us-daily": lambda a: us_daily(years=a.years, chunk=a.chunk),
    "us-financials": lambda a: us_financials(years=a.fin_years),
}


def _counts() -> dict:
    with connect() as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("ohlcv", "valuation", "financials")}


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=[*STEPS, "all"])
    ap.add_argument("--years", type=float, default=3, help="years of bars")
    ap.add_argument("--fin-years", type=int, default=4,
                    help="years of statements")
    ap.add_argument("--chunk", type=int, default=60, help="us-daily batch size")
    args = ap.parse_args()

    todo = list(STEPS) if args.step == "all" else [args.step]
    _log(f"before: {_counts()}")
    results = []
    for name in todo:
        _log(f"── {name} ──")
        t0 = time.time()
        try:
            r = STEPS[name](args)
        except Exception as e:  # noqa: BLE001 — one step must not kill the rest
            log.exception("%s failed", name)
            r = {"step": name, "fatal": str(e)[:300]}
        r["elapsed_s"] = round(time.time() - t0, 1)
        results.append(r)
        _log(f"   {json.dumps(r, ensure_ascii=False)}")
    _log(f"after: {_counts()}")

    try:
        from ...core.notify import discord
        bad = [r for r in results if r.get("fatal")]
        discord.send_embed(
            title=f"{'🚨' if bad else '📦'} universe backfill — {args.step}",
            description="\n".join(f"- `{r['step']}` {json.dumps({k: v for k, v in r.items() if k != 'step'}, ensure_ascii=False)}"
                                  for r in results)[:4000],
            color=0xE74C3C if bad else 0x2E7D4F,
            footer=json.dumps(_counts()))
    except Exception:  # noqa: BLE001 — notification is not the job
        pass
    return 1 if any(r.get("fatal") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
