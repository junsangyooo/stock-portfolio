"""The shared layers must not read an account book.

market data, research and analysis are about symbols, not accounts. The moment
one of them looks up "what do I hold", the result stops being shared: the same
stored analysis would have to fork per account, and whatever the lookup
happens to name gets covered while the rest silently does not.

The direction is one-way. An account may read a shared resource; a shared
resource may never read an account. A batch job that needs to know which
symbols to work on takes them as an argument (src/portfolio/jobs.py supplies
them for the human accounts).

This is a structural rule, so it is checked structurally.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

# Layers that know nothing about who owns anything. `ledger` defines the
# account tables and assembles one account's situation, but it is handed the
# connection and the id — it never picks a book.
SHARED = ("core", "universe", "market", "research", "ledger")

# The book they must not reach into.
BOOKS = ("portfolio",)


def _modules():
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC)
        if rel.parts[0] in SHARED:
            yield rel, path


def _imported_roots(path: Path, rel: Path) -> set[str]:
    """Top-level src package names this module imports, absolute or relative."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    depth = len(rel.parts) - 1          # package depth of this module
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:              # from ...portfolio.store import x
                up = node.level - 1
                base = rel.parts[:depth - up] if depth - up > 0 else ()
                head = (node.module or "").split(".")[0]
                roots.add(base[0] if base else head)
                if not base and head:
                    roots.add(head)
            elif node.module and node.module.startswith("src."):
                roots.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("src."):
                    roots.add(a.name.split(".")[1])
    return roots


def _mentions_book(path: Path) -> set[str]:
    """Book packages this module imports, however it spells the import."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            head = node.module.split(".")[0]
            if head in BOOKS:
                hits.add(head)
            for b in BOOKS:
                if f".{b}." in f".{node.module}." :
                    hits.add(b)
        elif isinstance(node, ast.Import):
            for a in node.names:
                for b in BOOKS:
                    if a.name.split(".")[0] == b or f".{b}." in f".{a.name}.":
                        hits.add(b)
    return hits


def test_shared_layers_never_import_an_account_book():
    offenders = []
    for rel, path in _modules():
        hit = _mentions_book(path)
        if hit:
            offenders.append(f"src/{rel} → {', '.join(sorted(hit))}")
    assert not offenders, (
        "a shared layer is reading an account book:\n  " +
        "\n  ".join(offenders) +
        "\nPass the symbols in instead — see src/portfolio/jobs.py."
    )


def test_the_analysis_envelope_carries_no_position():
    """The analysis is about the symbol. One stored result serves every
    account only while it says nothing about who holds what."""
    from src.research.analyze import prompt
    src = (_SRC / "research" / "analyze" / "inputs.py").read_text(encoding="utf-8")
    assert "holdings" not in src and "avg_cost" not in src
    assert '"stance"' not in prompt._SCHEMA   # not "resistance"
    assert "보유" not in prompt.SYSTEM.replace("보유 여부를 짐작해", "")


@pytest.mark.parametrize("module,flag", [
    ("src/market/pipeline/fundamental_job.py", "--symbols"),
    ("src/market/pipeline/etf_job.py", "--symbols"),
    ("src/research/analyze/batch_job.py", "--symbols"),
])
def test_shared_batch_jobs_require_an_explicit_symbol_list(module, flag):
    """No default that quietly means 'whatever portfolio.db holds'."""
    text = (_SRC.parent / module).read_text(encoding="utf-8")
    assert f'"{flag}", required=True' in text
