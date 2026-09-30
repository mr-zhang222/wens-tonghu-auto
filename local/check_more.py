"""Decide whether another round is needed (used by local/run_until_done.bat).

Reads the LAST round's output (one round per file) and prints exactly one
word: MORE (budget ran out with courses still pending) or DONE (nothing
left, or something failed hard). ASCII output so the .bat can match it
with plain `findstr /C:"MORE"`.
"""

from __future__ import annotations

import sys

# Marks meaning "time budget ran out while courses were still pending".
MORE_MARKS = (
    "超出本次时间预算",  # per-course skip note
    "留到下次",          # same note, shorter wording
    "时间预算用尽",      # per-course summary note
)
# Explicit "nothing pending at all".
DONE_MARKS = ("今日无待学任务",)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "local/logs/round.log"
    try:
        text = open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        print("DONE")
        return
    if any(mark in text for mark in MORE_MARKS):
        print("MORE")
    else:
        print("DONE")


if __name__ == "__main__":
    main()
