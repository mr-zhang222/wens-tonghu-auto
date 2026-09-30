"""Experiment: does simply dwelling on a DOC courseware advance its percent?

Opens the first pending DOC course, clicks the continue-learning entry,
waits, then re-reads the course percent. Read-only apart from normal
studying behaviour.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from src.browser import browser_page  # noqa: E402
from src.seek_adapter import SeekLearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402


def main() -> None:
    title_kw = sys.argv[1] if len(sys.argv) > 1 else "ELISA"
    dwell_s = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    settings = load_settings()
    with browser_page(settings) as page:
        adapter = SeekLearnAdapter(page, settings)
        adapter.open_entry()
        if not adapter.go_my_courses():
            print("[FATAL] 进不去我的课程")
            return
        course = next((c for c in adapter.scan_courses() if title_kw in c["title"]), None)
        if not course:
            print(f"[FATAL] 列表里没有含 {title_kw!r} 的课")
            return
        print(f"[before] {course['title']} 进度 {course['percent']}%")

        if not adapter._open_course(course["title"]):
            print("[FATAL] 打不开课程")
            return
        body = adapter._body_text(8000)
        print(f"[detail] 含文档={'文档' in body} 含视频={'视频' in body}")

        # 点「继续学习」进入课件
        clicked = False
        for text in ("继续学习", "开始学习"):
            try:
                loc = page.get_by_text(text, exact=True)
                if loc.count():
                    loc.first.click(timeout=8_000)
                    clicked = True
                    print(f"[open] 点击了「{text}」")
                    break
            except Exception:
                continue
        if not clicked:
            print("[open] 没找到继续/开始学习入口")

        print(f"[dwell] 挂机 {dwell_s}s ...")
        time.sleep(dwell_s)
        # 期间尝试滚动，模拟阅读
        for _ in range(3):
            try:
                page.mouse.wheel(0, 800)
            except Exception:
                pass
            time.sleep(dwell_s / 3)

        # 回列表看进度
        if adapter.go_my_courses():
            fresh = next(
                (c for c in adapter.scan_courses() if title_kw in c["title"]), None
            )
            if fresh:
                print(f"[after] {fresh['title']} 进度 {fresh['percent']}%")
            else:
                print("[after] 课程不在列表里（可能已完成！）")
        else:
            print("[after] 回不去列表")


if __name__ == "__main__":
    main()
