"""Replace one app's block of a crontab with that app's block file.

    crontab -l | python3 deploy/install_cron.py deploy/portfolio.cron | crontab -

The block file starts with a `# >>> NAME ...` marker line and ends with `# <<< NAME ...`. Reads
the current crontab on stdin and writes the new one to stdout: only the lines from the crontab's
`# >>> NAME` marker to its `# <<< NAME` marker are replaced; everything else (other apps' blocks
and lines) passes through untouched. A block file without the markers, or a crontab without
exactly one block of that name, is refused rather than appended to — appending would run every
job twice.
"""

from __future__ import annotations

import sys


def block_name(block: str) -> str:
    """NAME from the block file's first line `# >>> NAME ...`, which its last line must close."""
    lines = block.strip("\n").splitlines()
    first = lines[0].split() if lines else []
    if len(first) < 3 or first[:2] != ["#", ">>>"]:
        raise SystemExit("the block file does not start with a '# >>> NAME' marker")
    name = first[2]
    if not lines[-1].startswith(f"# <<< {name}"):
        raise SystemExit(f"the block file does not end with '# <<< {name}'")
    return name


def replace_block(current: str, block: str) -> str:
    name = block_name(block)
    begin, end = f"# >>> {name}", f"# <<< {name}"

    def marks(prefix: str) -> list[int]:
        return [
            i for i, line in enumerate(lines) if line == prefix or line.startswith(prefix + " ")
        ]

    lines = current.splitlines()
    starts, ends = marks(begin), marks(end)
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SystemExit(f"no single {name} block in the crontab — migrate it by hand")
    new = lines[: starts[0]] + block.strip("\n").splitlines() + lines[ends[0] + 1 :]
    return "\n".join(new) + "\n"


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        block = f.read()
    sys.stdout.write(replace_block(sys.stdin.read(), block))
