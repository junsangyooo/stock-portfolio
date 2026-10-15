# 펀더멘털 해석 사전 (Fundamental Reference)

> **용도**: 종합 분석(claude -p 한 콜, `src/research/analyze`)이 정적 펀더멘털 지표를 해석할 때 참조하는 재무 도메인의 사전. 재료는 `src/research/fundamental`이 조립한다.
> 정량(chart)·정성(news)과 분리된 **고정 소스(DART/yfinance) 지표 전용** 룰. 검증된 투자 프레임워크를 우리 실제 수집 필드에 1:1 매핑.
>
> **사용법**:
> 1. market.db의 `financials`·`valuation`과 파생 지표, 그리고 동종 백분위(`peer.percentiles`)를 입력으로 받는다.
> 2. 아래 룰을 적용해 **5축**(밸류·수익성·성장성·안정성·주주환원)으로 평가한다.
> 3. 단일 지표는 절대 단독 판단 금지 — **반드시 2개 이상 결합**(§B/§C)하고 **§E 섹터 보정** + **§F 레드플래그**를 교차 적용한다.
> 4. 이 문서는 종합 분석 1콜에 **재무 도메인의 사전**으로 주입된다. 도메인 단독 산출물은 없다.

---

## §0. 우리가 보유한 필드 (입력 레퍼런스)

종합 분석이 쓸 수 있는 실제 데이터. 룰은 이 필드명으로만 기술한다.

### L1 밸류에이션 (`valuation`, 일별)
`per` · `pbr` · `psr` · `eps` · `bps` · `dps` · `div_yield`(%) · `market_cap`

### L2 재무제표 (`financials`, 분기/연간 시계열)
`revenue` · `gross_profit` · `operating_income` · `net_income` · `eps` · `total_assets` · `total_liabilities` · `total_equity` · `current_assets` · `current_liabilities` · `operating_cf` · `investing_cf` · `financing_cf`

### L3 파생 (`derived`, on-the-fly)
`operating_margin` · `net_margin` · `roe` · `roa` · `debt_ratio`(부채/자본×100) · `current_ratio`(×100) · `revenue_yoy` · `operating_income_yoy`

> ⚠️ **미보유(룰 적용 시 주의)**: 발행주식수 변동(Δshares), 이익잉여금(retained earnings) 단독값, 정확한 ROIC용 투하자본·NOPAT, 컨센서스/추정 EPS. 이들이 필요한 룰은 §G 「계산 가능성」에서 대체식·생략을 명시.

---

## §A. 단일 지표 해석 룰 (Simple — 1차 신호, 단독 판단 금지)

| 지표 | 낮음(low) | 높음(high) | 핵심 임계 | 함정/주의 |
|---|---|---|---|---|
| `per` | 저평가 가능 or 성장정체 | 성장 기대 선반영 | 시장평균 대비 비교. 일반 10~20 보통 | **음수 PER**(적자)=무의미. 저PER+ROE하락=가치함정 |
| `pbr` | 자산 대비 저평가 가능 | 무형·브랜드·성장 프리미엄 | <1 = 청산가치 이하 | **PBR<1이 항상 저평가 아님** — 시장이 순자산 감소를 예상한 것일 수도 |
| `psr` | 매출 대비 저평가 | 고성장 기대 | <1.5 = 저평가 후보(Fisher) | 적자기업·매출 변동 큰 업종 판단용. 이익 무시하므로 보조 지표 |
| `roe` | 자본 효율 낮음 | 자본 효율 높음 | **>10~15% 양호**, <7% 약함 | 고부채로 부풀린 ROE 주의 → §B DuPont 분해 필수 |
| `roa` | 자산 효율 낮음 | 자산 효율 높음 | >5% 양호 (업종편차 큼) | 자산경량 업종(SW)은 높고 중장비·금융은 낮음 |
| `operating_margin` | 가격경쟁력 약 | 해자/가격결정력 | 추세가 핵심 | 마진 잠식 추세 = 경쟁심화 경고 |
| `net_margin` | — | — | operating_margin과 괴리 시 비영업손익 점검 | 일회성 이익으로 부풀 수 있음 |
| `debt_ratio`(부채/자본) | 보수적 재무 | 레버리지 위험 | **<100% 안정**, >200% 경계 | 업종편차 큼(§E). 금융·유틸·항공은 높음이 정상 |
| `current_ratio`(×100) | 단기지급 위험 | 안전 | **>200% 우수, <100% 위험** | 과도하게 높으면 자본 비효율일 수도 |
| `revenue_yoy` | 역성장 | 고성장 | >0 필수, 둔화추세 경고 | 인수·일회성으로 부풀 수 있음 |
| `operating_income_yoy` | 수익성 악화 | 개선 | revenue_yoy와 함께 봐야 | 매출↑·이익↓ = 마진압박(부정 신호) |
| `eps` | — | — | 추세·전환여부 | 자사주매입으로 부풀 수 있음(분모 감소) |
| `div_yield` | 저배당/성장재투자 | 인컴 매력 | 3~5% 매력대 | **너무 높으면(>8~10%) 배당함정** — 주가급락/감배 위험 |
| `operating_cf` | — | — | **>0 필수**, net_income과 비교 | 음수 영업현금흐름 지속 = 핵심 위험(§F) |

---

## §B. 2-지표 복합 룰 (Composite — 실전 1차 판단 단위)

### B1. PBR × ROE — 밸류·수익성 매트릭스 (가장 중요)
PBR과 ROE는 **이론적으로 연결**(PBR ≈ ROE 함수)되어 단독보다 결합이 결정적.

| | ROE 높음(>10%) | ROE 낮음(<7%) |
|---|---|---|
| **PBR 낮음(<1)** | ✅ **강력 저평가 후보** (좋은 회사가 싸다) | ⚠️ **가치함정 의심** (싼 데는 이유가 있다) |
| **PBR 높음(>2)** | 프리미엄 정당화 가능(질 높음) | ❌ 고평가 위험 (비싸고 효율 낮음) |

> 규칙: `pbr < 1 AND roe > 10%` → 밸류축 강한 가점. `pbr < 1 AND roe < 7%` → 가치함정 플래그(점수 깎되 §D 한국 밸류업 트리거 체크).

### B2. PEG — PER ÷ 성장률 (Lynch/GARP)
`PEG = per / (eps_growth_pct)`. eps 성장률은 `revenue_yoy`·`operating_income_yoy`·연 eps 성장으로 추정.

- **PEG < 1.0** → 성장 대비 저평가 (Lynch 핵심: "공정가 PER = 성장률")
- **PEG ≈ 1.0** → 적정
- **PEG > 2.0** → 성장 공격적 선반영, 안전마진 얇음
- GARP 스윗스팟: EPS 성장 15~30% + PEG<1 + 건전 재무.

### B3. Graham Number / 22.5 룰 — PER × PBR
- **`per × pbr ≤ 22.5`** → Graham 저평가 (PER≤15 × PBR≤1.5 곱이 22.5 이하).
- 초과할수록 밸류 매력 감소.

### B4. DuPont — ROE 분해 (수익성의 "질")
`ROE ≈ net_margin × asset_turnover × leverage`
- asset_turnover = `revenue / total_assets`, leverage = `total_assets / total_equity`
- **고ROE의 출처 판별**: 마진/효율(좋음) vs 레버리지(위험). 같은 ROE라도 고부채 기반이면 안정성축에서 감점.

### B5. 이익의 질 — OCF vs 순이익 (Quality of Earnings)
`QoE = operating_cf / net_income`
- **QoE ≥ 1.0** → 고품질(이익이 현금으로 뒷받침)
- **QoE < 0.8** → 경고(회계상 이익 의심, 비현금 항목 점검)
- **순이익↑인데 OCF↓/음수** → 강한 레드플래그(§F, 분식 가능성)

### B6. Sloan 발생액 비율 (Accrual)
`accrual = (net_income − operating_cf − investing_cf) / total_assets`
- **−10% ~ +10%** = 안전구간
- **±25% 밖** = 이익이 비현금 발생액 위주 = 위험 신호

### B7. 배당 지속성 — 배당성향 × 수익성
배당성향 = `dps / eps` (또는 총배당/net_income)
- **30~60%** = 지속가능 스윗스팟
- **>100%** = 이익 초과 배당(적립금/차입 의존) = 감배 위험
- 고배당수익률(§A)이라도 성향 과다·OCF 약하면 **배당함정**.

---

## §C. 다중 복합 프레임워크 (Complex — 검증된 정통 모델)

각 프레임워크를 우리 필드로 매핑. 「계산 가능성」은 §G 참조.

### C1. Graham 방어적 투자자 7기준 (Value)
1. 규모: `revenue` 충분히 큼 (대형주 선호)
2. 재무건전성: `current_ratio ≥ 200%` AND 장기부채 < 운전자본(`total_liabilities`(비유동분) < `current_assets − current_liabilities`)
3. 이익 안정성: 최근 7년 `net_income > 0` 연속
4. 배당: 장기 배당 이력(`dps`/`div_yield`)
5. 이익성장: 장기 EPS 최소 +1/3 (말기 3년 평균 ≥ 초기 3년 평균 ×1.33)
6. PER: `per ≤ 15` (3년 평균 이익 기준)
7. PBR: `pbr ≤ 1.5`, 그리고 **`per × pbr ≤ 22.5`**(B3)

### C2. Piotroski F-Score (Quality, 0~9점) — 저PBR주 옥석 가리기
저PBR 종목군에 적용해 재무개선 기업을 선별. 각 항목 1점.

**수익성(4)**: ① `roa > 0` ② `operating_cf > 0` ③ ΔROA>0(전년대비) ④ `operating_cf/total_assets > roa`(발생액)
**레버리지·유동성(3)**: ⑤ Δ`debt_ratio` < 0(레버리지 감소) ⑥ Δ`current_ratio` > 0 ⑦ 신주발행 없음(Δshares ≤ 0)
**효율성(2)**: ⑧ Δ매출총이익률(`gross_profit/revenue`) > 0 ⑨ Δ자산회전율(`revenue/total_assets`) > 0

- **8~9 = 강함, 0~2 = 약함**. 저PBR + 고F-Score = 핵심 매수 후보.
- ⚠️ 항목⑦(Δshares)은 우리 데이터로 불완전 → 8개 항목 부분점수로 운용(§G).

### C3. Greenblatt Magic Formula (Value+Quality 랭킹)
두 지표 랭킹 합산:
- **이익수익률(Earnings Yield)** ≈ `operating_income / EV`, EV ≈ `market_cap + total_liabilities − (현금)` (현금 미보유 시 근사)
- **자본수익률(ROIC)** ≈ `operating_income / (total_equity + total_liabilities − current_liabilities)`
- 둘 다 높은(랭크 합 낮은) 종목 우선. 우리 데이터로는 **근사 ROIC/EY**로 운용.

### C4. Altman Z-Score (Bankruptcy/안정성)
`Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5`
- X1 = `(current_assets − current_liabilities) / total_assets`
- X2 = 이익잉여금/`total_assets` → **미보유, `total_equity/total_assets`로 근사(주의)**
- X3 = `operating_income / total_assets` (EBIT 근사)
- X4 = `market_cap / total_liabilities`
- X5 = `revenue / total_assets`
- **Z<1.8 위험 / 1.8~3.0 회색 / >3.0 안전**. (제조·상장 기준; 비제조·신흥은 Z'·Z'' 변형 있으나 근사 사용)

### C5. Lynch GARP (§B2 확장)
저PEG + 적정성장(15~30%) + 저부채(`debt_ratio` 낮음) + 흑자지속. "비싸 보여도 성장 대비 싸다"는 종목 포착.

### C6. Fisher PSR (Value, 매출기반)
이익이 불안정/적자전환 업종에서 `psr` 활용. `psr < 1.5` 저평가 후보 + 높은 마진 + 저부채 + 매출성장 동반 요구. (성숙·저성장주엔 약함)

---

## §D. 한국 시장 특수 컨텍스트 (KR 종목 필수 보정)

- **코리아 디스카운트**: KOSPI 평균 PBR이 장기간 ~0.99로 선진국 대비 낮음. **KR은 PBR<1이 "흔한 정상"** → US 기준 그대로 적용 금지. KR은 동종업계·자기과거 상대비교를 우선.
- **밸류업 프로그램(2024~)**: 저PBR 기업의 자발적 기업가치 제고(배당확대·자사주소각·ROE개선) 공시 유도. **`pbr < 1` 종목은 밸류업 트리거 후보** — 가치함정(B1)이라도 주주환원 개선 모멘텀 가능성을 별도 가점/관찰 플래그로.
- KR 상장사 **60%+가 ROE < 7%(장기평균)** → KR ROE 임계는 시장 맥락에서 완화 해석하되, **고ROE+저PBR KR주는 더 희소·가치 높음**.
- 정책 리스크: 저PBR 2년 연속 시 밸류업 계획 제출 의무화 입법 흐름 → 정책 수혜/압박 양면.

---

## §E. 섹터 상대성 — 절대 임계 금지

PER/PBR/PSR/부채비율은 **업종마다 정상 범위가 다르다.** 반드시 동종업계 상대 비교.

| 업종 특성 | PBR 경향 | 부채비율(D/E) 정상대 | 비고 |
|---|---|---|---|
| 기술/SW (자산경량) | 높음 | 0.2~0.6 (낮음) | 무형자산↑ → PBR 본질적 高, ROA 高 |
| 소비재/유통 | 중간 | 0.5~1.5 | 재고·점포 차입 |
| 제조/중장비 | 낮음~중간 | 1.0~2.0+ | 자산집약, 회전율 낮음 |
| 금융/유틸/항공/통신/부동산 | 낮음 | >2.0 정상 | 구조적 고레버리지 — 高부채를 위험으로 오판 금지 |
| 성숙·저성장 산업 | 낮음 | — | PBR 본질적 低 |

> DuPont(B4) 관점: 저마진·고회전(유통) vs 고마진·저회전(중장비)은 트레이드오프 — 같은 잣대 금지.

---

## §F. 레드플래그 체크리스트 (복합 위험 — 발견 시 안정성/품질축 강한 감점)

1. **순이익↑ + `operating_cf` ↓/음수** (QoE<0.8, B5) — 이익의 질 붕괴, 분식 가능
2. **`operating_cf` 지속 음수** — 흑자라도 현금고갈 위험
3. **Sloan 발생액(B6) ±25% 밖** — 비현금 이익 위주
4. **`debt_ratio` 급증 추세** + `current_ratio < 100%` — 단기지급 위험
5. **이자보상배율 ≈ `operating_income / 이자비용` ≤ 1** — 영업이익으로 이자도 못 냄 (이자비용 별도 추정 필요)
6. **매출↑인데 `operating_income_yoy` < 0** — 마진압박/경쟁심화
7. **배당성향 > 100%** (B7) — 무리한 배당
8. **`pbr < 1` + ROE 하락 + 역성장** — 전형적 가치함정 (KR은 §D 밸류업 가능성 병기)
9. **Altman Z < 1.8** (C4) — 부도위험 구간

---

## §G. 계산 가능성 & 대체식 (우리 데이터 한계 명시)

| 프레임워크/항목 | 상태 | 대체/비고 |
|---|---|---|
| §A 단일지표, B1 PBR×ROE, B3 22.5, B4 DuPont, B5 QoE | ✅ 완전 | 보유 필드로 직접 계산 |
| B2/C5 PEG | ✅ 근사 | 성장률은 `revenue_yoy`/`operating_income_yoy`/연 eps성장으로 추정 |
| B6 Sloan 발생액 | ✅ 가능 | `net_income`,`operating_cf`,`investing_cf`,`total_assets` 보유 |
| B7 배당성향 | ✅ 가능 | `dps/eps` (KR dps 미수집 시 div_yield×per로 근사) |
| C1 Graham | ✅ 대체로 | 다년 시계열 필요(7년 흑자·성장) — financials 보관기간 의존 |
| C2 Piotroski | ⚠️ 8/9 | 항목⑦ Δshares 불완전 → 8항목 부분점수 |
| C3 Magic Formula | ⚠️ 근사 | ROIC·EV에 현금 미반영 근사. 절대값 아닌 **상대 랭킹**으로만 |
| C4 Altman Z | ⚠️ 근사 | X2(이익잉여금) → `total_equity` 근사. 비제조 변형 미적용 |
| 이자보상배율(F5) | ⚠️ | 이자비용 별도 필드 없음 — 추후 DART 금융비용 파싱 시 활성화 |
| 컨센서스/목표주가 | ❌ | 미수집(설계 L4 제외). 룰에서 사용 금지 |

> 종합 분석은 **근사·부분 항목엔 confidence를 낮추고** 그 사실을 근거에 명시할 것. 미보유 데이터로 단정 금지(추측 금지 원칙).

---

## §H. 출력

이 문서는 **사전**이다. 무엇을 어떤 모양으로 내보낼지는 종합 분석 한 곳(`src/research/analyze/`)이 정한다 — 콜이 하나이므로 출력 계약도 하나여야 하고, 룰북마다 스키마를 두면 세 벌이 서로 어긋난다.

---

## §I. 출처 (Sources)

- Piotroski F-Score — [Wikipedia](https://en.wikipedia.org/wiki/Piotroski_F-score), [StableBread](https://stablebread.com/piotroski-f-score/)
- Graham 방어적 투자자 기준 — [Portfolio123](https://blog.portfolio123.com/a-stock-pickers-guide-to-benjamin-grahams-screening-rules/), [Old School Value](https://www.oldschoolvalue.com/investing-strategy/benjamin-graham-investing-checklist/)
- Magic Formula — [QuantifiedStrategies](https://www.quantifiedstrategies.com/the-magic-formula-strategy/), [StableBread](https://stablebread.com/magic-formula-investing/)
- Altman Z-Score — [Wikipedia](https://en.wikipedia.org/wiki/Altman_Z-score), [CFI](https://corporatefinanceinstitute.com/resources/commercial-lending/altmans-z-score-model/)
- Lynch PEG / GARP — [PEG ratio (Wikipedia)](https://en.wikipedia.org/wiki/PEG_ratio), [Nasdaq GARP](https://www.nasdaq.com/articles/growth-investing-with-a-value-twist)
- PER/PBR 해석·가치함정 — [NRI: PBR<1](https://www.nri.com/en/media/column/mcs_blog/20260108.html), [TitanFX](https://research.titanfx.com/glossary/what-is-per)
- DuPont ROE 분해 — [AnalystPrep](https://analystprep.com/cfa-level-1-exam/financial-reporting-and-analysis/dupont-analysis-of-return-on-equity/), [CFI](https://corporatefinanceinstitute.com/resources/accounting/dupont-analysis/)
- 부채비율·유동비율 벤치마크·레드플래그 — [Eqvista 업종별 D/E](https://eqvista.com/debt-to-equity-ratio-by-industry/), [AAII 16 red flags](https://insights.aaii.com/p/16-red-flags-to-watch-for-in-a-companys), [Damodaran](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/dbtfund.htm)
- 배당성향 지속성 — [AboveTheGreenLine](https://abovethegreenline.com/payout-ratio-and-sustainability/), [Investing.com](https://www.investing.com/academy/analysis/dividend-payout-ratio/)
- PSR / Fisher — [Validea](https://www.validea.com/kenneth-fisher), [CFI](https://corporatefinanceinstitute.com/resources/valuation/price-to-sales-ratio/)
- 이익의 질·발생액(Sloan/QoE) — [WallStreetPrep QoE](https://www.wallstreetprep.com/knowledge/quality-of-earnings-ratio/), [FMP](https://site.financialmodelingprep.com/education/statements/how-to-detect-earnings-quality-erosion-via-cash-flow-statement)
- 코리아 디스카운트·밸류업 — [Robeco](https://www.robeco.com/en-int/insights/2024/02/unraveling-the-korea-discount), [AMRO](https://amro-asia.org/narrowing-the-korea-discount-stock-market-reform-via-corporate-value-up), [GlassLewis](https://www.glasslewis.com/article/navigating-south-koreas-corporate-value-up-program)

---

## §J. 변경 이력
- 2026-05-24 — 초안. 11개 프레임워크/주제(Piotroski·Graham·Magic Formula·Altman Z·Lynch PEG·DuPont·PER/PBR·배당·PSR·이익품질·코리아밸류업) 다중출처 리서치 → 우리 보유 필드 매핑 + 계산가능성 명시 + 5축 종합 가이드.
- 2026-10-15 — 낡은 설명만 고침(규칙 내용은 그대로): 옛 경로 `src/agents/…` → `src/research/…`, 도메인별 "판단 에이전트" 문구 → 종합 분석 한 콜의 사전. 도메인별 판정은 2026-09-01에 폐기됐다.
