"""landing.html on the token layer (ui-open-decisions B1/B3/B7/B9): no size
literals, mobile-first at the three canonical breakpoints, a11y skeleton,
one button family, CSP-clean markup. Static checks on the source file."""
import re
from pathlib import Path

from src.dashboard_web import server

LANDING = Path(server.__file__).resolve().parent / "static" / "landing.html"
SRC = LANDING.read_text()

# padding/margin/gap (and their -x longhands) whose value carries a px number,
# in CSS and in el() `style:` strings alike
_SPACING = re.compile(r"\b(?:padding|margin|gap)(?:-[a-z]+)?\s*:\s*[^;\"'`]*?\d+px")

# 1px/2px hairline offsets below the scale's floor (space-1 = 4px)
SPACING_LITERALS_CAP = 2


def test_no_font_size_literals():
    bad = [m.group(0) for m in re.finditer(r"font-size:\s*\d+px", SRC)]
    assert not bad, f"font-size literals — use --text-*/--h*: {bad}"


def test_spacing_literals_bounded():
    hits = [f"{i}: {l.strip()}" for i, l in enumerate(SRC.splitlines(), 1) if _SPACING.search(l)]
    assert len(hits) <= SPACING_LITERALS_CAP, "px spacing literals:\n" + "\n".join(hits)


def test_mobile_first_breakpoints():
    queries = re.findall(r"@media[^{]*", SRC)
    assert queries, "the page has responsive rules"
    for q in queries:
        assert "max-width" not in q, q
        for px in re.findall(r"(\d+)px", q):
            assert px in {"600", "900", "1100"}, q


def test_a11y_skeleton():
    body = SRC[SRC.index("<body>"):]
    assert re.match(r'<body>\s*<a class="skip-link" href="#main">본문으로 건너뛰기</a>', body)
    assert re.search(r'<main\b[^>]*\bid="main"', SRC)
    assert len(re.findall(r"<h1\b", SRC)) == 1
    assert len(re.findall(r"<h2\b", SRC)) >= 2, "each panel is a section with an <h2>"
    assert "S.whenVisible(" in SRC and "setInterval(" not in SRC
    assert "S.trapFocus" in SRC or "modal" not in SRC.lower()
    assert "outline:none" not in SRC.replace(" ", "") and "outline: none" not in SRC


def test_button_family_only():
    assert ".pill" not in SRC and not re.search(r"^\.chip\s*\{", SRC, re.M)
    assert 'class="btn btn-sm' in SRC and "className: 'btn btn-sm chip" in SRC
    assert "제거" not in SRC


def test_csp_clean_markup():
    assert 'style="' not in SRC, "inline style attributes are blocked by the nonce CSP"
    assert not re.search(r"\son[a-z]+=", SRC), "inline event handler attributes"
    assert "data:image/svg+xml" not in SRC, "the server injects the PNG icons"
