"""诊断脚本：确认 headless 下 video 的自然播放能不能推进 currentTime。

背景：seek 模式靠「拽 currentTime」推进进度，最后留 final_tail_seconds 秒
让它自然播完，指望平台自己的「播完」逻辑把这一讲记成 100%。
实测发现短讲次会停在 duration - tail + 1 附近（例如 75s 的视频停在 68s = 91%），
说明**尾段的自然播放没有推进**，讲次永远到不了 100%。

本脚本只做观测，不改业务代码：
  A. 打开指定的讲，读出 video 初始状态
  B. 调 play()，连续采样 15 秒，看 currentTime 有没有自己往前走
  C. 从暂停态/播放态各试一次
  D. 试着直接 seek 到结尾，看能不能触发 ended

用法（在项目根目录）：
  HEADLESS=1 python local/diag_tail.py ["课程标题关键字"]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser import browser_page  # noqa: E402
from src.seek_adapter import SeekLearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402

VIDEO_STATE_JS = """(el) => ({
  cur: el.currentTime, dur: el.duration, paused: el.paused, ended: el.ended,
  ready: el.readyState, muted: el.muted, rate: el.playbackRate,
  err: el.error ? (el.error.code + ':' + el.error.message) : null,
  vis: document.visibilityState, hidden: document.hidden,
  net: el.networkState,
})"""


def show(tag: str, video) -> dict:
    state = video.evaluate(VIDEO_STATE_JS)
    print(
        f"  [{tag}] cur={state['cur']:.2f} dur={state['dur']:.2f} "
        f"paused={state['paused']} ended={state['ended']} ready={state['ready']} "
        f"rate={state['rate']} net={state['net']} vis={state['vis']} err={state['err']}"
    )
    return state


def sample(video, seconds: int, tag: str) -> list[float]:
    """每秒采一次 currentTime，返回轨迹。"""
    track: list[float] = []
    for i in range(seconds):
        time.sleep(1)
        try:
            cur = float(video.evaluate("(el) => el.currentTime"))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{tag}] 第 {i + 1}s 读不到：{type(exc).__name__}")
            break
        track.append(cur)
        print(f"  [{tag}] +{i + 1}s  cur={cur:.2f}")
    return track


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "在【学习平台】"
    settings = load_settings()
    print(f"[diag] HEADLESS={settings.headless}  tail={settings.final_tail_seconds}s")
    print(f"[diag] 目标课程关键字：{keyword}")

    with browser_page(settings) as page:
        adapter = SeekLearnAdapter(page, settings)
        adapter.open_entry()
        print("[diag] 入口已打开")

        if not adapter.go_my_courses():
            print("[diag] 进不去「我的课程」")
            return 1

        courses = adapter.scan_courses()
        target = next((c for c in courses if keyword in c["title"]), None)
        if target is None:
            print(f"[diag] 没找到含 {keyword!r} 的课程，现有：")
            for c in courses[:25]:
                print(f"    {c['percent']:3d}%  {c['title']}")
            return 1
        print(f"[diag] 目标课程：{target['title']}（进度 {target['percent']}%）")

        if not adapter._open_course(target["title"]):
            print("[diag] 打不开课程详情")
            return 1

        lectures = adapter._list_lectures(stable=True)
        print(f"[diag] 目录共 {len(lectures)} 讲：")
        for i, lec in enumerate(lectures, 1):
            print(f"    {i}. done={lec['done']} pct={lec['percent']}  {lec['text'][:70]}")

        pending = [i for i, lec in enumerate(lectures) if not lec["done"]]
        if not pending:
            print("[diag] 这一门已经全部完成，无需诊断")
            return 0

        index = pending[0]
        print(f"[diag] 点开第 {index + 1} 讲")
        if not adapter._click_lecture(index):
            print("[diag] 点不开")
            return 1
        adapter._wait_idle(3_000)

        video = adapter._next_video(set())
        if video is None:
            print("[diag] 页面上找不到可见的 video 元素")
            return 1

        print("\n=== A. 初始状态 ===")
        initial = show("init", video)
        duration = float(initial["dur"] or 0)
        print(f"  距结尾还差 {duration - float(initial['cur']):.2f}s")

        print("\n=== B. play() 后采样 15 秒（自然播放能不能推进？）===")
        video.evaluate(
            "(el) => { try { el.muted = true; } catch (e) {}"
            " const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }"
        )
        track = sample(video, 15, "play")
        advanced = (track[-1] - track[0]) if len(track) >= 2 else 0.0
        print(f"  → 15 秒内 currentTime 推进 = {advanced:.2f}s")
        print(f"  → 结论：自然播放{'有效' if advanced > 3 else '没有推进（这就是 bug 根因）'}")

        print("\n=== C. 直接 seek 到结尾，看能不能触发 ended ===")
        before = video.evaluate(VIDEO_STATE_JS)
        print(f"  seek 前：cur={before['cur']:.2f} ended={before['ended']}")
        video.evaluate(
            "(el, d) => { try { el.muted = true; } catch (e) {}"
            " try { el.currentTime = d; } catch (e) {}"
            " const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }",
            duration,
        )
        for i in range(6):
            time.sleep(1)
            st = show(f"seek2end +{i + 1}s", video)
            if st["ended"]:
                print("  → ✅ 触发 ended")
                break
        else:
            print("  → ❌ 6 秒内没触发 ended")

        print("\n=== D. 回读平台侧这一讲的状态 ===")
        adapter._wait_idle(2_000)
        fresh = adapter._list_lectures(stable=True)
        for i, lec in enumerate(fresh, 1):
            print(f"    {i}. done={lec['done']} pct={lec['percent']}  {lec['text'][:70]}")

        print("\n[diag] 结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
