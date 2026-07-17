# portfolio-dashboard

멀티 계좌(ISA·일반·연금·해외) 통합 트래킹 + 종목 분석(차트·재무·뉴스) 대시보드.

- 단일 진실 분리: **portfolio**(계좌·보유) vs **market**(시장 데이터) vs **universe**(종목 마스터) vs **analysis**(분석)
- 동일 종목이 여러 계좌에 있어도 시장 데이터·분석은 종목 단위로 **딱 1벌**
- 분석 파이프라인 LIVE: 에이전트 3종(chart / fundamental / news) + judge 통합 + 야간 verdict 워밍 배치
- **LLM 백엔드 = Claude CLI 구독만** (API 절대 금지 — 정액제)


---

## 라이브 배포

- **Dashboard**: https://quant-lab.ai-sandbox.io (Cloudflare → nginx → `127.0.0.1:8888`)
- **1차 인증**: nginx HTTP Basic Auth · **2차 인증**: 계좌 상세 = 6자리 PIN + HMAC 세션 쿠키
- **서버**: Oracle Cloud (Ampere A1, Ubuntu 22.04 aarch64, 춘천 region), systemd `dashboard.service`
- 도메인 이름이 `quant-lab`로 남은 것은 의도적 (Cloudflare DNS·SSL·htpasswd 변경 회피)

## 4-영역 분리

| 영역 | 책임 | 저장 |
|---|---|---|
| **portfolio** | 계좌·보유·잔고·스냅샷 | `data/portfolio.db` (SQLite) |
| **market** | OHLCV·시세·재무·섹터·ETF (계좌 무관) | `data/market/**` (parquet/json) |
| **universe** | 종목 마스터 (KRX+US ~1.3만) | `data/universe/{kr,us}.json` (일 1회 batch) |
| **analysis** | chart·fundamental·news·judge verdict | `data/analysis/**` (rulebook JSON) |

## 아키텍처

```
src/
├── core/                 공용 유틸 (config, paths, notify, security=PIN·세션토큰)
├── universe/             종목 마스터 (builder / search / refresh_job)
├── market/               시장 데이터 (source / store / pipeline)
├── portfolio/            계좌·보유·잔고 (store / service / snapshot_job)
├── api/                  FastAPI 라우터 (universe / portfolio / market / analysis)
├── agents/               분석 에이전트 (chart / fundamental / news / judge, 룰북 = *_rules.md)
│   ├── verdict_job.py        야간 verdict 워밍 배치
│   └── llm_backend.py        Claude CLI subprocess (FAST=Haiku 4.5, SLOW=Sonnet 4.6)
└── dashboard_web/        server.py(라우터 mount) + static(landing / account)

data/                     운영 데이터 (Oracle 단방향, gitignore)
├── portfolio.db          accounts / holdings / balances / snapshots / holding_themes
├── universe/, market/, analysis/, signals/, backup/, logs/
```

## 분석 파이프라인 & AI 개입 지점

에이전트별 산출물은 `(symbol_id, date) → JSON`으로 통일. **AI(Claude CLI)** 개입은 다음과 같다.

| 에이전트 | AI | 모델 | 트리거 |
|---|---|---|---|
| **chart** | facts=순수함수 / verdict=LLM | Sonnet | 종목 분석 버튼 · 야간 배치 |
| **fundamental** | DART 파생 재무 verdict | Sonnet | 야간 배치 |
| **news** | 아래 뉴스 파이프라인 참조 | Haiku/Sonnet | 매일 batch + 종목 evaluate |
| **judge** | 크로스도메인 종합 verdict | Sonnet | 야간 배치 · 버튼 |

## 뉴스(정성) 파이프라인 — holdings-driven

매일 06:00 KST `news.daily_job`이 **테마 단위**로 `collect → digest → state`를 돈다.

```
collect  (규칙기반, NO LLM)  RSS·arXiv 크롤 → 키워드 필터 → url dedup → jsonl
  ↓
digest   (LLM = Haiku)        원시 시그널 중복병합 + 상위 5~8건 선별·요약·중요도
  ↓
state    (LLM = Sonnet, 게이트) 장기추세 요약·watchlist·sentiment 갱신 (slow memory)
```

**수집 대상 테마 = 보유 종목에서 도출** (2026-07-17~):

- 테마 활성 여부는 `themes.json`의 static 플래그가 아니라 **`holding_themes`(어느 계좌든 primary로 매핑된 테마) ∪ macro(always_on)** 에서 실시간 도출.
- **종목 추가** 시 미매핑이면 background로 자동 태깅(ETF=이름규칙 / 개별=LLM 1콜) → 저장 → 다음 배치부터 그 테마 수집 시작.
- **종목 삭제** 시 어느 계좌에도 없으면 `holding_themes` 정리 → 마지막 보유자가 사라진 테마는 수집 자동 중단.
- 즉 **뉴스 커버리지가 포트폴리오를 자동으로 따라간다.**

**비용 최적화**:

- **digest = Haiku 4.5** (중복병합·선별은 기계적 태스크 → 상위 모델 불필요)
- digest 투입은 테마당 **상위 40건 상한** (lead_lag 우선순위 + 최신순 규칙 pre-rank, 어차피 5~8건만 남음)
- **state = 이벤트 게이트** — 당일 digest에 중요도≥4 또는 novelty 신호가 없으면 결정적 decay만 수행하고 LLM은 skip (조용한 테마는 자연히 주 2~3회, 사건 있는 날은 즉시 반영)

> **LLM 백엔드 불변식**: 모든 호출은 `ClaudeCliBackend`(`claude -p`) 구독. `ANTHROPIC_API_KEY`는 subprocess env에서 빈값 강제. API 절대 금지.

## systemd / cron (Oracle, UTC)

| 시각 | 잡 | 역할 |
|---|---|---|
| 매시 정각 | backup | `portfolio.db` 24h 회전 백업 |
| 21:00 | `news.daily_job` | 뉴스 수집·digest·state (holdings-driven) |
| 21:10 | `fundamental_job` | 재무 backfill (보유 종목) |
| 21:15 | `etf_job` | ETF 일별 스냅샷 |
| 21:20 | `snapshot_job` | 계좌 자산 스냅샷 (OHLCV gap-fill 후) |
| 21:30 | `verdict_job` | verdict 캐시 워밍 (chart→judge, 2워커) |
| 22:30 | `universe.refresh_job` | 종목 마스터 refresh (~1.3만) |

## 배포

```bash
# 로컬 단일 진실 → git commit → deploy
./deploy/deploy.sh            # 코드 sync (push + Oracle pull, restart 없음)
./deploy/deploy.sh dashboard  # + dashboard.service restart + 헬스체크
```

- **로컬(MacBook) = 단일 진실.** Oracle은 `git pull`만 받는 배포 mirror (직접 편집 금지).
- 새 의존성은 Oracle venv에 수동 설치 후 restart.

## 주요 API

```
GET  /                                     landing (홈)
GET  /a/{account_id}                        계좌 페이지 (PIN 게이트)

GET  /api/universe/search|symbol|refresh
CRUD /api/accounts/**                       계좌·보유·잔고·스냅샷
GET  /api/market/ohlcv|quote|quotes
GET  /api/analysis/{kind}/{market}/{code}   verdict·facts·feed·summary
POST /api/analysis/{kind}/{market}/{code}/run   비동기 agent 실행
```

