# stock-portfolio

A self-hosted stock research and portfolio tracking app I build and run for my own investing.

It follows several of my own brokerage accounts across Korean and US markets, keeps a daily record
of holdings, P&L and trades, and puts market data, financial statements, chart structure and news
next to each holding. On top of that, LLM calls turn the collected data into a per-symbol analysis,
daily news briefings by theme, and a chat assistant that can look things up in the account and the
market on its own.

It is a personal project, not a product: every account in it is one of mine, each is PIN-locked,
and the code is shaped around one person's workflow.

---

## What it does

- **Portfolio book** — holdings, cash, trade history and daily asset snapshots per account, in KRW
  and USD, with P&L against live quotes.
- **Market data** — daily OHLCV, quotes, financial statements, valuation, sector tags and ETF facts
  for Korean and US symbols, with a symbol master covering KOSPI, KOSDAQ, KR ETFs, NASDAQ, NYSE
  and AMEX.
- **Chart engine** — 15 technical indicators across trend, momentum, volatility and volume, plus
  structure detection (swing extrema, support/resistance levels, trendlines, divergences,
  patterns) on daily, weekly and monthly bars, with a guard against reading unfinished bars.
- **Fundamentals** — standardized statements, derived trend series, quality scores
  (Piotroski F, Altman Z, Sloan accruals, earnings quality), valuation bands and peer percentiles
  within the same sector.
- **News pipeline** — collects articles from a curated set of feeds, tags them to themes, and
  compresses them into daily, weekly and monthly summaries per theme.
- **Comprehensive analysis** — one LLM call per symbol that reads chart, fundamentals and news
  together and returns a verdict, target and invalidation levels, and a counter-case.
- **Chat** — a per-account assistant that answers questions by calling a small, read-only
  lookup CLI (holdings, trades, quotes, charts, fundamentals, news, screeners).

## Architecture

```
                         ┌───────────────────────────────────────────┐
  Browser  ── HTTPS ──▶  │  Cloudflare  ──▶  nginx  ──▶  FastAPI     │   Oracle Cloud VM (ARM)
                         │                              (uvicorn)    │
                         └──────────────────────────────────┬────────┘
                                                            │
        ┌───────────────────────────────────────────────────┼──────────────────┐
        │  app layer                                        ▼                  │
        │    portfolio   accounts · holdings · trades · snapshots · chat       │
        │    api         routing only                                          │
        │    dashboard_web  server-rendered pages + shared CSS/JS              │
        └───────────────────────────────┬──────────────────────────────────────┘
                                        │ reads (never the other way)
        ┌───────────────────────────────▼──────────────────────────────────────┐
        │  shared layer                                                        │
        │    universe   symbol master (KR + US)                                │
        │    market     sources → stores → pipelines (OHLCV, statements, ETF)  │
        │    research   chart · fundamental · news · analyze                   │
        │    core       paths · secrets · LLM backends · jobs · backup · usage │
        └───────────────┬───────────────────────────────┬──────────────────────┘
                        │                               │
              SQLite books (WAL)                   LLM backends
          portfolio.db · market.db · news.db    claude -p  ·  local llama.cpp
```

**Dependencies point one way.** The app layer may read the shared layer; the shared layer never
reads an account. Market data, research and analysis take a list of symbols and know nothing
about who holds them, so one stored analysis serves every account. `tests/test_layering.py`
walks the AST of every module and fails the build if an import goes the wrong way
(`core ← universe · market · research ← portfolio ← api ← dashboard_web`).

**Three databases, each with one owner.** `portfolio.db` is the account book and belongs to the
app. `market.db` and `news.db` are shared facts any layer can read. All three are SQLite in WAL
mode, backed up nightly with `VACUUM INTO` and an integrity check before the copy replaces the
previous one.

**Scheduled jobs do the collecting; the web process only reads.** Cron runs the news cycle,
fundamentals, ETF snapshots, closing snapshots after each market's close, the KR screener cache,
the symbol master refresh and the evening analysis batch. The schedule lives in
`deploy/portfolio.cron` and is installed between markers so it can share a crontab with other
apps.

## AI layer

| Task | Engine | Notes |
|---|---|---|
| Comprehensive analysis | Claude via `claude -p` (Sonnet 5.5) | One call sees chart, fundamentals and news at once |
| Article enrichment | Claude via `claude -p` (Haiku 5.5); local Qwen on KST weekends | Summary, importance, novelty and themes in one batched call |
| Daily theme briefing | Local Qwen on llama.cpp, Haiku fallback | Reads already-compressed one-line summaries |
| Weekly / monthly roll-ups | Local Qwen on llama.cpp, Haiku fallback | One call per theme over the layer below |
| Symbol → theme tagging | Claude via `claude -p` (Sonnet 5.5) | ETFs are mapped by a deterministic name rule instead |
| Account chat | Claude via `claude -p` (Opus 5.5) | Multi-turn, resumable sessions, tool use through a lookup CLI |

Design choices that shape the AI layer:

- **CLI, not a paid API.** Judgment calls run through the `claude -p` subprocess on a
  subscription; mechanical summarization runs on an open-weight model served by llama.cpp on the
  same host. Every call's token usage is appended to a JSONL log, so spend can be traced by stage.
- **One call over all domains.** The analysis is deliberately not split into chart, fundamental
  and news calls merged afterwards: evidence has to meet before the judgment, not after.
- **Deterministic inputs, shallow validation.** Indicators, structure, fundamentals and peer
  percentiles are computed in Python and handed to the model as `{id, reference, data, status}`
  envelopes. Output enums are coerced and numbers clamped, but a missing level stays missing;
  nothing is invented to fill a gap.
- **Re-analyze only on change.** A symbol is re-analyzed when price crosses a stored level, a new
  filing lands, high-importance news names the company, the price moves enough, or the result
  ages out — not every symbol every day.
- **Scored afterwards.** `research/analyze/score.py` checks stored analyses against what the price
  did over 5, 10 and 20 trading days, so prompt changes can be compared by numbers.
- **Compression flows one way.** Articles → daily → weekly → monthly. Upper layers never re-read
  the layer two below, which keeps long-term memory a fixed size.
- **Long calls run in the background.** Analysis and chat turns run on in-process background jobs;
  the browser polls a job id instead of holding a request open past proxy limits.
- **The chat is sandboxed to one account.** The assistant may run exactly one command family, from
  an empty working directory, and its account binding is signed by the server. No command takes an
  account id, credential-shaped environment variables are stripped from the subprocess, and the
  CLI session transcripts are wiped nightly.

## Data and APIs

| Data | Source |
|---|---|
| KR prices, symbol master, market-wide fundamentals | KRX via `pykrx`, `FinanceDataReader` |
| US prices, KR fallback, sector tags | `yfinance` |
| KR financial statements | OpenDART API |
| US financial statements (full XBRL history) | SEC EDGAR `companyfacts` API |
| US conditional screener | Korea Investment & Securities (KIS) Open API |
| KR ETF list with NAV and AUM | Naver Finance ETF endpoint |
| News and research | A curated set of feeds across 23 themes |
| Alerts | Discord webhook |

Each market source is an adapter behind a common interface, and a failed fetch raises instead of
returning an empty frame, so a dead source never looks like a symbol with no data. Outbound calls
go through token-bucket rate limiters. KR valuation (PER, PBR, market cap) is derived from DART
statements and the close series, since no free source provides it reliably.

## Stack

- **Backend:** Python 3, FastAPI, Uvicorn, Pydantic, pandas, NumPy, SciPy, PyArrow (parquet caches),
  SQLite
- **Data clients:** requests, httpx, BeautifulSoup, lxml, pykrx, FinanceDataReader,
  yfinance, OpenDartReader
- **AI:** Claude Code CLI (`claude -p`, Haiku / Sonnet / Opus 5.5), llama.cpp in Docker serving Qwen
- **Frontend:** server-rendered HTML with vanilla JS and CSS, hand-drawn SVG charts, design tokens
  exported from Figma into `theme.json`, Pretendard variable font, Lucide icons — no build step
- **Infra:** Oracle Cloud ARM VM, systemd, nginx, Cloudflare (proxy + TLS), cron, logrotate
- **Tests:** pytest

## Security

- Every account requires a 6-digit PIN at creation; an account without one is closed. PINs are
  hashed with a server-side secret, sessions are signed cookies scoped to one account, and every
  authorization failure is a plain 403.
- Opening a new account also takes the owner's creation key, typed once in the add dialog; after
  that the account's own PIN is all it needs. The key is set on the server with
  `python -m src.portfolio.security set-create-key` and kept as a secret-salted hash. Until it is
  set, no account can be created from the site, and five wrong keys close creation for 15 minutes.
- nginx only accepts traffic that arrives through Cloudflare; the app listens on localhost only.
- Each response carries a strict Content-Security-Policy with a per-request nonce; pages have no
  inline `style` or `on*` handlers and no static file mount.
- The systemd unit unsets credentials the web process does not need and hides the secrets folder
  with `InaccessiblePaths=`.
- Tests block real network calls, webhooks and model calls by default.

## Repository layout

```
stock-portfolio/
├── deploy/            deploy.sh · systemd unit · nginx config · crontab block · logrotate · icon builder
├── src/
│   ├── core/          paths · KST time · secrets · rate limits · LLM backends · jobs · usage · backup
│   ├── universe/      symbol master build, refresh and search
│   ├── market/        source adapters · stores · pipelines · calendar · peers · KR valuation
│   ├── research/
│   │   ├── chart/        indicators/ · structure/ · multi-timeframe engine
│   │   ├── fundamental/  statement assembly and quality metrics
│   │   ├── news/         fetch/ · enrich · mapping · daily · roll-ups · reverse lookup
│   │   └── analyze/      inputs · prompt · engine · due · score
│   ├── portfolio/     store/ · service · security · chat/ · supply and snapshot jobs
│   ├── api/           routers: universe · accounts · market · analysis · chat
│   └── dashboard_web/ server.py · static/ (news, account, sources pages and fragments)
└── tests/             mirrors src/, plus layering, deploy and UI invariant tests
```

## Pages

| Page | Path | Content |
|---|---|---|
| News | `/` | Mood by theme, today's key points, news flow |
| Account | `/a/{account_id}` | Holdings with verdict badges, per-symbol analysis, trade history, chat |
| Sources | `/sources` | Health of each data source and scheduled job |

The server injects theme tokens, `shared.css` and `shared.js` into each page and inlines its
fragments through `<!--#include …-->`. UI tests enforce the rules mechanically: no color or font
size literals outside the tokens, mobile-first breakpoints, a fixed set of button styles.

## API

```
/api/universe/*     symbol search
/api/accounts/*     holdings · trades · cash · snapshots · chat     (account cookie)
/api/market/*       OHLCV · quotes
/api/analysis/*     comprehensive analysis · chart · fundamentals · news
```

## Running locally

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.lock.txt
set -a && source ~/.claude/secrets/.env && set +a     # KRX, DART, KIS, Discord keys

python -m src.dashboard_web.server --port 8888
```

Tests run from the same environment in another shell:

```bash
pytest tests/
```

The analysis and chat features need the `claude` CLI signed in; the daily and roll-up summaries
need a llama.cpp server on `LOCAL_LLM_URL` (default `http://127.0.0.1:8080`).

## Deploying

The laptop is the source of truth and the server only pulls.

```bash
./deploy/deploy.sh               # push → server pull → DB backup
./deploy/deploy.sh dashboard     # + restart the service and health-check
./deploy/deploy.sh rollback      # back to the commit before the last deploy
```
