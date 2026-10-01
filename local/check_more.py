"""Decide what the loop should do after one round (local/run_until_done.bat).

Reads the LAST round's output (one round per file) and prints exactly one
word, ASCII only so the .bat can match it with plain findstr:

  MORE    budget ran out while courses were still pending -> next round
  DONE    nothing pending (or everything completed)        -> stop, success
  FAILED  the round made zero progress (0 done, everything
          skipped/failed) -> stop immediately, no cooldown retry
          (user rule 2026-10-01: playback failures are skipped, not retried)
"""

from __future__ import annotations

import re
import sys

# Marks meaning "time budget ran out while courses were still pending".
MORE_MARKS = (
    "超出本次时间预算",  # per-course skip note
    "留到下次",          # same note, shorter wording
    "时间预算用尽",      # per-course summary note
)
DONE_MARKS = ("今日无待学任务",)

HEADLINE_RE = re.compile(r"完成 (\d+)，跳过 (\d+)，失败 (\d+)")


def classify(text: str) -> str:
    if any(mark in text for mark in MORE_MARKS):
        return "MORE"
    if any(mark in text for mark in DONE_MARKS):
        return "DONE"
    m = HEADLINE_RE.search(text)
    if m:
        done, skipped, failed = (int(g) for g in m.groups())
        if done == 0 and failed + skipped > 0:
            # No budget markers + zero progress => something broke
            # (page didn't render, clicks failed...). Retry, don't lie.
            return "FAILED"
    return "DONE"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "local/logs/round.log"
    try:
        text = open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        print("DONE")
        return
    print(classify(text))


if __name__ == "__main__":
    main()
