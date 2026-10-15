# portfolio-dashboard

주식 분석 프로그램 **portfolio**(`portfolio.jsyoo.dev`)의 저장소다. 여러 증권계좌(사람 계좌 4개 — ISA·일반·연금·해외)를 팔로업하고 분석한다: 보유 조회·손익·거래 기록·종목 분석·채팅.

**quant.jsyoo.dev는 같은 저장소의 새 앱이다** — `autotrade/`(엔진·API)와 `web/`(화면). 새 앱은 `src/`를 import하지 않는다. 아래는 `src/`의 portfolio 앱 이야기다.

```
   앱 (계좌의 책)      portfolio
                          │  읽는다
                    ┌─────▼────────────────────┐
   공유 지식        │ research  (수집·분석)     │
                    │ market    (시세·재무·지표) │
                    │ universe  (종목 마스터)    │
                    └──────────────────────────┘
```

**화살표는 한 방향뿐이다.** 앱은 공유 자원을 읽어도 되고, 공유 자원은 앱을 읽으면 안 된다.
`tests/test_layering.py`가 AST로 층 순서 전체(core ← universe·market·research ← portfolio ← api ← dashboard_web)를 검사한다.

- **유료 LLM API 절대 금지** — 판단은 Claude CLI 구독(`claude -p --safe-mode`), 요약·분류는 온호스트 open-weight(Qwen)
- **세 권의 책(DB)** — `portfolio.db`(앱 소유) / `market.db`·`news.db`(공유). 계좌 장부 스키마는 `src/portfolio/store/schema.py`
- **화면은 인라인 페이지 셋** — `news`·`account`·`sources`. 서버가 `<head>`에 theme 토큰·`shared.css`·`shared.js`를 주입하고, 페이지가 `<!--#include …-->`로 부르는 조각(`news/`·`account/`·`common/`)을 인라인으로 넣고, 응답마다 CSP nonce를 찍는다. 정적 파일 마운트는 없다
- **PIN 없는 계좌는 닫혀 있다** — 계좌는 생성 시 PIN 필수


---

## 저장소 구조

```
portfolio-dashboard/
├── README.md
├── autotrade/        새 quant 엔진·API (quant.jsyoo.dev)
├── web/              새 quant 화면
├── deploy/           deploy.sh · dashboard.service · nginx-portfolio.conf · make_icons.py
│                     portfolio.cron(정기 작업의 진실) · install_cron.py
│                     autotrade.service · autotrade.cron · nginx-quant.conf (새 quant)
├── docs/             기획·설계 문서 (Obsidian Vault 심링크, gitignore)
├── tests/            pytest — src를 따라 층별 폴더 (tests/autotrade는 새 quant)
├── src/
│   ├── core/         paths · kst · notify · secrets · ratelimit · backup · llm · usage · jobs  [공유]
│   ├── universe/     종목 마스터 (builder · search · refresh_job)    [공유]
│   ├── market/       시세·재무·지표 (source / store / pipeline)       [공유]
│   ├── research/     news(수집) · chart · fundamental · analyze(LLM)  [공유]
│   ├── portfolio/    사람 계좌 앱 (store · security · service · chat · supply_job)  [앱]
│   ├── api/          라우팅만
│   └── dashboard_web/  server.py + static/{news,account,sources}.html
│                       news/ · account/ · common/ (페이지 조각) · shared.css · shared.js
│                       fonts/ · icons/ · theme.json
└── data/             운영 데이터 (gitignore) — *.db · universe/ · market/ · analysis/ · backup/ · logs/
```

`[공유]`가 시스템의 대부분이라 `shared/`로 다시 감싸지 않는다. 새 코드를 어디에 두는지는 ops notes §5 참조.

## 화면

| 페이지 | 경로 | 내용 |
|---|---|---|
| news | `/` | 수집 — 분야별 분위기 · 뉴스 흐름 (`?theme=` 분야 패널) |
| account | `/a/{account_id}` | 계좌 — 보유(판정 배지 · 좌표 한 줄) · 종목 화면(`?s=`, 종합 분석) · 거래 이력(`?v=trades`, 종목·기간 필터) · 채팅 |
| sources | `/sources` | 소스·작업 현황 |

## 실행

### 로컬

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.lock.txt
source ~/secrets/.env            # KRX_ID · DART_API_KEY · KIS_* 등

python -m src.dashboard_web.server --port 8888
```

`.env`의 키는 로컬에서도 라이브다(KIS·DART·Discord, 새 quant의 토스 키까지). 테스트는 `conftest`가 `requests` 호출·Discord·claude CLI를 막는다.

### 테스트

```bash
pytest tests/              # 1,227건 — tests/autotrade(756건, 새 quant)까지 함께 돈다
pytest tests/autotrade     # 새 quant만 (자기 pytest.ini·conftest)
```

옛 테스트 파일과 `tests/autotrade`의 파일을 한 명령에 섞어 넘기지 않는다. 그러면 `tests/autotrade/pytest.ini`가 루트가 되어 `tests/conftest.py`(라이브 호출 차단)가 실리지 않는다.

구조 불변식: `test_layering.py`(공유 → 앱 import 금지) · `web/test_server_gate.py`(`/api` 게이트, PIN 없는 계좌는 닫힘) · `test_ui_invariants.py`(화면 파일 전부 — 색·글자 크기 리터럴 0건, mobile-first 브레이크포인트, `.btn` 4종, 인라인 `style`/`on*` 0건, 금지 낱말, quant로 가는 링크 0건)와 `test_ui_{news,account,stock,sources}.py` · `test_server_csp.py`(nonce). 실패하면 코드가 아니라 설계가 틀어진 것이다.

### 배포

로컬(MacBook)이 단일 진실, Oracle은 `git pull`만 받는 mirror다.

```bash
./deploy/deploy.sh                    # push → Oracle pull → DB 백업 (restart 없음)
./deploy/deploy.sh dashboard          # + dashboard.service restart + 헬스체크 (/api/accounts 와 / 둘 다 200)
./deploy/deploy.sh autotrade          # 새 quant (ops notes '배포')
./deploy/deploy.sh rollback           # 마지막 배포 바로 전 커밋으로
```

새 의존성은 Oracle venv에 수동 설치한다. 운영 cron 일정은 ops notes §8.

## API 요약

```
/api/universe/*      종목 검색
/api/accounts/*      사람 계좌 — 보유·거래(trades)·현금·스냅샷·채팅   (계좌 쿠키)
/api/market/*        OHLCV·시세
/api/analysis/*      종합 분석·차트 지표·재무·뉴스 (리터럴 경로만)
```

전체 표와 인가 규칙은 ops notes §10.

## 문서

- **`docs/`** — 개인 Obsidian Vault `001_projects/portfolio-dashboard/`의 설계 문서 심링크. 그 폴더에는 지금 맞는 문서(정의서·디자인 기록)와 할 일(`TODO.md`), 지난 경과를 모은 `시행착오.md`만 둔다(2026-10-15 정리). 자동매매 문서는 옆 폴더 `quant-dashboard/`. gitignore라 Oracle에는 없다
