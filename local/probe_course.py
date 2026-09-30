"""只读探针：打印「我的课程」全部进度，以及指定课程的讲次明细。

纯读取，不点讲次、不播放 —— 可以在正式刷课跑动期间并排使用，
用来回答"到底有没有推进"。

用法（项目根目录）：
  HEADLESS=1 python local/probe_course.py                # 只列课程
  HEADLESS=1 python local/probe_course.py 研发看板        # 再展开这门课的讲次
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser import browser_page  # noqa: E402
from src.learn_adapter import LearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    settings = load_settings()

    with browser_page(settings) as page:
        adapter = LearnAdapter(page, settings)
        adapter.open_entry()
        if not adapter.go_my_courses():
            print("[probe] 进不去「我的课程」")
            return 1

        courses = adapter.scan_courses()
        pending = [c for c in courses if int(c.get("percent") or 0) < 100]
        print(f"[probe] 列表共 {len(courses)} 门，其中未完成 {len(pending)} 门：")
        for i, c in enumerate(courses, 1):
            flag = "  " if int(c.get("percent") or 0) < 100 else "✅"
            print(f"  {flag} {i:2d}. {c['percent']:3d}%  {c['title']}")

        if not keyword:
            return 0

        target = next((c for c in courses if keyword in c["title"]), None)
        if target is None:
            print(f"[probe] 没有含 {keyword!r} 的课程")
            return 1

        print(f"\n[probe] 展开：{target['title']}（进度 {target['percent']}%）")
        if not adapter._open_course(target["title"]):
            print("[probe] 打不开课程详情")
            return 1
        lectures = adapter._list_lectures(stable=True)
        for i, lec in enumerate(lectures, 1):
            mark = "✅已完成" if lec["done"] else "  待学 "
            print(f"  {mark} {i:2d}. pct={lec['percent']}  {lec['text'][:76]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
