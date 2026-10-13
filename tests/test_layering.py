"""Layers depend downward only (정의서 §1.1 규칙 2).

    core                          settings, paths, time, secrets, model calls, jobs
      ↑
    universe · market · research  know nothing about accounts
      ↑
    portfolio                     the account book, its service and its chat
      ↑
    api                           routing only
      ↑
    dashboard_web                 the server and the pages

The rule that matters most is the middle one. market data, research and
analysis are about symbols, not accounts. The moment one of them looks up
"what do I hold", the result stops being shared: the same stored analysis
would have to fork per account, and whatever the lookup happens to name gets
covered while the rest silently does not. A batch job that needs to know
which symbols to work on takes them as an argument (portfolio/supply_job.py
supplies them).

This is a structural rule, so it is checked structurally.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

RANK = {"core": 0, "universe": 1, "market": 1, "research": 1,
        "portfolio": 2, "api": 3, "dashboard_web": 4}


def _imports(path: Path) -> set[str]:
    """Top-level src packages a module imports, absolute or relative."""
    rel = path.relative_to(_SRC)
    pkg = list(rel.parts[:-1])
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[:len(pkg) - (node.level - 1)]
                full = base + (node.module.split(".") if node.module else [])
                if not full and node.module is None:
                    full = [a.name for a in node.names][:1]
                if full:
                    out.add(full[0])
            elif node.module and node.module.startswith("src."):
                out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("src."):
                    out.add(a.name.split(".")[1])
    return out


def test_every_top_level_package_has_a_layer():
    pkgs = {p.name for p in _SRC.iterdir()
            if p.is_dir() and (p / "__init__.py").exists()}
    assert pkgs == set(RANK), pkgs ^ set(RANK)


def test_layers_depend_downward_only():
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        own = path.relative_to(_SRC).parts[0]
        if own not in RANK:
            continue
        for dep in _imports(path):
            if dep in RANK and RANK[dep] > RANK[own]:
                offenders.append(f"src/{path.relative_to(_SRC)} → {dep}")
    assert not offenders, (
        "a lower layer imports a higher one:\n  " + "\n  ".join(offenders) +
        "\nPass what it needs in as an argument instead.")


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
