"""account.html invariants (ui-open-decisions B1/B3/B7/B9, C1/C2), enforced
from source like test_ui_invariants.py: tokens instead of px literals,
mobile-first breakpoints, one button family, a11y floor, and the two
behaviour decisions on the trade flow."""
import re
from html.parser import HTMLParser
from pathlib import Path

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
ACCOUNT = STATIC / "account.html"
SRC = ACCOUNT.read_text()
LINES = SRC.splitlines()


def _hits(pattern, exempt=None):
    return [f"account.html:{i}: {line.strip()}"
            for i, line in enumerate(LINES, 1)
            if re.search(pattern, line) and not (exempt and exempt in line)]


# ── B1 tokens ────────────────────────────────────────────────────────────
def test_no_font_size_literals():
    """Every font-size is a --text-* / --h* token. The one exception is the
    decorative Press Start 2P price in the expand header, marked `decorative`
    on its line."""
    bad = _hits(r"font-size:\s*\d", exempt="decorative")
    assert not bad, "font-size px literals:\n" + "\n".join(bad)


SPACING_LITERAL_CAP = 175   # off-scale values (2/3/6/10/14/18px…) that stay


def test_spacing_literals_bounded():
    """padding/margin/gap values on the 4px scale are --space-n; the ones
    that stay are off-scale and their count must not grow."""
    on_scale = re.compile(r"(?:padding|margin|gap)[a-z-]*\s*:\s*[^;'\"}]*?(?<![-\d.])\b(?:4|8|12|16|24|32)px")
    bad = [f"account.html:{i}: {l.strip()}" for i, l in enumerate(LINES, 1) if on_scale.search(l)]
    assert not bad, "on-scale spacing literals:\n" + "\n".join(bad)
    n = len(re.findall(r"(?:padding|margin|gap)[a-z-]*\s*:\s*[^;'\"}]*\d+px", SRC))
    assert n <= SPACING_LITERAL_CAP, f"{n} spacing literals (cap {SPACING_LITERAL_CAP})"


# ── B3 mobile-first ───────────────────────────────────────────────────────
def test_mobile_first_min_width_only():
    assert not _hits(r"@media[^{]*max-width"), "narrow is the base; widen with min-width"
    bps = set(re.findall(r"@media[^{]*?(\d+)px", SRC))
    assert bps <= {"600", "900", "1100"}, bps
    # the expand modal's three columns only exist from 900px up
    m = re.search(r"@media \(min-width: 900px\)[^@]*?\.expand-body\s*\{[^}]*grid-template-columns:\s*minmax", SRC)
    assert m, "expand-body 3-column grid must sit inside the 900px query"


# ── B9 buttons ────────────────────────────────────────────────────────────
BTN = {"btn", "btn-primary", "btn-danger", "btn-sm"}
# toggles / tabs / chips are not action buttons: they carry state classes only
TOGGLE = {"active", "on", "fb-chip", "fb-toggle"}
# one layout modifier (shared.css): the vertical analysis button in the card
LAYOUT = {"sc-action-analysis"}
GONE = ("tb-btn", "curve-btn", "sc-expand-btn", "sc-delete-btn", "sc-edit-btn",
        "more-btn", "modal-close", "sc-action-buy", "sc-action-sell", "fa-dl",
        "chat-plan-save", ".expand-rerun", "remove_when_flat", "confirmFullSellout")


def test_button_classes_are_the_btn_family():
    bad = []
    for i, line in enumerate(LINES, 1):
        for m in re.finditer(r"<button[^>]*\sclass=\"([^\"]*)\"", line):
            cls = set(m.group(1).split())
            if not cls <= BTN | TOGGLE | LAYOUT:
                bad.append(f"account.html:{i}: {m.group(0)}")
        for m in re.finditer(r"el\('button',\s*\{[^}]*?className:\s*'([^']*)'", line):
            cls = set(m.group(1).split())
            if not cls <= BTN | TOGGLE | LAYOUT:
                bad.append(f"account.html:{i}: {line.strip()}")
    assert not bad, "buttons outside the .btn family:\n" + "\n".join(bad)
    for word in GONE:
        assert word not in SRC, word
    assert "S.LABELS.delete" in SRC


# ── B4 CSP hygiene ────────────────────────────────────────────────────────
def test_no_inline_style_or_handler_attributes():
    assert not _hits(r'(?<![\w-])style="'), "inline style attributes (use classes or el() style:)"
    assert not _hits(r"\son[a-z]+=\""), "inline on*= handlers"
    assert 'href="data:image/svg+xml' not in SRC, "server injects the PNG icons"


# ── B7 a11y ───────────────────────────────────────────────────────────────
def test_skip_link_and_heading_hierarchy():
    assert '<a class="skip-link" href="#main">' in SRC
    assert 'id="main"' in SRC
    assert len(re.findall(r"<h1\b", SRC)) == 1
    assert len(re.findall(r"<h2\b", SRC)) >= 2


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_label = 0
        self.labelled_ids = set()
        self.fields = []   # (line, id, aria, wrapped)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.labelled_ids.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.fields.append((self.getpos()[0], a.get("id"), a.get("aria-label"), self.in_label > 0))

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1


def test_static_inputs_have_labels():
    p = _Inputs()
    p.feed(SRC.split("<script>")[0])
    bad = [f"line {ln}: #{i}" for ln, i, aria, wrapped in p.fields
           if not (aria or wrapped or i in p.labelled_ids)]
    assert not bad, "inputs without a label:\n" + "\n".join(bad)
    # dynamic ones: the PIN gate, the new-account form, the trade filters
    assert "'aria-label': '6자리 비밀번호'" in SRC
    assert re.search(r"el\('label',\s*\{\s*for:", SRC), "new-account field() labels by for="


def test_modals_trap_focus_and_release():
    n_dialogs = len(re.findall(r"role=\"dialog\"|role: 'dialog'", SRC))
    assert n_dialogs >= 9, n_dialogs
    assert len(re.findall(r"S\.trapFocus\(", SRC)) >= n_dialogs
    assert "release()" in SRC or "_release()" in SRC


def test_pollers_respect_visibility():
    clock = SRC[SRC.index("// CLOCK (KST)"):SRC.index("// LIVE DATA")]
    assert "S.whenVisible(" in clock and "setInterval(" not in clock
    poll = SRC[SRC.index("async function pollAnalysisJob"):SRC.index("function makeJobProgress")]
    assert "document.hidden" in poll and "60000" in poll, "hidden tab: 60s stride, never stop"


# ── C1 / C2 ───────────────────────────────────────────────────────────────
def test_full_sell_confirms_then_deletes_the_card():
    body = SRC[SRC.index("async function submit() {\n    if (!currentStock) return;"):]
    body = body[:body.index("// edit — a direct correction")]
    assert "S.confirmModal(" in body and "danger: true" in body
    assert re.search(r"holding\s*&&\s*d\.holding\.removed", body)


def test_trade_history_has_symbol_and_date_filters():
    fn = SRC[SRC.index("window.openTradeHistory"):SRC.index("// PLAN NOTE MODAL")]
    assert fn.count("type: 'date'") == 2
    assert "since" in fn and "until" in fn and "symbol_id" in fn
    assert "el('select'" in fn
    assert "S.trapFocus(" in fn
    assert re.search(r"trades:\s*\(q\)", SRC), "API.trades takes the filter query"


# ── 계좌 배지 · 계좌×종목 모달 ──────────────────────────────────────────

def test_plan_badge_is_filled_in_place_from_one_request():
    """22 cards, one /plan_runs/latest. A badge that fetched per symbol would
    be 22 requests, and rebuilding the card to fill it loses scroll and the
    drawn chart (the in-place rule)."""
    assert "window._planBadgeEls[stock.symbolId] = planBadge" in SRC
    assert len(re.findall(r"/plan_runs/latest'", SRC)) == 1
    loader = SRC[SRC.index("async function loadPlanBadges"):]
    loader = loader[:loader.index("\n}\n")]
    assert "_setPlanBadge" in loader and "renderCard" not in loader


def test_the_plan_badge_is_reachable_without_a_mouse():
    badge = SRC[SRC.index("const planBadge = el("):]
    badge = badge[:badge.index("window._planBadgeEls")]
    assert "role: 'button'" in badge and "tabindex: '0'" in badge
    assert "'Enter'" in badge and "' '" in badge
    assert "aria-label" in SRC[SRC.index("function _planBadgeApply"):
                               SRC.index("window._setPlanBadge")]


def test_a_proposal_is_approved_or_rejected_by_patching_the_plan():
    """No second write path: approval is the same PATCH the plan note uses,
    and a rejection asks first."""
    fn = SRC[SRC.index("async function decide(plan, status)"):]
    fn = fn[:fn.index("\n  function planRow")]
    assert "API.plan(plan.plan_id)" in fn and "'PATCH'" in fn
    assert "S.confirmModal" in fn and "'armed'" not in fn.split("decide(plan")[0]
    assert "S.LABELS.approve" in SRC and "S.LABELS.reject" in SRC


def test_the_preview_is_a_job_not_a_blocking_call():
    """A synchronous plan pass would die at Cloudflare's 100s ceiling. The
    poller slows in a hidden tab and stops for good when the modal closes —
    S.whenVisible cannot express the second half (its stop is undone by the
    next visibilitychange)."""
    assert "API.runPlan()" in SRC and "API.planRunStatus()" in SRC
    poll = SRC[SRC.index("  async function poll(stock, btn, state)"):]
    poll = poll[:poll.index("\n  function render(stock)")]
    assert "document.hidden" in poll and "60000" in poll
    assert "S.whenVisible(" not in poll
    assert "mine !== pollToken" in poll


def test_the_account_modal_does_not_dress_a_preview_as_the_book():
    assert "장부에 적용되지 않음" in SRC


def test_plan_stance_dictionary_is_one_place():
    assert len(re.findall(r"const PLAN_STANCE_KO = ", SRC)) == 1
    for word in ("유지", "추가", "축소", "정리", "관심"):
        assert word in SRC[SRC.index("const PLAN_STANCE_KO"):
                           SRC.index("window._planBadgeEls")]
