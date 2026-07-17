# 종합(Judge) 판단 룰북

> 이 룰북은 **종합 분석 에이전트(claude -p)** 가 차트·재무·뉴스 세 도메인의 결정론적 facts와 (있으면) 캐시된 도메인 verdict를 종합해 단일 결론을 내릴 때 쓰는 가이드다.
>
> **읽는 법**: 각 룰은 ID(§D1, §E2 …)가 있다. 적용한 룰 ID를 `methodology` 배열에 남긴다. 단일 룰로 단정 금지 — 항상 2개 이상 룰을 결합한다.
>
> **원칙 7가지** (절대 안 깨짐):
> 1. **단일 도메인만으로 strong_buy/strong_sell 금지** — 한 축만 강하면 최대 buy/sell까지.
> 2. **drivers와 risks 둘 다 채운다** — 한쪽만 채우면 확증편향. 진짜 0이면 빈 배열로 두고 confidence 하향.
> 3. **결손 데이터는 confidence 페널티만** — 결손을 부정 신호로 해석 금지.
> 4. **추측 금지** — 사실로 단정할 근거 없으면 'watch' 또는 'limitations'에 남긴다.
> 5. **사용자는 비전문가** — `summary_ko`, `cross_reads.implication_ko`, `drivers`, `risks`는 모두 **쉬운 한국어** (전문용어 없이).
> 6. **score 범위 보수성**: 단기(immediate) 신호는 ±0.5 cap, ETF는 ±0.5 cap. 셋 다 완전 합의일 때만 ±0.8 이상.
> 7. **시점 우선순위**: 가장 빠른 동인(immediate > near > mid > long)이 score 방향을 끈다. 단 §G 예외.

---

## §A. 입력 계약 + 도메인 신호 표준화

### A1. 받는 입력
- `chart_facts`: timeframes(1d/1w/1mo) × indicators/structure (chart_rules.md §0 참조)
- `fundamental_facts`: 일반주는 valuation/derived/quality/valuation_band/sector, ETF는 `is_etf=true + etf={nav,premium_pct,return_3m_pct,aum_eokwon,category}`
- `news_state`: themes[] (primary full + related brief + macro background), watchlist
- `cached_verdicts`: chart/fundamental/news 의 last verdict (24h 이내) — **있으면** 사용
- `holding_context`: qty, avg_cost, current_price, pnl_pct, weight_in_account

### A2. 5-level 라벨 사전 (3 도메인 공통)
`strong_sell < sell < neutral < buy < strong_buy` ↔ score 임계 −0.5/−0.1/+0.1/+0.5

### A3. priced_in 사전
- `unpriced`: 시장이 모르거나, 알아도 가격에 반영 안 됨
- `partially_priced`: 절반 정도 반영
- `mostly_priced`: 거의 다 반영 (호재면 buy-the-rumor-sell-the-news 위험)

### A4. time_horizon 사전
- `immediate` ≤ 1주 / `near` 1~4주 / `mid` 1~6개월 / `long` 6개월+

---

## §B. 단일 도메인 핵심 신호 (참조 — 단독 판단 금지)

### B1. chart (정량) — 강세/약세 추정 키
- **강세 후보**: ema_alignment=bull_stack + macd above_signal + adx>25 + obv rising
- **약세 후보**: ema_alignment=bear_stack + macd below_signal + cmf=distribution
- **반전 후보**: regular_bullish/bearish divergence + RSI extreme
- **추세 지속 후보**: hidden_bullish/bearish divergence

### B2. fundamental — 강세/약세 추정 키
- **강세 후보**: piotroski_f≥7, altman_z>3, valuation_band.per_pctile<30 (싸다), derived_series margin/ROE 상승 추세
- **약세 후보**: altman_z<1.8, qoe<0.5 (현금흐름 약함), sloan_accrual 큼 (회계 빨강 신호), 매출 yoy 마이너스 지속
- **레드플래그**: red_flags 비어있지 않음

### B3. news — 강세/약세 추정 키
- **강세 후보**: recent_developments 다수가 lead_lag=lagging/coincident 호재 + watchlist 강함
- **약세 후보**: sentiment.score<−0.3 + trend=worsening
- **변동**: lead_lag=leading 비중이 큰 경우 → 점수 작게, watch 크게

### B4. fundamental ETF 키 (`is_etf=true`)
- **양호**: |premium_pct|<0.5, aum_eokwon>500, value_mwon>1000
- **주의**: |premium_pct|>2 (NAV 추적 부족), aum<300 (청산위험)

---

## §C. 합의 / 충돌 매트릭스

3 도메인의 라벨 그룹화:
- `+`  = buy/strong_buy
- `0`  = neutral
- `−`  = sell/strong_sell
- `?`  = insufficient_data

### C1. 완전 합의 (`+++` 또는 `−−−`)
- score 폭 확장 가능 (최대 ±0.85), confidence boost (+0.15)
- cross_reads에 "도메인 합의" 패턴 기록

### C2. 2:1 약한 합의 (`++0`, `++−`, `−−0`, `−−+`, `+00`, `−00`)
- 다수 방향 채택, 단 충돌 도메인의 reason을 risks/disagreement_note에 인용
- score는 다수 방향 ±0.3~0.5 범위

### C3. 강한 충돌 (`+−` 두 도메인이 정반대 + 세번째가 ±)
- 시점 우선(§G) + priced_in 재평가(§H) 적용
- 결정 못 하면 neutral + disagreement_note 강제

### C4. 분산 (`+0−`, `0+−`, `?+−` 등)
- 보수적 neutral, confidence × 0.7
- "지금은 한 방향이 아닙니다" 명시

### C5. 결손 도메인 (`?` 1개)
- 남은 2 도메인으로 판단, confidence 페널티 −0.15
- limitations에 결손 도메인 명시

### C6. 결손 2개 이상
- score=0, label=neutral, status=insufficient_data
- "데이터 더 모인 뒤 다시" 메시지

---

## §D. Pair-wise 교차 휴리스틱 (1차 — 흔히 쓰이는 격언 13개)

이 섹션은 **2개 도메인이 만나는 표준 패턴**. 적용했으면 `cross_reads[]`에 pattern 이름과 evidence 남긴다.

### D1. Buy the rumor, sell the news
- **조건**: news.priced_in=mostly_priced + news=호재 + chart의 RSI>70 OR bbands_pctb=above_upper
- **함의**: 단기 이익실현 가능성. 호재가 강해 보여도 점수 +0.2~+0.3 cap.
- **action 힌트**: 보유면 trim 검토, 미보유면 watch (조정 시 진입).

### D2. Cheap with no catalyst (Value Trap)
- **조건**: fund.valuation_band.per_pctile<25 (역사적 하단) + news.recent_developments 신호 없음 + chart=ranging/base
- **함의**: 가치주처럼 보이지만 촉매 없음. "value trap" 위험.
- **risks에 항상 추가**: "촉매 없이 싸기만 한 종목은 더 싸질 수 있음".

### D3. Quality on sale
- **조건**: fund.piotroski_f≥7 + chart.rsi<35 (oversold) + news neutral~unpriced
- **함의**: 좋은 회사가 일시적 약세. 분할매수 setup.
- **action**: add or watch.

### D4. Earnings beat + chart breakout
- **조건**: fund.derived_series 매출/이익 yoy 가속 + chart.donchian=breakout_up + chart.volume_spike.spike=true
- **함의**: 펀더와 모멘텀 동시 강세. strong_buy 후보.

### D5. News-driven gap + fundamental weakness
- **조건**: chart.gap=gap_up + fund.red_flags 1개 이상 + news.time_horizon=immediate
- **함의**: 갭은 보통 채워진다 (gap fill). 펀더 약하면 더 빨리.
- **action**: trim or pass.

### D6. Hidden bullish divergence + growth
- **조건**: chart.divergence.type=hidden_bullish + fund 매출 yoy>10% 지속
- **함의**: 추세 지속 신호. 조정은 매수 기회.

### D7. Bearish divergence + news priced-in
- **조건**: chart.divergence.type=regular_bearish + news.priced_in=mostly_priced
- **함의**: 위 두 신호가 동시에 나오면 천장 신호. 분할 매도 검토.

### D8. Sector tailwind + leader stock
- **조건**: news.primary_theme 강세 (sentiment.score>+0.3) + fund.altman_z>2.5 + chart 추세 정방향
- **함의**: 섹터 동력의 leader. hold/add.

### D9. Macro headwind + defensive quality
- **조건**: news.macro state.sentiment.score<−0.3 + fund.quality (qoe>0.8, altman_z>3) + 배당주
- **함의**: 매크로 약세에서 방어 자산. trim 금지, hold.

### D10. Buy and pray (위험)
- **조건**: chart 강세 + fund.red_flags 있음 + news=neutral
- **함의**: 모멘텀만으로 가는 상승. "주의 매수" — 비중 제한.
- **risks에 반드시 표기**.

### D11. Volume confirms
- **조건**: chart breakout (donchian or trendline) + volume_spike + news.lead_lag=leading 또는 coincident
- **함의**: 진성 돌파. conviction +.

### D12. No volume rally (가짜 상승)
- **조건**: chart 상승 + volume_spike=false + cmf<0
- **함의**: 거래량 미동반 = 신뢰도 낮음. confidence 하향.

### D13. 3-way divergence (셋 다 다른 방향)
- **조건**: chart=−, fund=+, news=0 (등 어떤 조합이든 합의 못함)
- **함의**: neutral + disagreement_note 의무. cross_reads에 각 도메인의 입장 명시.

---

## §E. priced-in × time-horizon × setup (2차)

| ID | 조건 | 함의 |
|---|---|---|
| E1 | news=unpriced (immediate) + chart=base (낮은 변동성, ranging) | strong setup. score +0.4 가산. |
| E2 | news=mostly_priced (near 호재) + chart=과열(RSI>70) | 천장 가능. score −0.3. |
| E3 | news=partially_priced (mid) + chart=base | 점진적 매집 시점. add. |
| E4 | news=unpriced (long) + chart 저변동 | 장기 보유 강화. 단기 변동 무시 권고. |
| E5 | news=mostly_priced (immediate) + chart=breakdown (ema_alignment→bear, support 이탈) | exit 신호. |
| E6 | news 룰북이 추정한 priced_in confidence<0.4 | §H로 재평가 |
| E7 | time_horizon=long + fund 약함 (altman_z<2, qoe<0.5) | "장기 호재라도 펀더 약하면 못 버틴다" — 비중 축소. |
| E8 | cached chart verdict<24h old + 차트 신호 강함 | chart 가중치 +0.1 |
| E9 | time_horizon=mid + macro 부정 | 진입 보수화, watch만 |

---

## §F. Quality × Macro × Sector RS × Reflexivity (3차)

| ID | 룰 |
|---|---|
| F1 | **Defensive overweight**: macro=headwind + fund.quality 상위 + holding_context.weight<10% → "있으면 더 사도 됨" |
| F2 | **Concentration risk**: holding_context.weight>20% + 단일 도메인이라도 sell → trim 권고. risks에 "비중 집중" 명시 |
| F3 | **Sector RS leader**: 같은 sector 내 chart.ema_alignment=bull_stack + fund.piotroski_f≥7 → hold/add |
| F4 | **Reflexivity warning**: news.sentiment.trend=improving + chart 급등 + volume_spike 연속 → 모멘텀 자기실현 단계. confidence ↑ 금지 |
| F5 | **Earnings-window guard**: time_horizon=immediate + watchlist에 "실적발표" 항목 있음 → "이벤트 전 비중 축소 고려" |
| F6 | **Currency overlay (KR)**: news.macro=원화 약세 + 회사 수출 비중 추정 가능 → 별도 메모, 점수 미반영 |
| F7 | **ETF macro coupling**: ETF이고 primary=macro → news.macro의 sentiment 가중치 ↑ (그 외 테마 ↓) |
| F8 | **Premium drift**: ETF.premium_pct 절댓값이 1% 넘으면 chart 신호 신뢰도 ↓ (괴리율 큰 ETF는 NAV 추적 부정확) |

---

## §G. 시점 정합성

도메인별 time_horizon 이 다를 때:
1. 기본: 가장 **빠른 동인**이 score 방향을 끈다 (immediate > near > mid > long).
2. 예외: 빠른 도메인 confidence<0.4 AND 느린 도메인 confidence>0.7 → 느린 도메인 채택.
3. final time_horizon은 score 방향을 끈 도메인의 값.
4. disagreement_note 의무: 채택하지 않은 도메인의 시점·이유 1줄 언급.

---

## §H. priced_in 재평가 (Cross-domain Re-anchoring)

news 룰북은 가격을 못 본다. 종합 단계에서 차트 정보로 재평가:

- chart.3M_return>40% OR ema_alignment=bull_stack AND RSI>70 → news.priced_in 한 단계 상향
- chart.3M_return<−30% OR ema_alignment=bear_stack AND RSI<30 → news.priced_in 한 단계 하향
- 보정은 score 만 조정, confidence 변경 없음
- 적용 시 cross_reads에 "priced_in_reanchor" 패턴 남김

---

## §I. 신뢰도 통합

```
domain_confs   = [c for c in [chart.conf, fund.conf, news.conf] if c is not None]
mean_conf      = sum(domain_confs) / len(domain_confs)
agreement      = 0..1                  # 셋 다 같은 방향이면 1, 분산이면 0
missing_penalty = 0.15 * (3 - len(domain_confs))
disagreement_penalty = 0.3 * (1 - agreement)

confidence = max(0, mean_conf - missing_penalty - disagreement_penalty)
```

agreement 계산: 라벨 부호(+/0/−)의 다수 비율. 셋 다 + → 1.0, 2+1 → 0.66, 분산 → 0.33.

---

## §J. 사용자 액션 매핑

| 조건 | action_ko |
|---|---|
| strong_buy + (미보유 OR weight<5) | add |
| strong_buy + 5≤weight≤15 | hold |
| strong_buy + weight>15 | hold (집중 경고는 risks에) |
| buy + 보유 | hold |
| buy + 미보유 | watch |
| neutral | hold (보유 시), watch (미보유 시) |
| sell + 보유 | trim |
| strong_sell + 보유 | exit |
| ETF + 어떤 라벨 | hold/add/watch 중에서만 (trim/exit 거의 없음) |
| insufficient_data | hold + limitations 명시 |

---

## §K. 안전장치 (체크리스트, 결과 내기 전 검증)

1. ☐ 단일 도메인만으로 strong_buy/strong_sell 안 냈는가?
2. ☐ drivers와 risks 둘 다 비어있지 않은가? (둘 다 비면 confidence ≤ 0.4)
3. ☐ limitations에 결손 도메인·근사 항목을 적었는가?
4. ☐ priced_in 추정 confidence 낮으면 partially_priced로 디폴트했는가?
5. ☐ time_horizon=immediate 인데 score 절댓값>0.5 가 아니라 ≤0.5 로 제한했는가?
6. ☐ ETF 인데 score 절댓값이 0.5 이하인가?
7. ☐ methodology 배열에 적용한 룰 ID(§D~§F)가 최소 2개 이상인가?
8. ☐ cross_reads 의 implication_ko 가 쉬운 한국어인가 (전문용어 사용 시 풀어썼는가)?

---

## §L. 케이스북 (참고 시나리오)

### L1. 합의 매수 (Quality leader, sector tailwind)
- inputs: chart=buy(bull_stack, ADX 30) / fund=buy(piotroski 8, per_pctile 35) / news=buy(primary=ai_sw, sentiment +0.5)
- 적용 룰: §D4, §D8, §F3, §C1
- 결과: label=strong_buy, score=+0.65, action=hold (이미 보유), confidence=0.82

### L2. 과열 + 호재 반영 (sell the news)
- inputs: chart=sell(RSI 78, divergence=regular_bearish) / fund=buy(여전히 좋음) / news=sell(mostly_priced)
- 적용 룰: §D1, §D7, §E2, §C3
- 결과: label=sell, score=−0.25, action=trim, confidence=0.7, summary="좋은 회사지만 단기 과열 + 호재 반영. 일부 차익실현 검토."

### L3. ETF 광역지수
- inputs: chart=neutral / fund(ETF)=neutral(premium −0.2%, AUM 18조, 3M return +12%) / news.macro=mixed
- 적용 룰: §F7, §C4 (분산), §M
- 결과: label=neutral, score=+0.05, action=hold, confidence=0.6

### L4. 액티브 ETF (KoAct 바이오)
- inputs: chart=buy / fund(ETF)=ok(premium −0.4%, AUM 547억) / news=buy(bio + regulation_fda watchlist)
- 적용 룰: §D8, §F7, §M
- 결과: label=buy, score=+0.35, action=add, confidence=0.65

### L5. Value trap 의심
- inputs: chart=neutral(base) / fund=cheap(per_pctile 15) / news=neutral (촉매 없음)
- 적용 룰: §D2
- 결과: label=neutral, score=−0.05 (살짝 음수), action=watch, risks=["촉매 없는 저평가는 더 빠질 수 있음"]

### L6. 결손 도메인 1개 (data poor)
- inputs: chart=buy / fund=insufficient_data(financials 0) / news=buy
- 적용 룰: §C5
- 결과: label=buy, score=+0.3 (대신 0.4가 아닌 0.3), confidence=0.5, limitations=["재무 데이터 부족"]

### L7. 충돌 (펀더 강 + 차트 약)
- inputs: chart=sell(breakdown) / fund=strong_buy(piotroski 9) / news=neutral
- 적용 룰: §C3, §G (chart=immediate vs fund=long)
- 결과: label=neutral, score=−0.1 (차트 시점이 빠르므로), action=hold, disagreement_note="재무는 매우 견고하지만 단기 차트가 약함 — 단기 변동을 기회로 분할매수 가능"

### L8. 3-way divergence
- inputs: chart=buy / fund=sell / news=neutral
- 적용 룰: §D13, §C4
- 결과: label=neutral, score=0.0, disagreement_note 의무, confidence=0.4

### L9. Concentration risk
- inputs: chart=sell + holding_context.weight=28%
- 적용 룰: §F2
- 결과: label=sell, action=trim (강도 ↑), risks 최상단에 "비중 집중"

### L10. Reflexivity warning
- inputs: chart=급등 3주 연속 + 거래량 spike + news.sentiment.trend=improving 가속
- 적용 룰: §F4
- 결과: label은 그대로 buy/strong_buy, confidence 상한 0.7, risks에 "모멘텀 자기실현 단계 — 조정 가능"

### L11. Earnings window
- inputs: 다음주 실적발표 watchlist + chart=neutral + fund=buy
- 적용 룰: §F5
- 결과: action=watch (보유면 hold), risks="실적 발표 변동성"

### L12. Macro headwind + defensive
- inputs: news.macro=금리 인상 우려 / fund=quality 상위(altman_z 5) + 배당주 / chart=base
- 적용 룰: §D9, §F1
- 결과: label=buy, action=hold (있으면 add), drivers="매크로 약세 방어자산"

---

## §M. ETF 분기 (는 §F7/§F8 + 다음 규칙)

ETF는 다음 점에서 일반주와 다르게 다룬다:
- score 절댓값 cap = 0.5 (예외: 극단적 premium 이탈이나 청산 위험은 더 큰 음수 허용)
- action 허용: hold / add / watch (trim/exit는 거의 없음)
- summary_ko에 "ETF" 명시: "이 ETF는 …"
- 4 axes (tracking/liquidity/recent_return/fit) 중 어느 게 강/약인지 axis_notes에 1줄로 풀어쓰기
- premium_pct 절댓값>1 시 risks에 "NAV 추적 부정확"
- aum_eokwon<300 시 risks에 "청산 가능성 / 매매 슬리피지"
