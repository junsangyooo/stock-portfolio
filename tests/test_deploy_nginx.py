"""deploy/nginx-portfolio.conf takes Cloudflare's edges only and sends HSTS.

A visitor that does not come through Cloudflare (a direct hit on the server's address) gets the
connection closed (444), on port 80 and 443 alike. The quant site on the same nginx keeps the same
list of ranges in its own repository: when Cloudflare publishes a change, update both.
"""
from __future__ import annotations

import ipaddress
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


def test_only_cloudflare_ranges_and_the_server_itself_are_let_in():
    conf = _code((_DEPLOY / "nginx-portfolio.conf").read_text())
    assert re.search(r"^\s*default 0;", conf, re.M)  # everyone else is refused
    _, allowed = _geo(conf)
    assert _LOCAL <= allowed                       # the server's own checks
    ranges = allowed - _LOCAL
    assert len(ranges) >= 10                       # Cloudflare publishes 15 IPv4 + 7 IPv6
    for r in ranges:
        ipaddress.ip_network(r)                    # every entry is a valid range


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
