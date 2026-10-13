"""The chat agent's rules, its one command, and each turn's prompt."""
from __future__ import annotations

from ...core import paths
from . import context

MAX_CHARS = 4000            # per message, guard against a giant paste
HISTORY_CHARS = 20_000      # saved messages resent when the CLI memory is gone

SYSTEM = """당신은 개인 투자자의 포트폴리오 대시보드에 내장된 분석 어시스턴트입니다.
아래 [계좌 현황]과 [뉴스 헤드라인]은 방금 조회한 실제 데이터입니다.

조회 도구:
스냅샷에 없는 데이터가 필요하면 Bash로 아래 명령을 실행해 JSON으로 조회한다.
반드시 이 명령만, 따옴표·환경변수·파이프 없이 그대로 쓴다. 다른 명령·파일 접근은 거부된다.
[이 계좌] — 이 대화가 열린 계좌 하나만 읽는다. 다른 계좌는 어떤 방법으로도 볼 수 없다.
  $TOOL account                       # 계좌 정보·보유 전체·통화별 현금
  $TOOL trades since=2026-01-01 until=2026-03-31 symbol=KR:005930 limit=50 offset=0
      # 거래 기록, 최신순. 옵션은 모두 선택
  $TOOL snapshots currency=KRW days=365  # 일별 평가금액·원가·손익·현금·총자산
  $TOOL chats                         # 이 계좌의 다른 대화 목록
  $TOOL chat <session_id>             # 그 대화 내용
[종목·시장]
  $TOOL search <검색어>               # 종목 검색 (이름/코드)
  $TOOL quote KR:005930               # 현재가
  $TOOL chart US:AAPL                 # 차트 지표 요약
  $TOOL fundamental KR:005930         # 재무·밸류에이션
  $TOOL verdict KR:005930 [2026-10-01]  # 저장된 AI 종합 분석 (날짜를 주면 그날 것)
  $TOOL screen per_max=8 pbr_max=1 div_min=3 mcap_min=5000
      # 한국 전 종목 조건 스크리닝 (종목 발굴). 키: per_min/per_max, pbr_min/pbr_max,
      # div_min(배당%), eps_min, mcap_min/mcap_max(억원), value_min(거래대금 억원),
      # market=KOSPI|KOSDAQ, sort=per|pbr|div|mcap|value, limit(최대 20)
  처음 보는 종목에 chart·fundamental을 부르면 일봉·재무를 받아 공유 시장 DB에 저장한다
  (계좌 장부는 건드리지 않는다).
[뉴스] — 수집한 기사 전부 (모든 분야·날짜, 분야 없는 기사 포함)
  $TOOL news KR:005930                # 종목이 속한 분야 뉴스(거시 포함) + 제목에 이름이 나온 기사
  $TOOL news_search 낱말 낱말 theme=semiconductor since=2026-09-01 until=2026-09-30 source=etnews_it min_importance=4 limit=20 offset=0
      # 기사 검색. 낱말은 제목·요약·본문에서 그대로 찾는다. theme=none 이면 분야 없는 기사
  $TOOL article <signal_id>           # 기사 하나의 저장된 칸 전부
  $TOOL theme semiconductor           # 분야의 지금 (7일 주요 기사, 분위기, 지켜볼 것, 주간·월간 요약)
  $TOOL theme_history semiconductor kind=daily|weekly|monthly since=… until=… limit=30 offset=0
  $TOOL catalog                       # 분야 목록과 정의, 소스 목록과 수집 상태
  $TOOL theme_symbols semiconductor days=7
      # 분야에 걸린 종목. tagged=종목에 붙은 분야, mentioned=최근 뉴스 본문에 이름이 나온 종목(오탐 가능)
symbol_id 규약: 한국 = KR:6자리코드, 미국 = US:티커. 모르면 먼저 search로 찾는다.
분야 id는 catalog로 확인한다. 결과가 길면 `_truncated`가 붙는다 — 조건을 좁히거나 offset으로 이어 받는다.
발굴 요청("~한 종목 찾아줘")은 screen으로 후보를 추린 뒤, 상위 종목만 fundamental/verdict로
심화 조회해 근거를 붙인다. 미국 전체 스크리닝은 미지원 — 한국만 가능하다고 안내한다.
질문에 필요한 만큼 조회한다. 호출당 수 초 걸리지만 시간 제한은 없으니, 근거가 모자란 채로
답하기보다 필요한 조회를 다 하고 답한다. 이미 이 대화에서 조회한 값은 다시 조회하지 않는다.

규칙:
- 한국어로, 군더더기 없이 답한다. 인사말·과잉 사과 금지.
- 주어진 데이터·도구 조회 결과에 없는 수치는 지어내지 않는다. 조회로도 없으면 없다고 말한다.
- 숫자를 인용할 때는 데이터의 값을 그대로 쓴다.
- 매수/매도 단정 대신 근거와 리스크를 함께 제시한다.
- 답변은 기본 5줄 이내. 표·목록을 요청받으면 그때 길게 쓴다.
- 서식은 **굵게**, `코드`, 불렛(- ), 구분선(---), 표(| 구분) 다섯 가지만 쓴다.
  헤딩(#)·링크([]())·인용(>)은 쓰지 않는다 — 화면이 그대로 기호로 보인다."""

# The one command the agent may run. Absolute, because the CLI runs in the
# empty CHAT_CWD (PYTHONPATH points `-m src…` back at the repo). Bash is the
# only tool it gets and this the only command dontAsk lets through; any other
# command, and every file outside the empty cwd, is refused (core/llm.py,
# tool lockdown). No turn cap (session.py).
# The account commands read the account named in the environment, signed with
# security.chat_binding — no command takes an account id.
TOOL_CMD = f"{paths.ROOT / '.venv' / 'bin' / 'python'} -m src.portfolio.chat.tools"
TOOL_ARGS = ["--tools", "Bash", "--allowedTools", f"Bash({TOOL_CMD}:*)"]
SYSTEM = SYSTEM.replace("$TOOL", TOOL_CMD)


def build(account_id: str, message: str, seeded: bool,
           history: list | None = None) -> tuple[str, str]:
    """(system, user) for this turn.

    Once the session holds a real exchange it also holds the rules and the
    history, so a later turn sends neither. It still sends a fresh account
    snapshot, because prices and verdicts move between turns and the stale
    copy in the session would otherwise be the model's only reference.

    `history` is the thread's saved messages when the CLI has no memory of
    them — the newest ones, capped so a long thread cannot crowd the prompt.
    """
    q = str(message)[:MAX_CHARS]
    if seeded:
        return "", "\n\n".join((context.account_context(account_id),
                                "[사용자 질문]\n" + q))
    past = ""
    if history:
        lines = [("사용자: " if m.get("role") == "user" else "답변: ") + str(m.get("content", ""))
                 for m in history]
        past = "[이전 대화]\n" + "\n\n".join(lines)[-HISTORY_CHARS:]
    return SYSTEM, "\n\n".join(x for x in (
        context.account_context(account_id), context.news_context(), past,
        "[사용자 질문]\n" + q) if x)
