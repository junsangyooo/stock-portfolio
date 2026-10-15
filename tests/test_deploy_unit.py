"""deploy/dashboard.service keeps keys and files this app never uses out of reach.

The web process faces the internet and loads the whole secrets file (EnvironmentFile=). Audit
2026-10-13: it has no Toss client, so the live account's keys are unset, together with every Toss
name src/ would read; the secrets folder is hidden too, because core.secrets.get() reads the file
itself when the environment lacks a name. The quant engine's folder on the same server is hidden
as well.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_UNIT = _ROOT / "deploy" / "dashboard.service"


def _directives(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "[")) and "=" in line:
            key, value = line.split("=", 1)
            out.append((key, value))
    return out


def _src_text(marker: str) -> str:
    """The lines of src/ that contain `marker`."""
    out = []
    for path in sorted((_ROOT / "src").rglob("*.py")):
        out += [l for l in path.read_text(encoding="utf-8").splitlines() if marker in l]
    return "\n".join(out)


def test_the_toss_keys_never_reach_the_web_process():
    unit = _directives(_UNIT.read_text())
    unset = " ".join(v for k, v in unit if k == "UnsetEnvironment").split()
    read = set(re.findall(r"\b(TOSS_[A-Z_]+)\b", _src_text("secrets.get(")))
    toss = {"TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_NO"}
    for name in read | toss | {"ANTHROPIC_API_KEY", "KRX_ID", "KRX_PW"}:
        assert name in unset, name
    assert ("EnvironmentFile", "/home/ubuntu/dotclaude/secrets/.env") in unit


def test_secrets_and_the_quant_folder_are_hidden():
    unit = _directives(_UNIT.read_text())
    hidden = " ".join(v for k, v in unit if k == "InaccessiblePaths").split()
    assert "-/home/ubuntu/dotclaude/secrets" in hidden  # core.secrets._FILES on the server
    assert "-/home/ubuntu/.claude/secrets" in hidden
    assert "-/home/ubuntu/quant-dashboard" in hidden


def test_the_unit_runs_from_this_repository():
    unit = dict(_directives(_UNIT.read_text()))
    assert unit["WorkingDirectory"] == "/home/ubuntu/stock-portfolio"
    assert unit["ExecStart"].startswith("/home/ubuntu/stock-portfolio/.venv/bin/python -m src.dashboard_web.server")
    assert "--host 127.0.0.1" in unit["ExecStart"]
    assert unit["StandardError"] == "append:/home/ubuntu/stock-portfolio/data/logs/dashboard.log"
