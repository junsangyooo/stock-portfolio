# stock-portfolio

주식 분석 프로그램 **portfolio**(`portfolio.jsyoo.dev`)의 저장소다. 여러 증권계좌(사람 계좌 4개 — ISA·일반·연금·해외)를 팔로업하고 분석한다: 보유 조회·손익·거래 기록·종목 분석·채팅.

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
stock-portfolio/
├── README.md
├── deploy/           deploy.sh · dashboard.service · nginx-portfolio.conf · make_icons.py
│                     portfolio.cron(정기 작업의 진실) · install_cron.py · logrotate-stock-portfolio
├── docs/             기획·설계 문서 (Obsidian Vault 심링크, gitignore)
├── tests/            pytest — src를 따라 층별 폴더
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

`[공유]`가 시스템의 대부분이라 `shared/`로 다시 감싸지 않는다. 새 코드는 계좌를 알아야 하면 `portfolio/`, 아니면 공유 층에 두고, 층은 아래로만 의존한다.

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

`.env`의 키는 로컬에서도 라이브다(KIS·DART·Discord). 테스트는 `conftest`가 `requests` 호출·Discord·claude CLI를 막는다.

### 테스트

```bash
pytest tests/
```

구조 불변식: `test_layering.py`(공유 → 앱 import 금지) · `web/test_server_gate.py`(`/api` 게이트, PIN 없는 계좌는 닫힘) · `test_ui_invariants.py`(화면 파일 전부 — 색·글자 크기 리터럴 0건, mobile-first 브레이크포인트, `.btn` 4종, 인라인 `style`/`on*` 0건, 금지 낱말, quant로 가는 링크 0건)와 `test_ui_{news,account,stock,sources}.py` · `test_server_csp.py`(nonce). 실패하면 코드가 아니라 설계가 틀어진 것이다.

### 배포

로컬(MacBook)이 단일 진실, Oracle은 `git pull`만 받는 mirror다.

```bash
./deploy/deploy.sh                    # push → Oracle pull → DB 백업 (restart 없음)
./deploy/deploy.sh dashboard          # + dashboard.service restart + 헬스체크 (/api/accounts 와 / 둘 다 200)
./deploy/deploy.sh rollback           # 마지막 배포 바로 전 커밋으로 (확인 후 dashboard.service 재시작)
```

서버 폴더는 `/home/ubuntu/stock-portfolio`다. 새 의존성은 Oracle venv에 수동 설치한다. 정기 작업 목록의 진실은 `deploy/portfolio.cron`이고, 서버 crontab에는 표시 사이 블록으로 들어간다:

```bash
crontab -l | python3 deploy/install_cron.py deploy/portfolio.cron | crontab -
```

## API 요약

```
/api/universe/*      종목 검색
/api/accounts/*      사람 계좌 — 보유·거래(trades)·현금·스냅샷·채팅   (계좌 쿠키)
/api/market/*        OHLCV·시세
/api/analysis/*      종합 분석·차트 지표·재무·뉴스 (리터럴 경로만)
```

계좌 데이터를 읽는 라우트는 그 계좌의 쿠키가 있어야 하고, PIN 없는 계좌는 닫혀 있다. 인가 실패는 항상 403이다.

## 문서

- **`docs/`** — 설계 문서(정의서·디자인 기록)를 가리키는 로컬 링크. gitignore라 저장소와 서버에는 없다
