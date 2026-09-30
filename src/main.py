"""主流程：开页面 → 先查 → 后做 → 推送飞书。

退出码：
  0  正常运行（哪怕个别课没刷完，也算跑通，明细里会写清楚）
  1  登录态失效或硬异常 —— 让 Actions 变红，提醒你去更新 secret
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone

from .browser import browser_page
from .feishu import send_text
from .fast_adapter import FastLearnAdapter
from .learn_adapter import LearnAdapter, NotLoggedIn, TaskResult
from .seek_adapter import SeekLearnAdapter
from .settings import Settings, load_settings

CST = timezone(timedelta(hours=8))

STATUS_ICON = {"done": "✅", "skipped": "⏭️", "failed": "⚠️"}


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def _summarize(results: list[TaskResult]) -> tuple[int, int, int, float]:
    done = sum(1 for r in results if r.status == "done")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    minutes = sum(r.watched_seconds for r in results) / 60.0
    return done, skipped, failed, minutes


def _build_message(results: list[TaskResult], error: str = "") -> str:
    lines = ["【同呼学习 · 自动刷课】", f"时间：{_now()}（北京时间）"]

    if error:
        lines.append("")
        lines.append("❌ 本次未跑完")
        lines.append(error)
        lines.append("")
        lines.append("处理办法：在本机重新执行 local/export_login.py，")
        lines.append("把新的凭据覆盖到 GitHub secret，再手动触发一次。")
        return "\n".join(lines)

    if not results:
        lines.append("")
        lines.append("✅ 今日无待学任务，已完成，无需重复")
        return "\n".join(lines)

    done, skipped, failed, minutes = _summarize(results)
    headline = "✅ 完成" if done > 0 else ("⏭️ 全部跳过" if failed == 0 else "⚠️ 有失败")
    lines.append("")
    lines.append(f"{headline}：待学 {len(results)} 项 → 完成 {done}，跳过 {skipped}，失败 {failed}")
    if minutes > 0:
        lines.append(f"本次观看总时长：{minutes:.1f} 分钟")
    lines.append("")
    lines.append("明细：")
    for index, item in enumerate(results, start=1):
        icon = STATUS_ICON.get(item.status, "•")
        suffix = f" — {item.detail}" if item.detail else ""
        lines.append(f"{index}. {icon} {item.label}{suffix}")
    return "\n".join(lines)


def _pick_adapter(page, settings: Settings) -> LearnAdapter:
    """按 FAST_MODE / FAST_METHOD 选适配器。

    - 默认（FAST_MODE 未开）：真实播放，最稳；
    - FAST_METHOD=seek（默认快速法）：只拽 video.currentTime，上报由页面自己发，
      签名 / CSRF 天然合法 —— 因为服务端有 signature 校验，这条是快速路的主选；
    - FAST_METHOD=replay（实验）：抓一条真心跳当模板后重放，只在平台没真正校验
      signature 时才成立，默认不启用。
    """
    if not settings.fast_mode:
        return LearnAdapter(page, settings)
    if settings.fast_method == "replay":
        print("[mode] replay：重放心跳（实验，signature 校验可能拒绝）")
        return FastLearnAdapter(page, settings)
    print("[mode] seek：拽进度条，上报由页面自己发")
    return SeekLearnAdapter(page, settings)


def _print_inspect(report: dict, settings: Settings) -> None:
    """把勘察结果打到 Actions 日志里。

    字段一律用 .get() 取值 —— 勘察报告是「尽力而为」的产物（某个环节没走到就少一个键），
    不能因为缺一个键就让整个验收流程崩掉。
    """
    print(f"[inspect] 入口地址：{report.get('entry_url', '(未知)')}")
    print(f"[inspect] 入口标题：{report.get('entry_title', '(未知)')}")

    if report.get("note"):
        print(f"[inspect] ⚠️ {report['note']}")

    courses = report.get("courses") or []
    print(f"[inspect] 课程列表：{len(courses)} 门")
    for course in courses[:15]:
        print(f"    - {course.get('title', '?')}  进度 {course.get('percent')}%")

    pending = report.get("matched_pending") or []
    print(f"[inspect] 待学课程：{len(pending)} 门")
    for title in pending[:15]:
        print(f"    - {title}")

    first = report.get("first_pending_course")
    if first:
        print(f"[inspect] 探路课程：{first.get('title', '?')}")

    lectures = report.get("lectures") or []
    pending_lectures = [lec for lec in lectures if not lec.get("done")]
    print(f"[inspect] 讲次：{len(lectures)} 讲（待学 {len(pending_lectures)}）")
    for index, lecture in enumerate(lectures, start=1):
        mark = "已完成" if lecture.get("done") else "待学"
        print(f"    {index}. [{mark}] {str(lecture.get('text', ''))[:70]}")

    videos = report.get("videos") or []
    print(f"[inspect] 页面 video 元素：{len(videos)} 个")
    for video in videos:
        duration = float(video.get("dur") or 0)
        current = float(video.get("t") or 0)
        print(
            f"    - 时长 {duration:.0f}s，已播到 {current:.0f}s，"
            f"src={str(video.get('src', ''))[:60]}"
        )
        if duration and duration <= 0:
            print("      ⚠️ 时长读不出来 → 多半是浏览器没解码 H.264")

    print(f"[inspect] 产物已写入 {settings.artifact_dir}")


def main() -> int:
    settings = load_settings()
    results: list[TaskResult] = []
    error = ""
    exit_code = 0

    try:
        with browser_page(settings) as page:
            adapter: LearnAdapter = _pick_adapter(page, settings)
            adapter.open_entry()

            if settings.inspect:
                _print_inspect(adapter.inspect(), settings)
                return 0

            results = adapter.run()
    except NotLoggedIn as exc:
        error = f"登录态失效：{exc}"
        exit_code = 1
    except Exception as exc:  # noqa: BLE001
        error = f"运行异常：{type(exc).__name__}: {exc}"
        traceback.print_exc()
        exit_code = 1

    message = _build_message(results, error)
    print(message)

    ok, detail = send_text(settings.feishu_webhook, settings.feishu_secret, message)
    if not ok:
        print(f"[feishu] 未推送成功：{detail}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
