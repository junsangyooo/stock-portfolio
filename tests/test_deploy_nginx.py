"""deploy/nginx-portfolio.conf takes Cloudflare's edges only, like nginx-quant.conf.

Both files list Cloudflare's ranges; a range one of them lacks turns that edge's visitors
away from one site and not the other. tests/autotrade checks the quant file against itself;
this checks the two files against each other.
"""
from __future__ import annotations

import re
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
_LOCAL = {"127.0.0.1", "::1"}


def _geo(text: str) -> tuple[str, set[str]]:
    m = re.search(r"^geo \$realip_remote_addr \$(\w+) \{(.*?)^\}", text, re.S | re.M)
    assert m is not None
    allowed = {l.split()[0] for l in m.group(2).splitlines()
               if l.strip() and not l.strip().startswith(("default", "#")) and l.split()[1] == "1;"}
    return m.group(1), allowed


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def test_both_sites_take_the_same_cloudflare_ranges():
    p_var, portfolio = _geo((_DEPLOY / "nginx-portfolio.conf").read_text())
    q_var, quant = _geo((_DEPLOY / "nginx-quant.conf").read_text())
    assert portfolio - _LOCAL and portfolio == quant
    assert _LOCAL <= portfolio                     # the server's own checks
    # one http block holds both files: a second geo of the same name would clash
    assert p_var != q_var


def test_portfolio_closes_on_non_cloudflare_and_sends_hsts():
    conf = _code((_DEPLOY / "nginx-portfolio.conf").read_text())
    var, _ = _geo(conf)
    servers = conf.split("server {")[1:]
    assert len(servers) == 2
    for s in servers:                              # port 80 and 443 alike
        assert f"if (${var} = 0) {{\n        return 444;" in s
    tls = servers[1]
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in tls
    assert "includeSubDomains" not in tls          # other *.jsyoo.dev sites decide for themselves
    assert "proxy_pass http://127.0.0.1:8888;" in tls
