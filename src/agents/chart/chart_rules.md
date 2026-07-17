# 차트(정량) 판단 룰북 (Chart Judgment Rulebook)

> 이 문서는 **판단 에이전트(claude -p)** 가 차트 정량 분석 결과를 해석할 때 쓰는 가이드(하네스)다.
> 정량 파이프라인(`src/agents/chart`)은 **객관적 fact만 추출**한다 — 지표의 state·value, 구조 검출(S/R·추세선·패턴), 거래량 spike 등. **"그래서 매수/매도/위험"이라는 판단은 코드가 하지 않고, 이 룰북을 근거로 판단 에이전트가 한다.**
>
> 원칙:
> 1. **단일 지표 단독 판단 금지.** 1차 신호일 뿐. 항상 복합(§B 이상)으로 확인.
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
| `stochastic`/`cci`/`williams_r` | RSI와 동일 계열 | 〃 | **같은 모멘텀끼리는 중복** — confluence로 안 침(§B 원칙) |
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

## §B. 2-지표 복합 룰 (Composite — 실전 1차 판단 단위)

> 핵심: **서로 다른 축**을 결합한다 (추세+모멘텀, 추세+거래량, 변동성+거래량). 같은 축(RSI+Stoch)은 중복이라 confluence 효과 약함.

### B1. 추세 방향 × 모멘텀 진입 타이밍 (가장 기본)
- `ema_alignment=bull_stack` (추세 상승) **AND** `rsi=oversold & rising` (눌림목 과매도 반등) → **상승추세 내 매수 타이밍(눌림목 매수)**. 추세추종의 정석.
- `ema_alignment=bear_stack` **AND** `rsi=overbought & falling` → **하락추세 내 매도(반등 매도)**.
- ⚠️ 추세와 모멘텀이 **반대**면(bull_stack + overbought) → 추세는 살아있고 단기 과열일 뿐 → **매도 아님, 관망/눌림 대기**.

### B2. ADX 게이트 — 추세지표 vs 오실레이터 스위치
ADX가 어느 도구를 믿을지 결정한다.
- `adx_dmi=strong_trend` (ADX≥25) → **추세추종 모드**: MACD/Supertrend/EMA 정배열 신뢰↑, 오실레이터 과매수/과매도 역추세 신호 **무시**.
- `adx_dmi=no_trend` (ADX<20) → **레인지 모드**: 오실레이터(RSI/Stoch/CCI) 역추세 신뢰↑, 추세교차(MACD골든/데드)는 **휩쏘로 강등**.

### B3. 돌파 × 거래량 확인 (신뢰도 1차 필터)
- `donchian=breakout_up` (또는 `support_resistance.recent_break=break_up`, `trendlines.resistance.break=break_up`) **AND** `volume_spike.spike=true` → **진성 돌파(actionable 매수 포인트)**.
- 같은 돌파인데 `volume_spike.spike=false` → **페이크 돌파 경계** — 수 봉 내 레인지 회귀(fade) 가능성. 진입 보류 or retest 대기.
- 보강: 돌파 후 `cmf=accumulation` / `obv_trend=rising`이면 신뢰 추가.

### B4. 지지/저항 돌파 → 역할전환(retest) 매수
- `support_resistance.recent_break=break_up` (저항 상향 돌파) 후, 가격이 그 레벨 부근으로 되돌아왔다가(=옛 저항이 지지로) 다시 상승 → **breakout-retest 매수**(고신뢰 셋업).
- `nearest_support.tests`가 많을수록(≥3) 그 지지 신뢰↑. **너무 많이 테스트된 지지는 약해져 돌파 임박** 신호이기도 함.

### B5. 볼린저 + 모멘텀 (평균회귀 vs 워킹 구분)
- `bbands_pctb=below_lower` **AND** `rsi=oversold & rising` **AND** `adx=no_trend` → **레인지 하단 평균회귀 매수**.
- `bbands_pctb=above_upper` **AND** `adx=strong_trend` **AND** `ema_alignment=bull_stack` → **밴드워킹(강세 지속)** — 과매수지만 매도 아님.
- `bandwidth`(evidence)가 장기 최저 + `ttm_squeeze=squeeze_on` → **변동성 압축, 돌파 임박** → fired 방향 + 거래량으로 진입.

### B6. TTM Squeeze 발사 × 방향 확정
- `ttm_squeeze=fired_up` **AND** (`macd=above_signal` OR `donchian=breakout_up`) **AND** `volume_spike=true` → **압축 후 상방 폭발 진입**. 압축이 길수록(여러 봉 squeeze_on 후) 폭발 강도↑.

### B7. 피보나치 되돌림 × 추세
- `fibonacci.direction=up` (상승 레그) 이고 가격이 0.382~0.618 되돌림 구간 **AND** `ema_alignment=bull_stack` **AND** 그 구간에 `support_resistance.nearest_support` 또는 bullish `candles` → **눌림목 매수 존**(confluence 강).
- 0.618 이탈(78.6 향함)은 추세 훼손 경고.

### B8. 캔들 반전 × 위치
- bullish `candles`(hammer/bullish_engulfing/morning_star) **가 `nearest_support` 또는 피보 지지 구간 + `rsi=oversold`에서** 출현 → **반전 매수 신호 신뢰↑**. 캔들 단독은 약함, **위치(S/R)와 결합**이 핵심.
- bearish `candles`(shooting_star/bearish_engulfing/evening_star) at `nearest_resistance` + `overbought` → 반전 매도.

### B9. 다이버전스 × 위치·추세 (반전/지속 시그널)
`divergence[]`는 가격 모멘텀의 **이면 약화/강화**를 드러낸다 — 가격만 보면 안 보이는 신호.
- `regular_bullish` (가격 신저점인데 오실 저점은 높아짐) **AND** `nearest_support`/피보 지지 근접 **AND** bullish `candles` → **바닥 반전 매수 후보**(고전 셋업). 거래량(`volume_spike`)·`obv_trend=rising` 동반 시 신뢰↑.
- `regular_bearish` (가격 신고점인데 오실 고점은 낮아짐) **AND** `nearest_resistance` 근접 **AND** bearish `candles` → **천장 반전 경계**(§E8과 연동).
- `hidden_bullish`/`hidden_bearish`는 **추세 지속** 신호 — `ema_alignment` 방향과 일치하면 추세추종 재진입(B1/C6) 보강.
- ⚠️ 다이버전스는 **확인 전까지 1차 신호**. `bars_ago`가 크면(오래된 스윙) 유효성 약화. macd·rsi 둘 다 같은 방향 다이버전스면 신뢰↑(서로 다른 오실레이터 = 약한 confluence). **반전은 종가 확인봉 필수** — 다이버전스만으로 역추세 진입 금물(강추세에선 여러 번 무력화됨).

### B10. 피봇 포인트 (단기 지지/저항 레퍼런스)
`pivots`(classic PP·R1~R3·S1~S3)는 **당일/단기 평균가 기준 매물대**.
- 가격이 `PP` 위 = 단기 강세 편향, 아래 = 약세 편향. R1/S1은 1차 반전·이익실현 후보 레벨.
- `support_resistance.levels`(스윙 기반)와 피봇이 **겹치는 가격대**는 강한 confluence 레벨 → 진입/손절 기준으로 우선.
- 단독 사용 금지 — S/R·추세 컨텍스트의 **보조 좌표**로만.

---

## §C. 다중 복합 프레임워크 (Complex — 정통 셋업/시스템)

### C1. 멀티 타임프레임 정렬 (MTFA, 최우선 프레임워크)
월봉(방향)→주봉(셋업)→일봉(진입). **상위 TF가 허가증.**
- 월/주봉 `ema_alignment=bull_stack` (대세 상승) + 일봉 B1 눌림목 매수 신호 → **고신뢰 매수**.
- 상·하위 TF **충돌**(주봉 bear_stack인데 일봉 golden_cross) → 일봉 신호 **강등/관망**. 하위가 상위를 못 이긴다.
- 세 TF 모두 정렬 = 최강. (단 월봉은 데이터 길이 부족 시 `no_data` — 그땐 주봉이 HTF.)

### C2. 추세추종 풀세트 (Trend-Following)
`adx_dmi=strong_trend(di up)` + `ema_alignment=bull_stack` + `supertrend=up` + `macd above_zero` 동시 → **확립된 상승추세**. 진입은 눌림(B1/B7), 청산은 `supertrend=down` 전환 또는 `macd=dead_cross`. 횡보 휩쏘를 ADX 게이트로 차단.

### C3. 변동성 돌파 시스템 (Volatility Breakout)
`ttm_squeeze` 장기 `squeeze_on` → `fired_up` + `donchian=breakout_up` + `volume_spike` + `bbands_pctb` 상단 돌파 → **압축→폭발 매수**. 목표는 압축폭 기반, 손절은 중심선(ema20)/스퀴즈 저점.

### C4. 평균회귀 시스템 (Mean Reversion, 레인지 전용)
`adx=no_trend` 전제. `bbands_pctb=below_lower` + `rsi/stoch oversold & rising` + `nearest_support` 근접 → 하단 매수, 중심선(ema20) 목표. **추세장에서 절대 쓰지 말 것**(B2).

### C5. 반전 셋업 (Reversal, 천장/바닥)
- 바닥: `chart_patterns=double_bottom`(or 역배열 끝) + bullish `candles` at support + `obv_trend=rising`(다이버전스 대용) + `volume_spike` → 반전 매수. **추세 전환은 확인봉 필수**(종가 돌파).
- 천장: `double_top` + bearish candle at resistance + 거래량.

### C6. 풀백/되돌림 진입 (Pullback in Trend)
C2 확립 추세 + B7 피보 되돌림 + B1 모멘텀 반등 + B4 retest = 추세 내 저위험 재진입. 추세추종의 실전 진입 방식.

### C7. 스테이지 분석 (Weinstein 4-Stage — 큰 그림 분류)
`ema_alignment`(특히 ema200 기준 가격 위치)와 `adx_dmi`로 종목이 4단계 중 어디인지 분류한다. 진입 전략을 결정하는 **상위 프레임**.
- **1단계 바닥권(Basing)**: 장기간 횡보, `adx=no_trend`, 가격이 ema200 부근 평탄. → 관망/분할 관찰. 돌파(2단계 전환) 대기.
- **2단계 상승(Advancing)**: `ema_alignment=bull_stack`, ema200 상향 기울기, `adx=strong_trend(up)`. → **매수·추세추종 구간**(C2/C6). 핵심 보유 단계.
- **3단계 천장권(Topping)**: 상승 둔화, 변동성↑, `regular_bearish` 다이버전스(B9), `ema_alignment=mixed`로 전환. → 신규 매수 자제, 이익실현 고려.
- **4단계 하락(Declining)**: `ema_alignment=bear_stack`, ema200 하향. → 매수 금지, 반등은 기술적(§E1).
- 매수는 원칙적으로 **2단계에서만**. 4단계 종목 저가 매수는 §E1·E7 고위험.

> **신뢰도 가산 원칙(confluence)**: 서로 다른 축의 신호가 같은 방향을 가리킬수록 신뢰↑. 한 셋업에 (추세+모멘텀+거래량+구조) 4축이 정렬되면 최고 등급. **단일 축만이면 1차 신호에 불과.**

---

## §D. 타임프레임 컨텍스트

- **일봉(1d)**: 진입 타이밍·단기 신호. 휩쏘 많음.
- **주봉(1w)**: 중기 추세·셋업. S/R·추세선 신뢰 더 높음.
- **월봉(1mo)**: 대세 방향. 신호 빈도 낮으나 강함. (데이터 길이 부족 시 `no_data`.)
- **HTF 게이트**: 상위 TF 방향과 반대인 하위 TF 신호는 신뢰도 강등(C1). 정렬 시 가산.
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
→ 경고: "가격은 오르지만 매수 힘이 약해지는 중 — 단기 조정·천장 가능성(3단계 전환, C7). 추격매수 신중, 손절선 상향." 단정 금지 — 강추세에선 다이버전스가 여러 번 무력화되므로 **확인봉 전까지 경고로만**.

---

## §F. 신뢰도 보정 / 레드플래그 체크리스트

- ❑ 돌파인데 `volume_spike=false` → 신뢰 −
- ❑ 신호가 `adx=no_trend` 구간의 추세지표 교차 → 휩쏘, 신뢰 −
- ❑ 단일 축(예: 모멘텀만)에서만 신호 → 1차 신호, 단독 진입 금지
- ❑ 상위 TF와 방향 충돌(C1) → 강등
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

## §H. 종합 산출 가이드 (출력 스키마 — 풍부하게, 빠짐없이)

> 철학: **정보를 적게 주는 것보다 풀어서 많이 주는 게 낫다. 선택은 사용자(인간)의 몫이다.** 차트가 말하는 기회·위험·가능성을 **모두** 끌어내되, 비전문가가 읽을 수 있게 쉬운 한국어로 풀어 쓴다. 단, 단정·과신은 금물(차트는 정성 촉매를 모른다).

판단 에이전트는 아래 항목을 **빠짐없이** 채운다 (정량 단독 호출에서도, 후속 🧠통합에서도 재사용).

1. **summary_ko** — 비전문가용 2~3문장 큰 그림. "지금 이 종목 차트는 ~한 상태고, ~면 기회·~면 위험."
2. **score(-1~1) + label** — 5단계(strong_sell~strong_buy). **룰 기반 종합**이지 코드가 매기지 않는다. confluence·TF정렬·거래량확인으로 가감.
3. **confidence(0~1)** — confluence 정도(정렬된 축 수)·TF정렬·거래량확인·데이터 신선도(§F)로 산출. strength(신호 강도)와 confidence(합의도)는 별개. 데이터 부족·`mixed`/`in_cloud` 多면 낮춘다.
4. **trend_context** — 월/주/일봉 방향과 정렬 상태(§C1). "큰 흐름은 ~, 진입 타이밍은 ~." 상·하위 TF 충돌이면 명시.
5. **axes(4축 0~100, 50=중립)** — trend·momentum·volatility·volume 각 축이 **강세를 가리키는 정도**. 각 축 한 줄 쉬운 설명(axis_notes).
6. **setup** — 지금 가장 지배적인 셋업 1개: 이름(눌림목매수/돌파-retest/평균회귀/추세없음 관망/천장경계 등) + 적용 프레임워크 ID(§C) + stance(actionable_long/watch/actionable_short/stay_out) + 쉬운 설명.
7. **levels** — **구체 가격대**(actionable): 지지·저항(가격+터치수+이유), 진입존(조건 포함), 무효화선(=이 선 깨지면 시나리오 무효, 손절 기준), 목표(1·2차). S/R·피보·피봇이 겹치는 confluence 레벨 우선.
8. **scenarios** — bull/bear/base 3갈래로 풀어서. "만약 [조건: 예 저항 X를 거래량 동반 돌파]면 → [기대 Y]", "만약 [지지 Z 이탈]면 → [위험 W]". **기회와 위험을 모두 제시**.
9. **drivers / risks** — 강세·기회 신호 / 약세·위험 신호를 각각 풍부하게(끝에 적용 룰ID 태그 권장). 둘 다 비우지 말 것(확증편향 방지).
10. **risk_patterns(§E)** — E1~E8 위험패턴을 **항상 점검**, 활성된 것만 {pattern, active, plain} 으로 경고 병기(방향과 무관).
11. **watch** — 다음 봉/주에 지켜볼 트리거(예: "거래량 동반 X 돌파 확인", "RSI 50 회복", "지지 Z 사수 여부").
12. **methodology** — 적용한 룰 ID 목록(§A~§E, 예: ["C7","B1","B9","E8"]) — 설명가능성.

★ 정량 단독일 때 전제: "정성 촉매(뉴스·실적)는 미확인". 차트가 말하는 것과 차트로 알 수 없는 것(§E 경고)을 구분해 **오픈형**으로 제시한다.

---

## §I. 출처 (Sources)

1차 신뢰: StockCharts ChartSchool(지표·패턴 정의/신호), Investopedia, TradingView(Technical Ratings voting·임계), Wilder(ADX/RSI/ATR/PSAR), Bollinger(밴드룰), Carter(TTM Squeeze).
멀티TF·집계: NinjaTrader/Tradeciety(top-down), look-ahead(Corporate Finance Institute·Freqtrade).
위험패턴: Wikipedia(dead cat bounce), Hussman Funds(거래량과 베어랠리), strike.money, QuantifiedStrategies(mean reversion·Hurst).
구조 탐지: Lo·Mamaysky·Wang(2000) kernel regression, Bulkowski(차트패턴), scipy.signal.argrelextrema.

> ⚠️ 정직성: 패턴 신뢰도 %(예 "72%")는 출처별 표본·기준 상이 → 절대수치 신뢰 금지. 이격도·돌파 임계의 절대값은 종목/시장별 캘리브레이션 필요. MTF "승률" 수치는 블로그 자체값(학술 미검증).

---

## §J. 변경 이력

- 2026-05-26 — 초판. 정량 파이프라인 fact 추출(C0~롤백) 완료 후, 판단 룰을 분리하여 작성. 단일(§A)·2지표복합(§B)·정통프레임워크(§C)·위험패턴(§E) 망라. `fundamental_rules.md`와 동일 구조. 판단 에이전트(claude -p)가 §0 facts + 이 룰북으로 판단.
- 2026-05-27 — 판단 에이전트 빌드와 함께 보강. (1) **다이버전스 실제 fact화**(`divergence[]`, rsi·macd) → §0·B9·C7·E8·G 반영. (2) 50/200 골든·데드크로스 룰(§A). (3) 피봇 활용 룰(B10). (4) 스테이지 분석(C7). (5) §H를 "풍부한 출력 스키마"(summary/trend_context/axes/setup/levels/scenarios/drivers/risks/risk_patterns/watch/methodology)로 재작성 — "정보는 풀어서 많이, 선택은 인간" 원칙.
