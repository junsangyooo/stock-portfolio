"""The single analysis call — prompt construction and the output contract.

One call sees all three domains. Splitting it into a chart agent, a
fundamental agent and a news agent and then merging their verdicts sounds
thorough but is strictly worse: each would judge blind to the other two, and
by the time the merge happens the conclusions have already hardened
one-dimensionally. Valuation only means something next to the news that
explains it; a breakout only means something next to the earnings that caused
it. The information has to meet before the judgment, not after.

There is no rulebook here, deliberately. The domain documents injected as
`reference` are dictionaries — what each field means, when each indicator
lies, which formula we use, what we could not compute. What replaced the
prescriptive half is *structure*: the model must separate what it observed
from what it inferred, and must argue against its own conclusion before
stating it. That opens reasoning where a rulebook narrowed it.
"""
from __future__ import annotations

import json

SYSTEM = (
    "너는 한 종목을 다각도로 뜯어보는 분석가다. 차트·재무·뉴스를 동시에 보고 "
    "지금 이 종목이 어떤 상태인지, 앞으로 어떻게 전개될 수 있는지 판단한다.\n"
    "\n"
    "매매 판단은 네 일이 아니다. 무엇을 사고 팔지, 얼마나, 언제는 다른 단계가 "
    "정하고 그쪽만이 누가 무엇을 얼마에 들고 있는지 안다. 너는 종목 자체를 "
    "본다 — 보유 여부를 짐작해 조언을 끼워 넣지 마라.\n"
    "\n"
    "지켜야 할 것:\n"
    "1) 인풋에 없는 숫자를 쓰지 마라. 모든 수치는 주어진 데이터에서 인용한다.\n"
    "2) 관찰과 해석을 섞지 마라. 데이터가 말하는 것과 네가 읽어낸 것은 다르다.\n"
    "3) 결론을 내기 전에 반드시 반대 논거를 세워라. 반박이 비어 있으면 그 결론은 "
    "   검토되지 않은 것이다.\n"
    "4) 결손 데이터는 신뢰도를 낮출 뿐, 부정 신호가 아니다. 없는 것을 나쁜 것으로 "
    "   읽지 마라.\n"
    "5) 모르면 모른다고 써라. 그럴듯한 추측보다 명시된 공백이 낫다.\n"
    "6) 사람이 읽을 문장은 전부 쉬운 한국어로. 전문용어를 쓰면 그 자리에서 풀어라.\n"
    "\n"
    "출력은 JSON 하나만. 설명도 코드펜스도 붙이지 마라."
)

_SCHEMA = """{
  "observations": [
    {"domain": "chart|fundamental|news|peer",
     "fact_ko": "데이터가 말하는 것 하나. 해석 금지, 수치 포함. 80자 이내",
     "cite": "timeframes.1d.rsi14"}   // 필드 경로만. 문장 금지
  ],                                    // 10~14개. 도메인마다 최소 2개
  "readings": [
    {"claim_ko": "관찰에서 읽어낸 것. 90자 이내",
     "from": [0, 3],
     "confidence": 0.0}
  ],                                    // 4~7개
  "counter": {
    "case_ko": "내 결론이 틀렸다면 그 이유. 가장 강한 반대 논거. 250자 이내",
    "what_would_show_it": ["반증이 사실로 드러나는 신호"]   // 최대 3개
  },
  "verdict": {
    "score": 0.0,
    "label": "strong_sell|sell|neutral|buy|strong_buy",
    "confidence": 0.0,
    "summary_ko": "비전문가용 2~3문장 결론. 200자 이내",
    "drivers": ["긍정 동인"],                 // 최대 3개, 각 40자 이내
    "risks": ["위험"],                       // 최대 3개, 각 40자 이내
    "domain_view": {
      "chart":       {"one_line_ko": "", "lean": "bullish|neutral|bearish"},
      "fundamental": {"one_line_ko": "", "lean": "bullish|neutral|bearish"},
      "news":        {"one_line_ko": "", "lean": "bullish|neutral|bearish"}
    },
    "disagreement_ko": null
  },
  "outlook": {
    "horizon": "immediate|near|mid|long",
    "base_ko": "가장 그럴듯한 전개. 150자 이내",
    "scenarios": [
      {"case": "bull|base|bear",
       "narrative_ko": "이렇게 전개된다면. 70자 이내",
       "confirm": ["맞다는 신호"],
       "invalidate": ["틀렸다는 신호"]}
    ]
  },
  "levels": {
    "current": 0,
    "support":    [{"price": 0, "basis_ko": "근거 50자 이내", "strength": "weak|medium|strong"}],  // 최대 3
    "resistance": [{"price": 0, "basis_ko": "", "strength": ""}],                        // 최대 3
    "entry_zone":   {"low": 0, "high": 0, "rationale_ko": "60자 이내"},
    "invalidation": {"price": 0, "rationale_ko": "논지가 깨지는 이유. 60자 이내"},
    "targets":    [{"price": 0, "basis_ko": ""}]        // 최대 2
  },
  "reassess_when": ["이런 일이 생기면 이 분석은 다시 해야 한다"],   // 최대 4개
  "limitations": ["못 본 것·근사한 것을 솔직하게"]   // 최대 3개
}"""

_GUIDE_KO = """
각 칸에 대해:

- **observations** — 데이터가 말하는 것만. "RSI 71" 은 관찰, "과열이다" 는 해석.
  **10~14개**로 추린다. 도메인마다 최소 2개(peer 도 도메인이다).
  전부 나열하는 자리가 아니라 **결론에 실제로 쓰인 것**만 남기는 자리다.
- **readings** — 관찰에서 읽어낸 것 **4~7개**. `from` 에 근거가 된 observations
  인덱스를 적는다. 근거를 못 가리키면 그건 관찰한 게 아니라 떠올린 것이다.
- **counter** — 성의껏 써라. "약간의 위험은 있다" 같은 형식적 반박은 반박이 아니다.
  네 결론을 진짜로 무너뜨릴 논거를 찾아라.
- **verdict.score** — -1(강한 약세) ~ +1(강한 강세). 라벨과 어긋나지 않게.
  confidence 는 확신이 아니라 **근거가 서로 얼마나 맞물리는지**다. 도메인이
  엇갈리거나 데이터가 비면 낮춘다.
- **outlook** — 확률은 쓰지 마라. 대신 각 시나리오에 **무엇을 보면 맞다고 알 수
  있는지**(confirm)와 **무엇을 보면 틀렸는지**(invalidate)를 적는다. 가능하면
  가격·지표·이벤트처럼 나중에 확인 가능한 형태로.
- **levels** — 다음 단계(매매 계획)가 그대로 집어갈 좌표다. 가격은 인풋의
  지지·저항·피보·피봇에서 가져오고, 지어내지 마라. 차트 데이터가 없으면
  전부 null 로 두고 limitations 에 이유를 적는다.
  `invalidation` 은 손절의 근거가 되는 선이다 — 여기가 비면 계획을 세울 수 없다.
- **reassess_when** — 이 분석의 유통기한. "실적 발표", "60,000원 이탈" 처럼.

문장은 짧게. 같은 말을 두 칸에 나눠 쓰지 마라 — observations 에 쓴 수치를
readings 에서 반복하지 말고 인덱스로 가리켜라. 확신은 근거만큼만.

- **분량도 계약이다.** 각 칸에 적힌 글자수·개수 상한을 지켜라. 넘기면 넘긴 만큼
  잘려서 저장되므로 문장이 중간에서 끊긴다.
- **`cite` 는 설명이 아니라 필드 경로다.** `timeframes.1d.rsi14`, `structure.pivots.S1`,
  `financials_series[0].revenue` 처럼 적어라. 한국어 문장으로 쓰지 마라.
- 줄일 것은 **문장의 길이**이지 관찰·해석의 **개수**가 아니다. 근거를 덜 대지 마라.
"""


def _reference_block(d: dict) -> str:
    """One domain's dictionary — byte-identical for every symbol."""
    return (f"━━━━━ 해석 기준(사전): {d['title_ko']}  [id={d['id']}] ━━━━━\n"
            f"{d['reference']}\n")


def _data_block(d: dict) -> str:
    head = f"━━━━━ 데이터: {d['title_ko']}  [id={d['id']}] [{d['status']}] ━━━━━"
    if d["note_ko"]:
        head += f"\n※ {d['note_ko']}"
    if d["status"] == "missing" or d["data"] is None:
        return f"{head}\n(데이터 없음 — 이 도메인은 판단에서 제외하고 신뢰도에만 반영)\n"
    return f"{head}\n{json.dumps(d['data'], ensure_ascii=False, default=str)}\n"


def build(envelope: dict) -> str:
    """The prompt, invariant half first.

    The three dictionaries plus the output contract are ~38,000 characters
    that are the same for every symbol on every day; only the symbol's own
    data changes. They lead so that the two halves can be split across a
    session boundary — dictionaries once in a root session, one symbol per
    fork — which is what actually turns them into a cache hit.

    Measured 2026-09-15, and worth writing down because the obvious guess is
    wrong: reordering alone changes nothing. `claude -p` caches the user
    message as ONE block, so two prompts that differ anywhere — even in the
    last line — share no cache at all (identical prompt: 50,783 tokens read,
    $0.036; last line changed: 30,861 created, $0.154).

    The pairing that ordering costs is restored by `id=`: each dictionary and
    its data block carry the same domain id, and the header says to read one
    through the other.

    A domain with no data still ships no dictionary — there is nothing to read
    through it, and 11,000 characters of reference for an absent domain is the
    same waste in a different place.
    """
    parts = [
        "이 프롬프트는 세 부분이다: ① 도메인별 해석 기준(사전) ② 출력 계약 "
        "③ 이 종목의 데이터.",
        "①과 ②는 어느 종목이든 같다. 각 데이터 블록은 같은 `id`를 가진 사전으로 "
        "읽어라.",
        "",
    ]
    parts += [_reference_block(d) for d in envelope["domains"]
              if d["status"] != "missing" and d["data"] is not None]
    parts += _contract()
    return "\n".join(parts + ["", symbol_part(envelope)])


def _contract() -> list[str]:
    return [
        "━━━━━ 작성 순서 ━━━━━",
        "① 관찰 → ② 해석 → ③ 반증 → ④ 결론 → ⑤ 전망 → ⑥ 좌표.",
        "이 순서로 생각하고, 아래 JSON 한 덩이로만 답한다.",
        "",
        _SCHEMA,
        _GUIDE_KO,
    ]


def symbol_part(envelope: dict) -> str:
    """Only what changes: this symbol's header, its data, its peer position.

    Sent on its own it is a fork's whole message — the dictionaries and the
    contract are already in the session it forked from.
    """
    sym = envelope["symbol"]
    parts = [
        "━━━━━ 여기부터가 이 종목이다 ━━━━━",
        f"종목: {sym['name']} ({sym['symbol_id']})",
        f"기준일: {envelope['asof']}",
        "",
    ]
    parts += [_data_block(d) for d in envelope["domains"]]
    ctx = envelope.get("context") or {}
    parts += [
        "━━━━━ 도메인 밖 맥락 ━━━━━",
        "어느 사전에도 속하지 않는다 — 동종 대비 위치다.",
        "",
        f"[동종 백분위]\n{json.dumps(ctx.get('peer'), ensure_ascii=False, default=str)}",
        "",
        "위 순서(① 관찰 → ⑥ 좌표)로 생각하고, 계약된 JSON 한 덩이로만 답하라.",
    ]
    return "\n".join(parts)


def root_context(references: list[dict]) -> str:
    """The half a whole batch shares — every dictionary, plus the contract.

    Sent once into a session that each symbol then forks, so it must be
    byte-identical across the batch: that is the only thing a prompt cache can
    hold (measured — a message that differs anywhere shares nothing). It
    therefore carries all three dictionaries even for a batch whose symbols
    are missing a domain; the fork's own data block says what is missing.
    """
    parts = [
        "이 세션은 종목 분석용 참고 자료다. 아래는 도메인별 해석 기준(사전)과 "
        "출력 계약이고, **분석할 종목의 데이터는 다음 메시지로 온다.**",
        "각 데이터 블록은 같은 `id`를 가진 사전으로 읽어라.",
        "",
    ]
    parts += [_reference_block(d) for d in references]
    parts += _contract()
    parts += ["", "지금은 아무것도 분석하지 마라. `준비됨` 한 단어만 답하라."]
    return "\n".join(parts)
