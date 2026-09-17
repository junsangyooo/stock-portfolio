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
    fn = SRC[SRC.index("window.openTradeHistory"):SRC.index("// ASSET CURVE MODAL (F) — line chart")]
    assert fn.count("type: 'date'") == 2
    assert "since" in fn and "until" in fn and "symbol_id" in fn
    assert "el('select'" in fn
    assert "S.trapFocus(" in fn
    assert re.search(r"trades:\s*\(q\)", SRC), "API.trades takes the filter query"


# ── 사람 책에는 계획이 없다 · 좌표 한 줄 ────────────────────────────────

def test_the_human_book_has_no_plan_ui():
    """Plans left the portfolio book: no plan routes, no ```plan hand-off
    from the chat, no plan picker on the trade form."""
    for gone in ("/plans", "/plan_runs", "```plan", "PLAN_FENCE", "plan_id",
                 "plan_candidates", "_planBadge", "openPlanNote", "plan-symbol-modal",
                 "sc-plan-badge", "S.LABELS.approve", "S.LABELS.reject"):
        assert gone not in SRC, gone


def test_coord_line_is_one_deterministic_helper():
    fn = SRC[SRC.index("function coordLineText("):]
    fn = fn[:fn.index("\n}\n")]
    assert "'보유 기준'" in fn and "'진입 기준'" in fn and "'진입구간 '" in fn
    assert "'손절 '" in fn and "'목표 '" in fn
    assert "c.stop == null && c.target == null)) return ''" in fn
    assert "S.api(" not in fn and "fetch(" not in fn


def test_card_coord_line_fills_in_place_from_the_summary_batch():
    """Same /api/analysis/summary response as the verdict badge — no second
    request, no card rebuild; hidden when there is nothing to say or the
    analysis is not an ok verdict."""
    assert "window._coordLineEls[stock.symbolId] = coordLine" in SRC
    loader = SRC[SRC.index("async function loadAnalysisBadges"):]
    loader = loader[:loader.index("\n}\n")]
    assert loader.count("S.api(") == 1
    assert "coordLineText(" in loader and "entry.coords" in loader
    assert "entry.status === 'ok'" in loader
    assert "line.hidden = !line.textContent" in loader and "renderCard" not in loader


def test_modal_coord_line_sits_next_to_the_hint():
    fn = SRC[SRC.index("function renderAnalysis(pane, body, date)"):]
    fn = fn[:fn.index("// ── 자세히 보기")]
    assert "const coordText = factsOnly ? '' : coordLineText(held," in fn
    assert "lz.invalidation" in fn and "lz.targets" in fn and "lz.entry_zone" in fn
    assert "coordText ? el('div', { className: 'jd-hint' }, [coordText])" in fn


# ── modal bodies scroll ──────────────────────────────────────────────────
def _css_block(name: str) -> str:
    """Declarations of the first `.name { … }` rule."""
    m = re.search(r"^\." + re.escape(name) + r"\s*\{([^}]*)\}", SRC, re.M)
    return m.group(1) if m else ""


def test_every_modal_body_can_scroll():
    """A dialog is a flex column with `overflow: hidden`, so its body has to
    be able to shrink (`min-height: 0`) and scroll. `.plan-body` had neither
    and `.curve-body` had only the second half, so both lists simply grew past
    the bottom of the screen with no way to reach them. Read per element, not
    per class: a body that is a grid is exempt because its panes scroll on
    their own, and that grid may come from a second class on the same tag."""
    bad = []
    for attr in re.findall(r'class="([^"]*)"', SRC):
        names = attr.split()
        if not any(n.endswith("-body") for n in names):
            continue
        css = " ".join(_css_block(n) for n in names)
        if not css.strip() or "display: grid" in css:
            continue
        if "overflow-y: auto" not in css or "min-height: 0" not in css:
            bad.append(f'class="{attr}" — {" ".join(css.split())[:90]}')
    assert not bad, ("modal body that cannot scroll:\n" + "\n".join(bad)
                     + "\n(needs `flex: 1; min-height: 0; overflow-y: auto`)")


def test_a_scrolling_flex_item_can_shrink():
    """`flex: 1` + `overflow-y: auto` without `min-height: 0` does not scroll:
    a flex item refuses to shrink below its content, so the pane grows past
    its parent and the overflow rule never fires. Four places had it — the
    plan note, the asset curve, the chat log and the theme panel."""
    bad = []
    for sel, body in re.findall(r"^(\.[^{\n]+)\{([^}]*)\}", SRC, re.M):
        css = " ".join(body.split())
        if ("overflow-y: auto" in css and "flex: 1" in css
                and "min-height: 0" not in css):
            bad.append(f"{sel.strip()} — {css[:90]}")
    assert not bad, ("scrollable flex item that cannot shrink:\n"
                     + "\n".join(bad) + "\n(add `min-height: 0`)")
