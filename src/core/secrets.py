"""Secrets — the environment first, then the secrets .env file.

systemd and cron inject ~/dotclaude/secrets/.env as the environment; a local
CLI run has none of it, so the file is parsed directly. The laptop keeps the
file under ~/.claude/secrets, Oracle under ~/dotclaude/secrets.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_FILES = (Path.home() / ".claude" / "secrets" / ".env",
          Path.home() / "dotclaude" / "secrets" / ".env")


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Value of `name`: the process environment, else the first .env file
    that defines it (quotes stripped), else `default`."""
    v = os.environ.get(name)
    if v:
        return v
    pat = re.compile(r"\s*" + re.escape(name) + r"\s*=\s*(.*)$")
    for p in _FILES:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            m = pat.match(line)
            if m:
                return m.group(1).strip().strip('"').strip("'") or default
    return default
