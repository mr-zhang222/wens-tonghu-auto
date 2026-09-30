"""定点验证 `SeekLearnAdapter._finish_tail`（本次修的那个 bug）。

做法：打开指定课程的一讲 → 把 currentTime 直接推到「距结尾 tail 秒」的位置
（完全复刻正式流程进入尾段时的状态）→ 调用修复后的 `_finish_tail` →
回读平台侧这一讲是否变成「100% 再次学习」。

这样不用等 100 分钟的长视频自然跑完，就能验证"尾段能不能被确认为完成"。

用法（项目根目录）：
  HEADLESS=1 python local/verify_tail.py 研发看板
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser import browser_page  # noqa: E402
from src.seek_adapter import SeekLearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402

STATE_JS = """(el) => ({
  cur: el.currentTime || 0, dur: el.duration || 0,
  ended: !!el.ended, paused: !!el.paused, ready: el.readyState || 0
})"""


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "研发看板"
    settings = load_settings()
    print(f"[verify] HEADLESS={settings.headless} tail={settings.final_tail_seconds}s")

    with browser_page(settings) as page:
        adapter = SeekLearnAdapter(page, settings)
        adapter.open_entry()
        if not adapter.go_my_courses():
            print("[verify] 进不去「我的课程」")
            return 1

        courses = adapter.scan_courses()
        target = next((c for c in courses if keyword in c["title"]), None)
        if target is None:
            print(f"[verify] 找不到含 {keyword!r} 的课程")
            return 1
        print(f"[verify] 目标课程：{target['title']}（进度 {target['percent']}%）")

        if not adapter._open_course(target["title"]):
            print("[verify] 打不开课程详情")
            return 1

        lectures = adapter._list_lectures(stable=True)
        pending = [i for i, lec in enumerate(lectures) if not lec["done"]]
        if not pending:
            print("[verify] 这门课已全部完成")
            return 0
        index = pending[0]
        print(f"[verify] 第 {index + 1} 讲，开始前：{lectures[index]['text'][:70]}")

        if not adapter._click_lecture(index):
            print("[verify] 点不开讲次")
            return 1
        adapter._wait_idle(3_000)

        video = adapter._next_video(set())
        if video is None:
            print("[verify] 找不到 video 元素")
            return 1

        duration = adapter._wait_duration(video, time.time() + 30)
        if not duration:
            print("[verify] 读不到时长")
            return 1
        tail = max(3.0, settings.final_tail_seconds)
        target_time = duration - tail
        print(f"[verify] 时长 {duration:.1f}s，直接推到 {target_time:.1f}s（模拟进入尾段）")

        video.evaluate(
            "(el, t) => { try { el.muted = true; } catch (e) {}"
            " try { el.currentTime = t; } catch (e) {}"
            " const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }",
            target_time,
        )
        page.wait_for_timeout(1_500)
        before = video.evaluate(STATE_JS)
        print(f"[verify] 推进后：cur={before['cur']:.2f} dur={before['dur']:.2f} "
              f"paused={before['paused']} ended={before['ended']}")

        print("[verify] 调用修复后的 _finish_tail（最多 120s）...")
        started = time.time()
        peak, ok = adapter._finish_tail(video, time.time() + 120)
        print(f"[verify] _finish_tail 返回 peak={peak:.2f} ok={ok}，耗时 {time.time() - started:.1f}s")

        print("[verify] 回读平台侧状态 ...")
        adapter._wait_idle(2_500)
        fresh = adapter._list_lectures(stable=True)
        for i, lec in enumerate(fresh, 1):
            mark = "✅已完成" if lec["done"] else "  待学 "
            print(f"    {mark} {i}. pct={lec['percent']}  {lec['text'][:76]}")

        done = bool(fresh and index < len(fresh) and fresh[index]["done"])
        print(f"\n[verify] 结论：{'✅ 通过 —— 讲次被平台记录为 100%' if done else '❌ 未通过 —— 讲次仍未完成'}")
        return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
