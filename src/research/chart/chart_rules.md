# 차트(정량) 판단 룰북 (Chart Judgment Rulebook)

> 이 문서는 **판단 에이전트(claude -p)** 가 차트 정량 분석 결과를 해석할 때 쓰는 가이드(하네스)다.
> 정량 파이프라인(`src/agents/chart`)은 **객관적 fact만 추출**한다 — 지표의 state·value, 구조 검출(S/R·추세선·패턴), 거래량 spike 등. **"그래서 매수/매도/위험"이라는 판단은 코드가 하지 않고, 이 룰북을 근거로 판단 에이전트가 한다.**
>
> 원칙:
> 1. **단일 지표 단독 판단 금지.** 1차 신호일 뿐. 서로 다른 축이 겹칠 때만 근거가 된다(§B).
> 2. **추세장 vs 횡보장에서 같은 신호의 의미가 반대일 수 있다** (오실레이터 과매수가 추세장에선 강세 지속).
> 3. **돌파/반등은 거래량으로 확인.** 거래량 미동반 신호는 신뢰도 강등(§F).
> 4. **상위 타임프레임(월>주>일)이 방향을 제약한다** (§D).
> 5. 차트만으로는 "정성적 촉매 유무"를 모른다 — 위험 패턴(§E)은 **경고로 제시하되 단정하지 않는다**. 뉴스/재무와 합쳐질 때 확정된다.

---

## §0. 우리가 추출하는 facts (입력 레퍼런스)

판단 에이전트가 받는 입력. 룰은 이 이름·state로만 기술한다. 각 fact는 timeframe(`1d`/`1w`/`1mo`)별로 존재한다.

### 지표 facts (`timeframes.{tf}.indicators[]`) — `{indicator, state, value, evidence}`

**추세(trend)**
- `ema_alignment` — state: `bull_stack`(20>50>200) · `bear_stack` · `mixed`; evidence: `price_above_ema20`, `dist_to_ema20_pct`, ema20/50/200
- `macd` — state: `golden_cross` · `dead_cross` · `above_signal` · `below_signal`; evidence: `above_zero`, `hist`
- `adx_dmi` — state: `strong_trend`(ADX≥25) · `weak_trend`(20~25) · `no_trend`(<20); value=ADX; evidence: `di_direction`(up/down), +di/-di
- `supertrend` — state: `up` · `down`
- `ichimoku` — state: `above_cloud` · `below_cloud` · `in_cloud`

**모멘텀(momentum)** — 공통 state: `oversold` · `overbought` · `neutral`; evidence: `rising`(또는 k_above_d)
- `rsi`(14) · `stochastic`(14,3,3) · `cci`(20) · `williams_r`(14) · `mfi`(14, 거래량가중)

**변동성(volatility)**
- `bbands_pctb` — state: `above_upper`(%B>1) · `below_lower`(%B<0) · `inside`; value=%B; evidence: `bandwidth`
- `ttm_squeeze` — state: `squeeze_on`(압축중) · `fired_up` · `fired_down`; value=momentum
- `donchian` — state: `breakout_up`(N일 신고가) · `breakout_down` · `inside`

**거래량(volume)**
- `obv_trend` — state: `rising` · `falling` (OBV vs 20-EMA)
- `cmf`(21) — state: `accumulation`(>0.05) · `distribution`(<-0.05) · `neutral`
- `volume_spike` (지표 리스트와 별도, `timeframes.{tf}.volume_spike`) — `{spike: bool, ratio}` (최근봉 거래량 / 20봉평균)

### 구조 facts (`structure`, 일봉 기준)
- `support_resistance` — `levels[]`(price,tests), `nearest_support`/`nearest_resistance`(price,tests,distance_pct), `recent_break`(type: break_up/break_down, level, tests)
- `fibonacci` — `direction`(up/down), `swing_high`, `swing_low`, `levels`{0.236…0.786}
- `pivots` — classic PP/R1~R3/S1~S3
- `trendlines` — `support`/`resistance` 각 {slope, direction(rising/falling/flat), value_now, break(break_up/break_down/None)}
- `candles[]` — {pattern, direction(bullish/bearish/indecision)} (doji/hammer/shooting_star/marubozu/engulfing/harami/morning_star/evening_star)
- `gap` — {type(gap_up/gap_down), atr_mult}
- `chart_patterns[]` — {pattern(double_top/double_bottom), direction, level}
- `divergence[]` — 최신 두 스윙(저점=강세계열 / 고점=약세계열)의 **가격 vs 오실레이터(rsi·macd 히스토그램)** 괴리. `{indicator(rsi/macd), type, direction(bullish/bearish), price_points:[older,newer], osc_points:[older,newer], bars_ago}`
  - `type=regular_bullish` (가격 저점↓ + 오실 저점↑) = **반전 상승** 후보 / `regular_bearish` (가격 고점↑ + 오실 고점↓) = **반전 하락** 후보
  - `type=hidden_bullish` (가격 저점↑ + 오실 저점↓) = **상승추세 지속** / `hidden_bearish` (가격 고점↓ + 오실 고점↑) = **하락추세 지속**
- `recent_move` — **직전 하락 레그 + 현재 반등의 정량 지표**(데드캣바운스 E1·blow-off E4·capitulation E5 판별의 결정론 근거). 최근 확정 스윙고점 → 이후 최저점 = 하락 레그, 반등 종점 = 현재 종가. 필드:
  - `decline_from_high_pct` (직전 하락폭 %, +값) · `rebound_from_low_pct` (저점 대비 현재 반등 %) · `rebound_bars` (저점 이후 경과 봉)
  - `rebound_vol_ratio` (반등구간 평균거래량 ÷ 하락구간 평균거래량) — **<1 이면 거래량 미동반 반등**(데드캣 핵심 신호)
  - `fib_recovery` {0.382/0.5/0.618: {price, price_above}} — 하락폭의 피보 되돌림을 현재가가 회복했는지(false = 회복 실패)

> ⚠️ **facts 한계(§G 참조)**: 헤드앤숄더·삼각형·쐐기 등 복합 기하 패턴, Anchored VWAP, Volume Profile/POC는 현재 자동 추출 안 함. 해당 룰은 §G에서 "수동/근사/생략" 명시. (다이버전스는 이제 `divergence[]`로 실제 추출됨.)

---

## §A. 단일 지표 해석 룰 (Simple — 1차 신호, 단독 판단 금지)

| fact = state | 1차 의미 | 강세/약세 | 함정·주의 |
|---|---|---|---|
| `ema_alignment=bull_stack` | 정배열, 상승 추세 | 강세 | 가격이 ema20 한참 위면 과열(이격, §E) |
| `ema_alignment=bear_stack` | 역배열, 하락 추세 | 약세 | 역배열 반등은 대개 기술적 반등(§E dead cat) |
| `ema_alignment.evidence` ema50 vs ema200 | **골든크로스**(ema50>ema200) / **데드크로스**(ema50<ema200) — 리테일 대표 신호 | 강세/약세(대세) | 후행 지표(이미 한참 움직인 뒤 발생). 단독 진입 신호 아님 — 추세 **확인**용. 횡보장 잦은 거짓교차 |
| `macd=golden_cross` | 단기 모멘텀 상향 전환 | 강세 신호 | **횡보장 잦은 거짓교차**. 0선 아래 교차는 약함 |
| `macd=dead_cross` | 모멘텀 하향 전환 | 약세 신호 | 동일. above_zero 여부로 강도 구분 |
| `adx_dmi=strong_trend` | 추세 강함(ADX≥25) | 방향은 di_direction | **방향 아님, 강도만**. 추세추종 유효구간 |
| `adx_dmi=no_trend` | 추세 없음(<20) | 중립 | **이 구간 추세지표(MACD/ST) 신뢰 강등** → 오실레이터 우위 |
| `supertrend=up`/`down` | trailing-stop 방향 | 추세 추종 | **횡보장 50%+ 휩쏘** — ADX와 결합 필수 |
| `ichimoku=above_cloud` | 구름 위 = 강세 영역 | 강세 | `in_cloud`=추세 불명확(관망) |
| `rsi=oversold`(<30) | 단기 과매도 | 반등 가능 | **강한 하락추세선 70/30 무력** — oversold가 계속 깊어짐. rising 동반돼야 1차 신호 |
| `rsi=overbought`(>70) | 단기 과열 | 조정 가능 | **강한 상승추세선 과매수 오래 유지**(밴드워킹) — 매도신호 아님 |
| `stochastic`/`cci`/`williams_r` | RSI와 동일 계열 | 〃 | **같은 모멘텀끼리는 중복** — 넷이 다 과매도여도 근거는 하나(§B) |
| `mfi=oversold/overbought` | 거래량 가중 과매수도 | 〃 | RSI보다 반전포착 강하나 거래량 품질 의존 |
| `bbands_pctb=below_lower`(<0) | 하단밴드 이탈 | 평균회귀 반등 후보 | **추세장에선 하단 워킹** = 강한 하락 지속. 역추세 진입 금물 |
| `bbands_pctb=above_upper`(>1) | 상단밴드 돌파 | 과열 or 강세 워킹 | 밴드워킹(상단 연속)은 **강세 지속** — 매도신호로 오해 금지 |
| `ttm_squeeze=squeeze_on` | 변동성 압축(축적) | 방향성 없음 | **곧 폭발** 예고일 뿐. 방향은 fired 시 momentum |
| `ttm_squeeze=fired_up/down` | 압축 해소·변동성 폭발 | momentum 방향 | 짧은 압축 후 fired는 신뢰도 낮음 |
| `donchian=breakout_up` | N일 신고가 돌파 | 강세(추세추종) | **거래량 미동반 돌파 = 페이크 빈번**(§F). 횡보장 가짜돌파 |
| `obv_trend=rising` | 매집(누적거래량↑) | 강세 확인 | 절대값 무의미, 방향·다이버전스만 |
| `cmf=accumulation`(>0.05) | 자금 유입 우위 | 강세 확인 | **갭 무시** 약점 — OBV와 교차확인 |
| `volume_spike.spike=true` | 평균 1.5×+ 거래량 | 신뢰도 부스터 | **방향 없음**. 무엇을 확인하는지(돌파/반전)와 결합해야 |
| `gap=gap_up` | 상방 갭 | 맥락 의존 | breakaway(추세시작) vs exhaustion(끝물) 구분은 추세단계 필요 |

---

## §B. 축을 섞어서 볼 것 (원칙만)

명명된 셋업 목록은 두지 않는다. 이름을 주면 데이터를 읽는 대신 목록에서 가장
비슷한 이름을 고르게 되고, 해당되는 것이 없을 때도 억지로 하나를 고른다.
대신 이 도메인에서 신호가 서로를 보강하는 **방식**만 적는다.

- **서로 다른 축이 같은 방향을 가리킬 때만 confluence다.** 추세·모멘텀·변동성·
  거래량·구조는 서로 다른 축이고, RSI·스토캐스틱·CCI·윌리엄스는 **같은 축**이다.
  같은 계열 넷이 모두 과매도라고 해서 네 개의 근거가 되지 않는다 — 하나다.
- **ADX가 어느 도구를 믿을지 정한다.** `strong_trend`(≥25) 구간에서는 추세지표가
  유효하고 오실레이터의 역추세 신호는 반복해서 무력화된다. `no_trend`(<20)
  구간에서는 그 반대이고, 추세지표의 교차는 대개 휩쏘다.
- **상위 타임프레임이 하위를 제약한다.** 월>주>일. 방향이 충돌하면 하위 신호의
  신뢰도를 낮춘다. 하위가 상위를 이기지 못한다.
- **거래량은 방향이 아니라 확인이다.** 돌파·반등·반전은 거래량 동반 여부로
  신뢰도가 갈린다. `volume_spike=false`인 돌파는 저확신이다.
- **구조(S/R·피보·피봇)가 겹치는 가격대**는 단일 레벨보다 강하다. 진입·손절
  좌표는 겹치는 곳을 우선한다.

## §D. 타임프레임 컨텍스트

- **일봉(1d)**: 진입 타이밍·단기 신호. 휩쏘 많음.
- **주봉(1w)**: 중기 추세·셋업. S/R·추세선 신뢰 더 높음.
- **월봉(1mo)**: 대세 방향. 신호 빈도 낮으나 강함. (데이터 길이 부족 시 `no_data`.)
- **HTF 게이트**: 상위 TF 방향과 반대인 하위 TF 신호는 신뢰도 강등. 정렬 시 가산.
- **look-ahead 금지**: 모든 fact는 **확정봉(closed bar)** 기준. 진행 중 주/월봉은 분석에서 제외됨(파이프라인이 강제). 판단도 미완성봉을 가정하지 말 것.

---

## §E. 위험 / 함정 패턴 (경고 — 차트만으로는 단정 금지)

> 이 패턴들은 **감지 시 "위험 가능성"으로 제시**하되, "확실히 그렇다"고 단정하지 않는다. 정성적 촉매(뉴스·실적) 유무가 확정 짓는다. 그래프만 분석해도 **이 경고는 항상 함께 제시**한다.

### E1. Dead Cat Bounce (기술적 반등 → 재하락)
조건(차트, **이제 `recent_move`로 정량 판별**): `ema_alignment=bear_stack`(역배열) **AND** `recent_move.decline_from_high_pct ≥ 15`(직전 급락) **AND** `recent_move.rebound_vol_ratio < 1`(반등 거래량 미동반; `obv_trend=falling`/`cmf=distribution`이면 보강) **AND** `recent_move.fib_recovery["0.500"].price_above = false`(하락폭 50% 회복 실패).
- 4개 중 3개 이상 충족 시 active로 제시. `rebound_bars`가 작을수록(급반등) + `rebound_vol_ratio`가 낮을수록 위험도↑.
→ 경고: "정성적 촉매 없는 거래량 미동반 급반등은 추가 하락 후 신저가 가능성. 반등 지속은 거래량 동반·뉴스 확인 필요." 단정 금지 — 강한 호재(뉴스/실적)면 진성 반등일 수 있으므로 §종합에서 확정.

### E2. Bull Trap / Bear Trap (거짓 돌파 함정)
조건: `recent_break=break_up`인데 `volume_spike=false` + 직후 가격이 돌파 레벨 안쪽으로 재진입 + bearish `candles`.
→ 경고: "거래량 미동반 돌파 + 즉시 재진입 = 페이크 돌파 가능. retest 확인 전 추격 금지."

### E3. Overextension / 이격 과열
조건: `ema_alignment.evidence.dist_to_ema20_pct`가 비정상적으로 큼(과거 분포 대비 극단) + `bbands_pctb=above_upper` + `rsi=overbought`.
→ 경고: "평균(ema20)에서 과도 이격 → 최소 일시 되돌림(pullback) 위험. 추격매수 불리."

### E4. Blow-off Top (소진 천장)
조건: 포물선 급등(단기 큰 run) + `volume_spike`(거래량 클라이맥스) + 위꼬리 긴 캔들(shooting_star) + 종가가 고가서 크게 밀림.
→ 경고: "매수 소진 가능성, 급반전 위험."

### E5. Capitulation (투매 바닥) — 반대 방향 기회 경고
조건: 급락 + `volume_spike`(평균 2.5×+) + 긴 아래꼬리(hammer) + 종가 고가 근처.
→ 경고: "투매 클라이맥스 → 단기 바닥 가능. 단 진짜 바닥 확정은 거래량 동반 retest + 센티먼트 필요."

### E6. 거래량 없는 신호 전반
모든 돌파/반등/반전 신호는 `volume_spike=false`면 **신뢰도 강등**. "저확신 신호 — fade 가능."

### E7. 강추세 속 역추세 오실레이터
`adx=strong_trend`인데 오실레이터 과매수/과매도로 역추세 진입 → **고위험**. "추세는 예상보다 오래 extended 유지된다(B2)."

### E8. 약세 다이버전스 (상승 중 모멘텀 균열)
조건: `ema_alignment=bull_stack`(상승 중)인데 `divergence`에 `regular_bearish`(가격 신고점 + 오실 고점 약화), 특히 `nearest_resistance` 근접·`bbands_pctb=above_upper` 동반.
→ 경고: "가격은 오르지만 매수 힘이 약해지는 중 — 단기 조정·천장 가능성(추세 후반 전환 가능성). 추격매수 신중, 손절선 상향." 단정 금지 — 강추세에선 다이버전스가 여러 번 무력화되므로 **확인봉 전까지 경고로만**.

---

## §F. 신뢰도 보정 / 레드플래그 체크리스트

- ❑ 돌파인데 `volume_spike=false` → 신뢰 −
- ❑ 신호가 `adx=no_trend` 구간의 추세지표 교차 → 휩쏘, 신뢰 −
- ❑ 단일 축(예: 모멘텀만)에서만 신호 → 1차 신호, 단독 판단 금지
- ❑ 상위 TF와 방향 충돌 → 강등
- ❑ `in_cloud`(이치모쿠) / `mixed`(EMA) → 추세 불명확, 관망 가중
- ❑ 미완성봉 가정 / look-ahead → **금지**
- ❑ 위험패턴(§E) 활성 → 매수 신호라도 경고 병기
- ❑ 같은 계열 지표 중복을 confluence로 착각 → 금지(서로 다른 축이어야)

---

## §G. 계산 가능성 & facts 한계 (현재 미추출 — 룰 적용 시 대체/생략)

| 룰에 필요하나 미추출 | 대체/처리 |
|---|---|
| 다이버전스(가격 vs 오실레이터) | **이제 `divergence[]`로 추출됨**(rsi·macd, 최신 두 스윙만). 단 3개 이상 스윙에 걸친 장기 다이버전스, 스토캐스틱/CCI 다이버전스는 미포함 — `obv_trend`/`cmf` 방향 괴리로 보강 |
| 헤드앤숄더·삼각형·쐐기·깃발 | 현재 `double_top/bottom`만. 그 외 복합 기하 패턴은 **수동 또는 생략** |
| measured-move 목표가 | 패턴 높이 기반 — `chart_patterns.level`·S/R로 **근사** |
| Anchored VWAP / Volume Profile / POC | 미추출 — 매물대 룰은 `support_resistance.levels`(tests 가중)로 대체 |
| 갭 종류(breakaway/runaway/exhaustion) | `gap.type`+`atr_mult`만. 종류 분류는 추세단계 필요 → 맥락으로 추정 |
| 이격도 절대 임계 | 종목별 분포가 달라 **절대값 금지**. `dist_to_ema20_pct`를 상대적으로 해석 |

---

## §H. 출력

이 문서는 **사전**이다. 무엇을 어떤 모양으로 내보낼지는 종합 분석 한 곳(`src/agents/analyze/`)이 정한다 — 콜이 하나이므로 출력 계약도 하나여야 하고, 룰북마다 스키마를 두면 세 벌이 서로 어긋난다.

---

## §I. 출처 (Sources)

1차 신뢰: StockCharts ChartSchool(지표·패턴 정의/신호), Investopedia, TradingView(Technical Ratings voting·임계), Wilder(ADX/RSI/ATR/PSAR), Bollinger(밴드룰), Carter(TTM Squeeze).
멀티TF·집계: NinjaTrader/Tradeciety(top-down), look-ahead(Corporate Finance Institute·Freqtrade).
위험패턴: Wikipedia(dead cat bounce), Hussman Funds(거래량과 베어랠리), strike.money, QuantifiedStrategies(mean reversion·Hurst).
구조 탐지: Lo·Mamaysky·Wang(2000) kernel regression, Bulkowski(차트패턴), scipy.signal.argrelextrema.

> ⚠️ 정직성: 패턴 신뢰도 %(예 "72%")는 출처별 표본·기준 상이 → 절대수치 신뢰 금지. 이격도·돌파 임계의 절대값은 종목/시장별 캘리브레이션 필요. MTF "승률" 수치는 블로그 자체값(학술 미검증).

---

## §J. 변경 이력

- 2026-05-26 — 초판. 정량 파이프라인 fact 추출(C0~롤백) 완료 후, 판단 룰을 분리하여 작성. 단일(§A)·2지표복합(§B)·정통프레임워크·위험패턴(§E) 망라. `fundamental_rules.md`와 동일 구조. 판단 에이전트(claude -p)가 §0 facts + 이 룰북으로 판단.
- 2026-05-27 — 다이버전스 fact화, 골든·데드크로스, 피봇, 스테이지 분석, 풍부한 출력 스키마.
- 2026-09-01 — 명명 셋업 목록(§B 10개·§C 7개)과 룰 ID 인용 강제를 제거. 이름을 주면 데이터를 읽는 대신 목록에서 가장 비슷한 것을 고르고, 해당 없을 때도 억지로 하나를 고른다. 자리에 confluence·ADX 게이트·HTF·거래량 확인의 **원칙**만 남겼다. §0 필드 사전·§A 함정 열·§E 계산 가능한 위험 조건은 그대로 — 이것들은 처방이 아니라 사전이다.