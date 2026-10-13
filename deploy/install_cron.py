"""Replace the portfolio block of a crontab with deploy/portfolio.cron.

    crontab -l | python3 deploy/install_cron.py deploy/portfolio.cron | crontab -

Reads the current crontab on stdin and writes the new one to stdout. Only the
lines from the block's `# >>> portfolio` marker to its `# <<< portfolio <<<`
marker are replaced; everything else (the quant block, other apps) passes
through untouched. A crontab without the markers is refused rather than
appended to — appending would run every job twice.
"""
from __future__ import annotations

import sys

BEGIN, END = "# >>> portfolio", "# <<< portfolio"


def replace_block(current: str, block: str) -> str:
    lines = current.splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith(BEGIN)]
    ends = [i for i, l in enumerate(lines) if l.startswith(END)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SystemExit("no single portfolio block in the crontab — migrate it by hand")
    new = lines[:starts[0]] + block.rstrip("\n").splitlines() + lines[ends[0] + 1:]
    return "\n".join(new) + "\n"


if __name__ == "__main__":
    block = open(sys.argv[1], encoding="utf-8").read()
    sys.stdout.write(replace_block(sys.stdin.read(), block))
