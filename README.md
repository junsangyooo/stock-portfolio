# portfolio-dashboard

주식 분석 프로그램. 한 가족(family) 안에 앱이 둘이고, 둘은 **같은 지식 자원**을 공유한다.

| | **portfolio** | **quant** |
|---|---|---|
| 무엇 | 여러 증권계좌를 팔로업하고 분석하는 웹 | 토스 계좌에서 자동매매하는 계좌 |
| 계좌 | 사람 계좌 4개 (ISA·일반·연금·해외) | 자동매매 계좌 2개 — 실계좌 `robo`(토스) · 모의 `robo_paper`(브로커 없음 — 진입·익절은 계획 가격, 손절은 관측 시세로 체결) |
| 주소 | `portfolio.jsyoo.dev` | `quant.jsyoo.dev` |
| 하는 일 | 보유 조회·손익·거래 기록·종목 분석·채팅 | 사이클마다 판단·주문·감시, 계좌별 정지/재개 |

```
   앱 (계좌의 책)      portfolio        quant
                          │  읽는다       │  읽는다
                    ┌─────▼──────────────▼─────┐
   공유 지식        │ research  (수집·분석)     │
                    │ market    (시세·재무·지표) │
                    │ universe  (종목 마스터)    │
                    └──────────────────────────┘
```

**화살표는 한 방향뿐이다.** 앱은 공유 자원을 읽어도 되고, 공유 자원은 앱을 읽으면 안 된다.
`tests/test_layering.py`가 AST로 이 경계를 검사한다.

- **유료 LLM API 절대 금지** — 판단은 Claude CLI 구독(`claude -p --safe-mode`), 요약·분류는 온호스트 open-weight(Qwen)
- **네 권의 책(DB)** — `portfolio.db`·`quant.db`(앱 소유) / `market.db`·`news.db`(공유). 계좌 장부는 `src/ledger` 한 스키마를 두 책이 함께 쓴다
- **화면은 인라인 페이지 셋** — `landing`·`account`·`quant`. 서버가 `<head>`에 theme 토큰·`shared.css`·`shared.js`를 주입하고 응답마다 CSP nonce를 찍는다. 정적 파일 마운트는 없다
- **PIN 없는 책은 닫혀 있다** — 사람 계좌는 생성 시 PIN 필수, quant의 첫 PIN은 서버에서 `python -m src.quant.pin`


---

## 저장소 구조

```
portfolio-dashboard/
├── README.md
├── deploy/           deploy.sh · dashboard.service · nginx-portfolio.conf · make_icons.py
├── docs/             기획·설계 문서 (Obsidian Vault 심링크, gitignore)
├── tests/            pytest
├── src/
│   ├── core/         paths · kst · notify · security · secrets · ratelimit · backup  [공유]
│   ├── ledger/       계좌 장부 스키마 — 두 책이 함께 쓴다              [공유]
│   ├── universe/     종목 마스터 (builder · search · refresh_job)    [공유]
│   ├── market/       시세·재무·지표 (source / store / pipeline)       [공유]
│   ├── research/     news(수집) · chart · fundamental · analyze(LLM)  [공유]
│   ├── portfolio/    사람 계좌 앱 (store · service · jobs)            [앱]
│   ├── quant/        자동매매 앱 (broker/toss · keeper · cycle_job · pin)  [앱]
│   ├── api/          얇은 read/dispatch 층
│   └── dashboard_web/  server.py + static/{landing,account,quant}.html
│                       shared.css · shared.js · icons/ · theme.json
└── data/             운영 데이터 (gitignore) — *.db · universe/ · analysis/ · backup/ · logs/
```

`[공유]`가 시스템의 대부분이라 `shared/`로 다시 감싸지 않는다. 새 코드를 어디에 두는지는 ops notes §5 참조.

## 화면

| 페이지 | 경로 | 내용 |
|---|---|---|
| landing | `/` | 계좌 목록 · 뉴스 피드 (분야별 요약) |
| account | `/a/{account_id}` | 보유 카드(판정 배지 · 좌표 한 줄) · 종합 분석 탭 · 거래 기록(매수/매도, 종목·기간 필터) · 채팅 |
| quant | `/q` (또는 `quant.jsyoo.dev/`) | 모드·자산·자산곡선 · 보유 카드 · 계획/관심/뉴스 · 사이클 타임라인 · 계좌 전환(실계좌/모의) · 시장별(KR·US) 가동/끔 |

`portfolio.jsyoo.dev/q`와 `quant.jsyoo.dev/a/…`는 404다. 두 페이지는 서로로 가는 링크를 갖지 않는다.

## 실행

### 로컬

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.lock.txt
source ~/secrets/.env            # KRX_ID · DART_API_KEY · TOSS_* 등

python -m src.dashboard_web.server --port 8888
```

`.env`에 토스 자격증명이 있으면 브로커 코드는 로컬에서도 라이브다. 테스트는 `conftest`가 브로커를 막지만, 테스트 밖에서 `src.quant`의 쓰기 경로를 직접 실행하지 않는다.

### 테스트

```bash
pytest tests/          # 691건
```

구조 불변식: `test_layering.py`(공유 → 앱 import 금지) · `test_quant_api.py`(전 라우트 `require_quant`/`require_account`) · `test_ui_invariants.py`와 `test_ui_{landing,account,quant}.py`(색·글자 크기 리터럴 0건, mobile-first 브레이크포인트, `.btn` 4종, 인라인 `style`/`on*` 0건, 크로스링크 0건) · `test_server_csp.py`(nonce). 실패하면 코드가 아니라 설계가 틀어진 것이다.

### 배포

로컬(MacBook)이 단일 진실, Oracle은 `git pull`만 받는 mirror다.

```bash
./deploy/deploy.sh                    # push → Oracle pull → DB 백업 (restart 없음)
./deploy/deploy.sh dashboard.service  # + restart + 헬스체크 (/api/accounts 와 / 둘 다 200)
```

서버에서 1회, 멱등:

```bash
ssh oracle-instance "cd ~/portfolio-dashboard && .venv/bin/python -m src.quant.db add-paper-account"   # 모의 계좌 행
ssh oracle-instance "cd ~/portfolio-dashboard && .venv/bin/python -m src.quant.pin"                     # quant PIN (없을 때만)
```

새 의존성은 Oracle venv에 수동 설치한다. 운영 cron 일정은 ops notes §8.

## API 요약

```
/api/universe/*      종목 검색
/api/accounts/*      사람 계좌 — 보유·거래(trades)·현금·스냅샷·채팅   (계좌 쿠키)
/api/market/*        OHLCV·시세
/api/analysis/*      종합 분석·차트 지표·재무·뉴스 (리터럴 경로만)
/api/quant/*         자동매매 — state·plans·positions·cycles/{id}·watch·journal·orders,
                     pin·markets/{KR|US}·mode·sync·cancel_all   (자기 쿠키 + 돈이 걸리면 PIN 재확인, ?account=)
```

전체 표와 인가 규칙은 ops notes §10.

## 문서

- **`docs/`** — 개인 Obsidian Vault `001_projects/portfolio-dashboard/`의 설계 문서 심링크. 프로젝트 맥락은 같은 폴더의 `LOG.md`, 할 일은 `TODO.md`. gitignore라 Oracle에는 없다
