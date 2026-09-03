# portfolio-dashboard

주식 분석 프로그램. 한 가족(family) 안에 앱이 둘이고, 둘은 **같은 지식 자원**을 공유한다.

| | **portfolio** | **quant** |
|---|---|---|
| 무엇 | 여러 증권계좌를 팔로업하고 분석하는 웹 | 토스 계좌에서 자동매매하는 계좌 |
| 계좌 | 사람 계좌 4개 (ISA·일반·연금·해외) | 자동매매 계좌 1개 |
| 주소 | `portfolio.jsyoo.dev` | `quant.jsyoo.dev` |
| 하는 일 | 보유 조회·손익·종목 분석·채팅 | 사이클마다 판단·주문·감시 |

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


---

## 저장소 구조

```
portfolio-dashboard/
├── README.md
├── deploy/           deploy.sh · dashboard.service · nginx-portfolio.conf
├── docs/             기획·설계 문서 (Obsidian Vault 심링크, gitignore)
├── tests/            pytest
├── src/
│   ├── core/         paths · kst · notify · security · backup       [공유]
│   ├── ledger/       계좌 장부 스키마 — 두 책이 함께 쓴다              [공유]
│   ├── universe/     종목 마스터 (builder · search · refresh_job)    [공유]
│   ├── market/       시세·재무·지표 (source / store / pipeline)       [공유]
│   ├── research/     news(수집) · chart · fundamental · analyze(LLM)  [공유]
│   ├── portfolio/    사람 계좌 앱 (store · service · jobs)            [앱]
│   ├── quant/        자동매매 앱 (broker/toss · keeper · cycle_job)   [앱]
│   ├── api/          얇은 read/dispatch 층
│   └── dashboard_web/  server.py + static/{landing,account,quant}.html
└── data/             운영 데이터 (gitignore) — *.db · universe/ · analysis/ · backup/ · logs/
```

`[공유]`가 시스템의 대부분이라 `shared/`로 다시 감싸지 않는다.

## 실행

### 로컬

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.lock.txt
source ~/secrets/.env            # KRX_ID · DART_API_KEY · TOSS_* 등

python -m src.dashboard_web.server --port 8888
```

### 테스트

```bash
pytest tests/
```

`test_layering.py`(공유 → 앱 import 금지)와 `test_quant_api.py`(전 라우트 `require_quant`)는 구조 불변식이다. 실패하면 코드가 아니라 설계가 틀어진 것이다.

### 배포

로컬(MacBook)이 단일 진실, Oracle은 `git pull`만 받는 mirror다.

```bash
./deploy/deploy.sh                    # push → Oracle pull → DB 백업 (restart 없음)
./deploy/deploy.sh dashboard.service  # + restart + 헬스체크
```

새 의존성은 Oracle venv에 수동 설치한다. 운영 cron 일정은 deploy/portfolio.cron.

## 문서

- **`docs/`** — Obsidian Vault 원본의 심링크(`_STATUS.md`, `planning-*.md`). gitignore라 Oracle에는 없다
