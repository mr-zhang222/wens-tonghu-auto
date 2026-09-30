"""Dump the courseware TYPE mix (视频/文档/...) for the pending courses.

The lecture parser only accepts items starting with '视频'. Overnight the
pending pool seems to have shifted to DOC-type courseware. This script
opens the first few courses and prints the RAW courseware rows so we can
see what we are actually facing. Read-only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from src.browser import browser_page  # noqa: E402
from src.seek_adapter import SeekLearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402

RAW_JS = r"""() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div,li,span,p')) {
    if (el.childElementCount) continue;
    const t = el.textContent.trim();
    if (!t || seen.has(t)) continue;
    if (/^(视频|文档|音频|资料|附件|课件)/.test(t) || t.includes('%')) {
      seen.add(t);
      out.push(t);
    }
  }
  return out.slice(0, 30);
}"""


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    settings = load_settings()
    with browser_page(settings) as page:
        adapter = SeekLearnAdapter(page, settings)
        adapter.open_entry()
        if not adapter.go_my_courses():
            print("[FATAL] 进不去我的课程")
            return
        courses = adapter.scan_courses()
        pending = [c for c in courses if int(c.get("percent") or 0) < 100]
        print(f"[probe] 待学 {len(pending)} 门，抽查前 {min(limit, len(pending))} 门：\n")
        for course in pending[:limit]:
            title = course["title"]
            ok = adapter._open_course(title)
            print(f"=== {title}（进度 {course['percent']}%）open={ok} ===")
            if ok:
                try:
                    for row in page.evaluate(RAW_JS):
                        print("   ", row[:90])
                except Exception as exc:
                    print("    读取失败:", exc)
            adapter.go_my_courses()
            print()


if __name__ == "__main__":
    main()
