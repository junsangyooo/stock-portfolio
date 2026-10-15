# 정성 해석 사전 (Qualitative Reference)

> **용도**: 종합 분석(claude -p 한 콜, `src/research/analyze`)이 정성(뉴스·이슈·트렌드·연구) 시그널을 해석할 때 참조하는 뉴스 도메인의 사전. 재료는 `src/research/news`가 모은다.
> 정량(chart)·펀더멘털(fundamental)과 분리된 **정성 전용** 룰. 검증된 시장 프레임워크(EMH·PEAD·buy-rumor-sell-news·2차 사고·리플렉시비티·hard/soft 카탈리스트·정보확산·내러티브 이코노믹스)를 우리가 실제 수집하는 정성 필드에 1:1 매핑.
>
> **사용법**:
> 1. 종목의 테마 매핑(`symbol_themes`) + 각 테마의 상태(일간 7일 창·주간·월간)를 입력으로 받는다.
> 2. 아래 4개 렌즈를 **순서대로** 적용한다 — §B 반영도 → §C 방향·강도 → §D 시점·지속성 → §E 2차 파급 → §F 종목 귀속.
> 3. 단일 시그널로 단독 결론 금지 — **§A 분류 + §B~§E 4렌즈를 결합**하고 **§G 함정**을 교차 점검한다.
> 4. 이 문서는 종합 분석 1콜에 **뉴스 도메인의 사전**으로 주입된다. 도메인 단독 산출물은 없다.

---

## §0. 우리가 보유한 필드 (입력 레퍼런스)

종합 분석이 쓸 수 있는 실제 데이터. 룰은 이 필드명으로만 기술한다. (다른 필드를 지어내지 말 것.)

> ⚠️ **두 개의 서로 다른 `relation`을 혼동하지 말 것** — 아래 (1)과 (3)은 이름만 같고 별개의 필드다.

### (1) 종목↔테마 관계 (`symbol_themes.relation`)
이 종목이 어떤 테마에 속하는지. **종목 귀속(§F)에 사용.**
- `relation` ∈ **`primary`**(이 종목의 핵심 테마) · **`related`**(연관 테마) · **`macro`**(거시 배경)
- `weight` (0.0~1.0) — 이 테마가 종목에 꽂히는 강도

### (2) 테마 rolling state (`state`, 테마 단위 1벌 — §F로 종목에 귀속)
- `horizon_summary` — 장기추세 2~3문장 (천천히 변함, 거시백본 역할)
- `recent_developments[]` — 각 항목: `date` · `point`(한 문장) · **`lead_lag`** · `weight`(0~1, 중요도/5) · `novelty`(bool, 신규성 — 신규 데이터에만 존재) · `url`
- `watchlist[]` — 전개중/예정 이슈 (아직 안 터진 카탈리스트 = 미반영 후보)
- `sentiment` — `score`(-1~1) · `trend` ∈ `improving`|`flat`|`deteriorating`
- `last_shift` — 센티먼트 방향 전환 이력 ("YYYY-MM-DD: 중립→약세 (이유)")

### (3) 테마↔테마 엣지 (`edge_relation` — related 테마에만 주입)
이 종목의 **primary 테마**에서 본 related 테마의 밸류체인 위치. **2차 파급(§E) 경로 해석에 사용.**
- `edge_relation` 예: `upstream`(소재·부품) · `downstream`(수요·완성품) · `supply` · `demand` · `application` · `adjacent` · `policy` · `power` · `brain` · `naval` · `lng_demand` · `drug_discovery` 등 (테마마다 다양 — 고정 enum 아님).
- ⚠️ **부호는 enum 이름이 아니라 §C1 "종목 입장"으로 재판정**한다(`upstream`이라고 무조건 +가 아님). enum은 방향 힌트일 뿐.

### 시그널 시간축 (`lead_lag`) — 정보확산 단계의 핵심 축
| 값 | 소스 | 의미 | 반영도 시사 |
|---|---|---|---|
| **`leading`** | arXiv·연구 | 과학·기술 선행 신호, 패러다임 씨앗 | **대부분 미반영** (시장 인지 전). 장기·확률적 |
| **`coincident`** | 소셜·X | 실시간 담론·관심 | **반영 진행 중** (내러티브 형성기) |
| **`lagging`** | 뉴스·RSS | 이미 공개된 확정 이벤트 | **상당부 선반영 가능** (공개 시점=가격 반응 시점) |

> ⚠️ **미보유·부분보유(룰 적용 시 주의)**:
> - **실제 주가·거래량** → chart 도메인(같은 콜의 차트 입력). 뉴스 입력에는 없으므로 이 사전의 "반영도"는 가격이 아니라 정보확산 단계로 **추정**(§B·§I).
> - **컨센서스 추정치·목표주가** 미수집 → "기대 대비"(§C2)는 `watchlist`·`sentiment` 대용치로만 추정.
> - **정확한 이벤트 예정일** 부재 → time_to_impact(§D2)는 카테고리 기반 근사.
> - **`novelty`는 신규 수집분에만 존재** — 과거에 쌓인 `recent_developments` 항목엔 없을 수 있다. 없으면 `lead_lag` 전이(coincident→lagging)·`watchlist` 신규 등장으로 **대체 추정**(§B1).
> - 개별 기사 본문 전문 없음(digest 요약만). 이들이 필요한 판단은 §I에서 신뢰도하향을 명시한다.

---

## §A. 이벤트 분류 체계 (Event Taxonomy — 1차 신호, 단독 판단 금지)

각 `development`를 먼저 카테고리로 태깅한다. 카테고리마다 **기본 방향성·확산속도·반영 난이도**가 다르다. (방향은 §C, 반영도는 §B에서 확정 — 여기선 분류만.)

| 카테고리 | 예시 | 기본 영향 시간축 | hard/soft | 주의 |
|---|---|---|---|---|
| **규제·정책** | 보조금·관세·인증·금지·밸류업 | 중기 (정책 시차) | soft→hard | 방향 양면. 수혜/피해 종목 갈림 |
| **M&A·지분** | 인수·합병·매각·지분투자 | 즉시~단기 | hard | 발표=대부분 즉시 반영. 루머=soft |
| **실적·가이던스** | 어닝·수주·가이던스 상하향 | 즉시 + 드리프트(PEAD §B3) | hard | **기대 대비**가 전부(§C2) |
| **제품·기술돌파** | 신제품·수율·임상·성능기록 | 단기~중기 | soft | 양산/매출 연결 시차 큼 |
| **수급·증설** | 증설·감산·재고·가동률·가격 | 중기 | soft | 공급↑=가격↓(생산자 악재일 수 有) |
| **공급망** | 핵심 고객/공급사 이벤트 | 시차 확산(§E1) | soft | read-through로 귀속(§F) |
| **경쟁·점유율** | 신규진입·점유율·치킨게임 | 중기 | soft | 테마 호재 ≠ 개별주 호재 |
| **소송·제재·사고** | 리콜·벌금·제재·화재·해킹 | 즉시(악재) + 꼬리 | hard | 불확실성 해소 시 오히려 반등 |
| **경영·지배구조** | CEO·배당·자사주·분할·오너 | 즉시~중기 | hard/soft | 거버넌스 = KR 밸류업 연결 |
| **기초연구(leading)** | arXiv 신소재·신이론 | **장기·확률적** | soft | 상용화까지 수년. 대체위협 씨앗(§E2) |
| **거시(macro)** | 금리·환율·유가·경기·지정학 | 배경·상시 | — | primary 아니면 배경으로만(§F3) |

> **규칙**: 카테고리는 방향을 **결정하지 않는다**. "증설"이 수요주(호재)엔 +, 공급과잉을 부르면 생산자(악재)엔 −. 반드시 §C에서 종목 입장으로 방향을 정한다.

---

## §B. 렌즈 1 — 반영도 판정 ("벌써 시장에 반영됐나?")

정성 신호의 가치는 **새로움(novelty)**에 있다. 이미 가격에 반영된 정보는 추가 알파가 없다. **우리는 가격을 직접 못 보므로(§I) 반영도는 "정보확산 단계"로 추정한다.**

### B1. 확산 단계 → 반영도 추정 (lead_lag + novelty + watchlist)
> `novelty`가 없으면 **`lead_lag` 단계 + `watchlist` 신규 등장 여부**로 대체 추정한다(§0). novelty는 보조 신호일 뿐, 토대는 lead_lag·watchlist다.

| 단계 | 신호 특징 | 추정 반영도 | 함의 |
|---|---|---|---|
| **미반영 (선행)** | `leading`, 또는 watchlist에 **새로 등장**한 미발생 이벤트 (novelty 있으면 true) | 거의 0% | **알파 잠재 최대** (단 불확실·장기) |
| **반영 진행 (동행)** | `coincident` 담론 급증, 내러티브 형성기 | 0~60% | 모멘텀 구간. 과열 주의(§G2) |
| **선반영/소멸 (후행)** | `lagging` + **오래 watchlist에 머문 사안의 "확정"** (novelty 있으면 false) | 60~100% | 추가 상승동력 약함. §B2 적용 |

### B3. PEAD — 과소반응 드리프트 (미반영 잔여)
반대 케이스: **놀라움이 큰**(`weight` 高 + novelty 있으면 true, watchlist에 없던) `lagging` 이벤트는 시장이 즉시 다 반영하지 못하고 **수일~수주에 걸쳐 드리프트**(과소반응). → 확정 뉴스라도 **신규성·서프라이즈가 크면** 잔여 미반영분을 인정(점수 유지).
- ⚠️ PEAD는 대형주에서 2006년 이후 상당부 소멸(시장 효율화). KR 중소형·정보비대칭 종목에 더 강함 → **대형 우량주엔 드리프트 가정을 약하게**.

### B4. 반영도 종합 산출
`priced_in` ∈ {`unpriced`(미반영), `partially_priced`(부분), `mostly_priced`(대부분)} 로 라벨.
- 점수 기여 = 방향(§C) × **(1 − 반영도)** 가중. 같은 호재라도 `unpriced` > `partially_priced` > `mostly_priced` 순으로 점수에 크게 반영.

---

## §C. 렌즈 2 — 방향·강도 ("호재인가 악재인가, 얼마나?")

### C1. 종목 입장 기준 (테마 호재 ≠ 종목 호재)
방향은 **이 종목 관점**에서 정한다. 같은 사실도 §F relation·밸류체인 위치에 따라 부호가 바뀐다.
- 예: "배터리 증설 가속" → 셀 메이커=수요 신호(+), 그러나 양극재 공급과잉 신호면 소재사=가격압박(−).
- 예: "Mg이온 양극재 발견(leading)" → 배터리 테마엔 중립, **리튬 양극재 전문기업엔 장기 대체위협(−)** (실데이터 에코프로비엠 케이스).

### C2. 기대 대비 서프라이즈 (Second-Level Thinking — 가장 중요)
> Howard Marks: **시장을 움직이는 건 "좋다/나쁘다"가 아니라 "예상보다 좋다/나쁘다"다.**

방향·강도는 **사실의 절대 톤이 아니라 기대 대비 편차**로 본다. 우리는 컨센서스를 미보유(§I)하므로 **대용치**로 추정:
- `watchlist`·`horizon_summary`에 **이미 예고된** 사안 = 기대 형성됨 → 부합/소폭 상회는 약한 신호.
- `novelty:true` + watchlist에 없던 사안 = 기대 밖 → 서프라이즈로 강하게.
- `sentiment.trend`가 이미 `improving`인데 추가 호재 = 한계효용 체감.

### C3. 강도 분해 — magnitude · breadth · credibility (정성적 결합)
| 차원 | 측정 | 우리 필드 |
|---|---|---|
| **magnitude** (사안 크기) | 산업 판도 vs 소소한 업데이트 | `weight` (development 중요도) |
| **breadth** (파급 폭) | 테마 전체 vs 단일 종목 | primary/related relation, §E |
| **credibility** (확실성) | 확정 vs 루머·미확인 | lead_lag, 소스, §G3 |

> 강도는 산술 곱이 아니라 **정성적으로 결합**한다 — **약한 고리 우선**(셋 중 하나라도 약하면 강도를 그 수준으로 끌어내린다). 미확인 루머(credibility 低)는 magnitude가 커도 강도를 깎는다. ("곱"처럼 숫자로 곱하지 말 것 — 가짜 정밀도.)

### C4. 비대칭 — 악재가 빠르고 깊다
악재(소송·사고·감배·가이던스 하향)는 호재보다 **즉시·과민** 반응하는 경향. 단 §C5 적용.

### C5. 불확실성 해소 = 부호 반전 가능
악재라도 **이미 알려진 리스크가 "확정·종결"**되면(소송 합의·제재 확정·악재 출회) 불확실성 프리미엄 해소로 **오히려 +**일 수 있다. ("악재인데 오른다" — §H4.)

---

## §D. 렌즈 3 — 시점·지속성 ("앞으로 반영될까, 언제까지?")

### D1. 정보확산 타임라인 (Gradual Diffusion)
정보는 즉시 완전반영되지 않고 **점진 확산**. 실증(공급망 read-through): 관련 종목 수익률은 공개 **~30일 전부터 반응→공개 +1일 피크→180일에 걸쳐 0으로 수렴**. → 우리 함의:
- `leading` 신호 = 확산 **초입**(반영 멀었음, 장기 모니터 = watchlist).
- `lagging` 확정 = 확산 **후기**(잔여 드리프트만, §B3).
- 효과는 시간이 지나면 **감쇠**(state의 `decay_recent`가 7일 윈도우로 이미 반영 — 오래된 development는 약하게). 단 7일 넘은 구조적 추세는 `recent_developments`에서 사라지고 `horizon_summary`에만 남으므로, 장기 추세는 horizon_summary로 읽는다.
- ⚠️ **KR 보정**: 위 30/180일 확산 곡선은 미국 시장 실증치다. 개인 비중이 높고 변동성이 큰 KR 시장은 반영이 더 빠르거나 과잉반응할 수 있으니 수치를 절대 기준으로 쓰지 말고 **상대적 시간축(선행<동행<후행) 판단에만** 사용한다.

### D2. time_to_impact 라벨
각 핵심 동인에 **반영 시점**을 붙인다: `immediate`(이미 진행) · `near`(수일~수주) · `mid`(분기~수개월) · `long`(수년·확률적, 주로 `leading`).
- `leading` 기초연구는 거의 `long` + 저확률. 점수엔 작게, watchlist엔 크게.

### D3. 지속성 vs 일회성
- **구조적 변화**(정책·증설·기술표준·점유율 이동) = 추세, 지속 → `horizon_summary`에 녹고 오래 간다.
- **일회성 이벤트**(단발 수주·일시 급등 담론) = 펄스, 빠르게 감쇠 → 점수에 일시 가중만.
- `sentiment.trend`(`improving`/`deteriorating`)와 `last_shift`로 추세의 방향·전환을 읽는다.

### D4. 내러티브 수명 (Hype Cycle)
담론(`coincident`) 주도 테마는 기대 곡선을 탄다: 촉발 → **과열 정점**(거품) → 환멸 → 재평가. `coincident` 급증 + `novelty:false` 반복 = 정점 부근 신호 → 추격 경계(§G2).

---

## §E. 렌즈 4 — 2차·3차 파급 ("그 다음엔 어떤 영향이?")

1차(직접)에서 멈추지 말 것. **"그래서 다음은?"**을 2~3홉 전개한다(Second-Order Thinking).

### E1. 공급망 전·후방 전파 (read-through)
한 종목/테마 이벤트는 **공급사·고객사**로 가치가 전파된다(실증). 이 종목에 매핑된 **related 테마들의 state**(이미 입력에 주입됨)와 각 테마의 **`edge_relation`**(primary 테마 기준 밸류체인 위치, §0-(3))를 경로로 사용한다 — themes.json 원본 엣지 그래프 전체가 아니라, **입력에 들어온 related 테마만** 본다.
- `edge_relation`: `upstream`(소재·부품) · `downstream`(수요·완성품) · `demand` · `application` 등. **부호는 §C1 종목입장으로 재판정**(이름만으로 부호 단정 금지).
- 예: primary=battery 종목에서 related ev(downstream)의 "EV 판매 호조" → 배터리 수요(+) read-through. 단 홉마다 **weight만큼 강도 감쇠**.
- related 테마 state가 입력에 없으면(매핑 안 됨) 그 경로는 보지 않는다(환각 금지).

### E2. 대체·잠식 (Substitution / Disruption)
`leading` 기초연구의 핵심 함의. 신기술이 **기존 제품을 대체**하면 현재 강자에겐 장기 악재.
- 예: 전고체·Mg이온·LFP 진전 = 기존 NCM 하이니켈 양극재 전문기업엔 **장기 포지셔닝 위협**(저확률·고임팩트). time_to_impact=`long`, 점수 소폭·risks/watchlist에 명시.

### E3. 경쟁 read-through (sympathy & 역방향)
- **Sympathy**: 동종 1社 호재 → 테마 전체 동반 기대(+). 단 §F로 이 종목 실제 수혜 검증.
- **역방향(제로섬)**: 경쟁사의 점유율·수주 획득 = 이 종목엔 (−). "테마 호재인데 우리 종목엔 악재".

### E4. 리플렉시비티 (Reflexivity — 내러티브↔펀더멘털 피드백)
> Soros: 가격/내러티브가 펀더멘털에 되먹임된다.
- 긍정 내러티브 → 주가↑ → 자금조달·증설 용이 → 실제 펀더멘털 개선 → 내러티브 강화(상방 나선). 역방향도 성립(하방 나선).
- 거품·붕괴의 씨앗. `sentiment`가 펀더멘털과 괴리되어 한 방향으로 가속하면 **반전 위험** 경고(§G2).

### E5. 2차 효과가 1차를 뒤집는 경우
- "보조금 확대(1차 호재)" → 과당경쟁·치킨게임 유발(2차) → 마진 붕괴(종목엔 악재). 1차 부호와 종합 부호가 다를 수 있음 — second_order에 명시.

---

## §F. 종목 귀속 (테마 신호 → 개별 종목 강도)

테마 state는 종목이 아니다. relation·weight로 **이 종목에 실제로 얼마나 꽂히는지** 변환한다.

### F1. relation 가중
- **primary**: full 반영(종목 정체성). 점수 주력.
- **related**: `weight`만큼만. themes.json 엣지 종류(upstream/downstream 등)로 §E 경로 해석.
- **macro**: **배경으로만**. 방향 결정 금지, 톤·리스크 조정에만(§F3).

### F2. 다대일 — 종목은 여러 테마 교차점
한 종목이 primary 1 + related 2~3에 걸침. 테마 간 신호가 **상충**하면(한쪽 호재·한쪽 악재) 상쇄하되 primary 우선, related는 weight 비례.

### F3. macro 배경 처리
거시(금리·환율·유가)는 모든 종목 공통이라 상대우위를 못 만든다. **점수 방향을 정하지 말고**, 전반 톤(위험선호/회피)·리스크 항목으로만. 단 환율 등 종목 수익구조에 직접 닿으면(수출주·원자재 수입주) related급으로 승격해 명시.

### F4. 신호 부재 = 중립 (공백을 악재로 오해 금지)
해당 테마에 최근 development가 없으면 **중립**(0)이다. "뉴스가 없다 = 악재"가 아니다. confidence를 낮추고 data gap을 명시(§I).

---

## §G. 위험·함정 체크리스트 (발견 시 강도·confidence 하향)

1. **선반영 무시** — 오래 기대된 호재를 새 호재로 오판(§B2). watchlist 장기체류 사안의 "확정"은 소진으로.
2. **하이프 정점 추격** — `coincident` 급증·`novelty:false` 반복 = 거품 정점 가능. 내러티브가 펀더멘털과 괴리(§E4)면 반전 경고.
3. **미확인 루머를 사실로** — 소스·`lead_lag`·복수출처 교차 없는 단발 주장은 credibility 하향(§C3). 루머는 루머로 태깅.
4. **테마 호재 = 종목 호재 오판** — §C1·§E3 역방향. 경쟁사 수혜를 우리 종목 호재로 착각 금지.
5. **leading 과대평가** — arXiv 한 편을 즉시 매수재료로. 상용화 시차·저확률(§D2 `long`) 반영, 점수 소폭.
6. **거시 과적용** — macro로 개별주 방향 결정(§F3 위반).
7. **확증편향** — `sentiment`가 한 방향이면 반대 신호를 무시. drivers와 risks를 **반드시 둘 다** 채운다.
8. **데이터 공백을 신호로** — 신호 부재를 호·악재로(§F4). 침묵은 중립.
9. **생존·선택 편향** — 수집 소스가 특정 관점(예: EV 낙관 매체)에 치우치면 톤 보정.
10. **오래된 신호 재계상** — decay 윈도우 밖 development를 새 것처럼. state의 `date` 확인.

---

## §I. 한계 & confidence 가이드 (정직성 — 추측 금지)

| 한계 | 영향 | 대응 |
|---|---|---|
| **가격·거래량 미관측** | "반영도"는 실제 주가반응이 아니라 **확산단계 추정**(§B) | priced_in은 추정임을 전제. 단정 금지. 교차융합은 🧠 Judge 담당 |
| **컨센서스·목표주가 미보유** | "기대 대비"(§C2)를 watchlist·sentiment 대용치로만 추정 | 서프라이즈 판정 confidence 하향 |
| **이벤트 예정일 부재** | time_to_impact는 카테고리 기반 추정 | near/mid/long은 근사 |
| **digest는 요약만** | 기사 뉘앙스·세부 누락 가능 | 단정 약화 |
| **소스 편향·커버리지 공백** | 특정 관점 과표집 가능(§G9) | 톤 보정, data gap 명시 |
| **신호 부재** | 중립(§F4), 악재 아님 | confidence 하향 |

> 데이터가 부족하면 **confidence를 낮추고 그 사실을 근거에 명시**. 미보유로 단정 금지.

---

## §J. 출력

이 문서는 **사전**이다. 무엇을 어떤 모양으로 내보낼지는 종합 분석 한 곳(`src/research/analyze/`)이 정한다 — 콜이 하나이므로 출력 계약도 하나여야 하고, 룰북마다 스키마를 두면 세 벌이 서로 어긋난다.

---

## §K. 출처 (Sources)

- 시장효율성·정보반영 (EMH) — [Efficient-market hypothesis (Wikipedia)](https://en.wikipedia.org/wiki/Efficient-market_hypothesis)
- PEAD·과소반응 드리프트 — [Post–earnings-announcement drift (Wikipedia)](https://en.wikipedia.org/wiki/Post%E2%80%93earnings-announcement_drift), [UCLA Anderson Review: Is PEAD a thing again?](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/)
- Buy the rumor, sell the news — [Forex.com](https://www.forex.com/en-ca/trading-guides/buy-the-rumor-sell-the-news/)
- 2차 사고(Second-Level Thinking) — [Howard Marks, Novel Investor](https://novelinvestor.com/howard-marks-importance-second-level-thinking/)
- 리플렉시비티(Reflexivity) — [Soros, Novel Investor](https://novelinvestor.com/reflexivity-explaining-bubbles/), [Alpha Architect: Reflexivity & feedback effect](https://alphaarchitect.com/reflexivity-and-the-feedback-effect-in-financial-markets/)
- Hard vs Soft 카탈리스트(이벤트드리븐) — [AnalystPrep CFA: Event-Driven Strategies](https://analystprep.com/study-notes/cfa-level-2/event-driven-strategies-merger-arbitrage/), [AllianceBernstein](https://www.alliancebernstein.com/us/en-us/investments/insights/investment-insights/beyond-mergers-a-diversified-approach-to-event-driven-investment.html)
- 내러티브 이코노믹스 / 하이프사이클 — [Gartner Hype Cycle (Wikipedia)](https://en.wikipedia.org/wiki/Gartner_hype_cycle), [Equity Toolkit: Hype Cycle as investment tool](https://www.equitytoolkit.com/gartner-hype-cycle-investment-tool/)
- 정보확산·공급망 read-through — [MIT: Gradual incorporation of information into stock prices](https://economics.mit.edu/sites/default/files/publications/informjcf.pdf), [Market reaction to news in supply chain networks (arXiv)](https://arxiv.org/html/2409.06255)

---

## §L. 변경 이력
- 2026-05-27 — 초안. 7개 검증 프레임워크(EMH·PEAD·buy-rumor-sell-news·2차사고·리플렉시비티·hard/soft 카탈리스트·내러티브/하이프사이클·정보확산) 다중출처 리서치 → 우리 보유 정성 필드(holding_themes·테마 state·lead_lag) 매핑. 사용자 4렌즈(반영도§B·방향§C·시점§D·2차파급§E)를 척추로, 12개 dense 케이스(§H) + 한계명시(§I) + 출력스키마(§J).
- 2026-10-15 — 낡은 설명만 고침(규칙 내용은 그대로): 옛 경로 `src/agents/…` → `src/research/…`, 도메인별 "판단 에이전트" 문구 → 종합 분석 한 콜의 사전, 옛 표 이름 `holding_themes` → `symbol_themes`. 도메인별 판정은 2026-09-01에 폐기됐다.
