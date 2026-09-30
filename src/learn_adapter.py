"""同呼学习平台适配层 —— 全项目唯一需要「照着真实页面调」的文件。

真实页面结构（2026-09-30 用真实账号实测，截图在 artifacts/probe/）：

    入口(mobile.html) → 学习平台首页
      └ 底部标签「我的」→「当年学习总课时 / 我的课程 / 我的专题 / …」
          └「我的课程」→ 课程卡片列表（含 筛选 未完成/已完成）
              · 卡片文案：`<课程名> 学分: 3 课时: 3 进度： 30%`
          └ 点课程 → 课程详情（目录 共 N 讲）
              · 每讲：`视频\n<讲次名>\n00:06:46`，学到 100% 的讲带「再次学习」
              · 底部「继续学习」直达当前进度
          └ 播放器（video.js 系）：打开即自动续播到上次位置（paused），倍速 0.5x~1.5x

三条硬约束（对齐原文里那三句"不是客套话"的提示词）：

1. **顺序钉死：先查后做。** 先扫「进度 < 100%」的课程，再逐讲补；
   已经 100% 的一律跳过。跑三次和跑一次结果一样（幂等）。
2. **以实际为准。** 所有关键字/选择器集中在下面常量与 JS 里，
   页面改版先跑 INSPECT=1 拿真实 DOM，别照抄网上旧选择器。
3. **单点失败不污染结论。** 某一讲挂了记下原因继续下一讲，整体结论由回读平台状态决定。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .settings import Settings

# ---------------------------------------------------------------- 可调常量

TAB_MINE = "我的"            # 底部标签
MENU_MY_COURSES = "我的课程"  # 「我的」页里的菜单项
COURSE_DONE_PERCENT = 100    # 课程进度到多少算完

# 讲次「已学完」的标记：进度条 100% 的讲会带「再次学习」入口
LECTURE_DONE_MARKS = ("再次学习", "已学完")

# 页面加载失败时的自愈入口（这套 SPA 偶发「资源文件加载失败/点击重试」）
RETRY_HINT = "点击重试"

# 打开入口页的重试策略。
# ⚠️ 云端 runner 在境外，访问国内企业站可能极慢甚至完全不通 ——
# 这种情况下「重试」救不了，但能让我们把「网络不通」和「登录态失效」区分开，
# 不至于把一个网络超时误报成"请重新导出登录态"，把排查方向带偏。
GOTO_ATTEMPTS = 3
GOTO_TIMEOUT_MS = 60_000

# 出现这些字说明登录态没了
LOGIN_HINTS = ("忘记密码", "短信登录", "验证码登录", "扫码登录")

# 兼容保留：页面级"已完成"字样
DONE_KEYWORDS = ("已完成", "已学完", "再次学习")

PROGRESS_RE = re.compile(r"进度\s*[：:]\s*(\d{1,3})%")

# ---- 页面内 JS：课程卡片扫描（innerText 形如 `<名> 学分: 3 课时: 3 进度： 30%`）
COURSE_SCAN_JS = r"""() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div,li')) {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const m = t.match(/^(.{2,50}?)\s*学分\s*[：:]\s*\d+\s*课时\s*[：:]\s*\d+\s*进度\s*[：:]\s*(\d{1,3})%\s*$/);
    if (m && !seen.has(m[1])) { seen.add(m[1]); out.push({ title: m[1], percent: parseInt(m[2], 10) }); }
  }
  return out;
}"""

# ---- 页面内 JS：课程目录里的讲次列表
# 实测 DOM（diag_lectures）：未学完 = `视频 <名> 00:06:46 0% 开始学习`；
# 学完 = `视频 <名> 00:13:12 100% 再次学习`。百分比和动作链接都在文本里。
LECTURE_LIST_JS = r"""() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div,li')) {
    const raw = (el.innerText || '').trim();
    if (!raw.startsWith('视频')) continue;
    const t = raw.replace(/\s+/g, ' ').trim();
    const durs = t.match(/\d{1,2}:\d{2}(:\d{2})?/g) || [];
    if (durs.length !== 1 || t.length > 150) continue;
    if (seen.has(t)) continue;
    seen.add(t);
    const pm = t.match(/(\d{1,3})\s*%(?!\))/);
    const percent = pm ? parseInt(pm[1], 10) : null;
    const done = percent === 100 || /再次学习|已学完/.test(t);
    const hasStatus = percent !== null || /开始学习|继续学习|再次学习/.test(t);
    out.push({ text: t, done, percent, hasStatus });
  }
  return out;
}"""

# ---- 页面内 JS：按序号点讲次（与 LECTURE_LIST_JS 同一过滤，保证下标一致）
LECTURE_CLICK_JS = r"""(args) => {
  const els = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div,li')) {
    const raw = (el.innerText || '').trim();
    if (!raw.startsWith('视频')) continue;
    const t = raw.replace(/\s+/g, ' ').trim();
    const durs = t.match(/\d{1,2}:\d{2}(:\d{2})?/g) || [];
    if (durs.length !== 1 || t.length > 150) continue;
    if (seen.has(t)) continue;
    seen.add(t);
    els.push(el);
  }
  const el = els[args.index];
  if (!el) return false;
  el.scrollIntoView({ block: 'center' });
  el.click();
  return true;
}"""


class NotLoggedIn(RuntimeError):
    """登录态失效——需要重跑 local/export_login.py 更新 secret。"""


class EntryUnreachable(RuntimeError):
    """入口页根本打不开（网络超时 / DNS 失败 / 站点不可达）。

    与 NotLoggedIn 严格区分：这不是凭据问题，重跑导出脚本没用。
    云端 runner 位于境外，访问国内企业站时最常见的就是这一类。
    """


@dataclass
class Task:
    label: str
    handle: Any = None


@dataclass
class TaskResult:
    label: str
    status: str  # done / skipped / failed
    detail: str = ""
    watched_seconds: float = 0.0


class LearnAdapter:
    """真实播放模式基类：导航 + 逐讲真实播放；播放策略可被子类替换。"""

    def __init__(self, page, settings: Settings) -> None:
        self.page = page
        self.settings = settings

    # ------------------------------------------------------------ 进出页面

    def _goto_entry(self) -> None:
        """打开入口页。失败会重试；重试仍失败则抛 EntryUnreachable（而不是 NotLoggedIn）。"""
        last_error = ""
        for attempt in range(1, GOTO_ATTEMPTS + 1):
            try:
                self.page.goto(
                    self.settings.entry_url,
                    wait_until="domcontentloaded",
                    timeout=GOTO_TIMEOUT_MS,
                )
                self.page.wait_for_timeout(3_000)
                return
            except Exception as exc:  # noqa: BLE001 - 超时/DNS/连接都被这一类覆盖
                last_error = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
                print(
                    f"[entry] 第 {attempt}/{GOTO_ATTEMPTS} 次打开入口失败：{last_error}"
                )
                if attempt < GOTO_ATTEMPTS:
                    self.page.wait_for_timeout(3_000)
        raise EntryUnreachable(
            f"连不上入口页（已重试 {GOTO_ATTEMPTS} 次）：{self.settings.entry_url}\n"
            f"最后一次错误：{last_error}"
        )

    def open_entry(self) -> None:
        self._goto_entry()
        self.assert_logged_in()

    def assert_logged_in(self) -> None:
        url = self.page.url
        if "login" in url.lower() or "/idmauth/" in url:
            raise NotLoggedIn(f"页面被重定向到登录页：{url}")
        text = self._body_text()
        if any(hint in text for hint in LOGIN_HINTS) and "我的课程" not in text:
            raise NotLoggedIn("页面渲染成了登录表单，登录态已失效")

    def _body_text(self, limit: int = 6000) -> str:
        try:
            return (self.page.inner_text("body") or "")[:limit]
        except Exception:
            return ""

    def _frames(self) -> list:
        return list(self.page.frames)

    def _wait_idle(self, extra_ms: int = 2_500) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        self.page.wait_for_timeout(extra_ms)

    def _recover(self) -> None:
        """这套 SPA 偶发「资源文件加载失败 / 点击重试」，能救就救。"""
        try:
            retry = self.page.get_by_text(RETRY_HINT, exact=True)
            if retry.count() > 0 and retry.first.is_visible():
                retry.first.click(timeout=5_000)
                self.page.wait_for_timeout(4_000)
                return
        except Exception:
            pass
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(4_000)
        except Exception:
            pass

    # ------------------------------------------------------------ 导航：我的 → 我的课程

    def go_my_courses(self, tries: int = 3) -> bool:
        """从入口一路点到「我的课程」列表页。页面偶发加载失败，多试几次。"""
        for _ in range(tries):
            self._goto_entry()
            self.assert_logged_in()
            try:
                self.page.get_by_text(TAB_MINE, exact=True).first.click(timeout=8_000)
                self.page.wait_for_timeout(3_500)
                self.page.get_by_text(MENU_MY_COURSES, exact=True).first.click(timeout=8_000)
                self.page.wait_for_timeout(4_500)
            except Exception:
                self._recover()
                continue
            body = self._body_text()
            if "进度" in body or "筛选" in body:
                return True
            self._recover()
        return False

    # ------------------------------------------------------------ 先查

    def scan_courses(self) -> list[dict]:
        """只读地扫出课程卡片：[{title, percent}]。"""
        try:
            courses = self.page.evaluate(COURSE_SCAN_JS)
        except Exception:
            return []
        return [c for c in courses if isinstance(c, dict) and c.get("title")]

    def scan_pending(self) -> list[Task]:
        """先查：只读地扫出「还没学完」的课程，不点、不提交。"""
        return [
            Task(label=f'{c["title"]}（进度 {c["percent"]}%）')
            for c in self.scan_courses()
            if int(c.get("percent") or 0) < COURSE_DONE_PERCENT
        ]

    # ------------------------------------------------------------ 后做

    def run(self) -> list[TaskResult]:
        deadline = time.time() + self.settings.budget_seconds

        if not self.go_my_courses():
            return [
                TaskResult(
                    "学习平台",
                    "failed",
                    "进不去「我的课程」列表（页面结构或加载异常），建议跑一次 INSPECT",
                )
            ]

        courses = self.scan_courses()
        pending = [c for c in courses if int(c.get("percent") or 0) < COURSE_DONE_PERCENT]

        if self.settings.dry_run:
            return [
                TaskResult(f'{c["title"]}（进度 {c["percent"]}%）', "skipped", "dry-run：只查不做")
                for c in pending
            ]

        if not pending:
            return [
                TaskResult(
                    "学习平台",
                    "skipped",
                    f"全部课程已完成（共 {len(courses)} 门），无需重复（幂等）",
                )
            ]

        results: list[TaskResult] = []
        for course in pending:
            if time.time() >= deadline:
                results.append(
                    TaskResult(
                        f'{course["title"]}（进度 {course["percent"]}%）',
                        "skipped",
                        "超出本次时间预算，留到下次运行补做",
                    )
                )
                continue
            try:
                results.append(self._do_course(course, deadline))
            except NotLoggedIn:
                raise
            except Exception as exc:  # 单点失败不污染整体结论
                results.append(
                    TaskResult(
                        f'{course["title"]}（进度 {course["percent"]}%）',
                        "failed",
                        f"{type(exc).__name__}: {exc}"[:200],
                    )
                )
                if not self.go_my_courses():  # 回到列表继续下一门
                    break
        return results

    def _do_course(self, course: dict, deadline: float) -> TaskResult:
        title = str(course["title"])
        label = f"{title}（进度 {course['percent']}%）"

        if not self._open_course(title):
            return TaskResult(label, "failed", "点不开课程卡片（文案可能被截断或结构变化）")

        lectures = self._list_lectures(stable=True)
        if not lectures:
            return TaskResult(label, "failed", "课程目录里没解析出讲次（建议跑 INSPECT 看真实 DOM）")

        pending_idx = [i for i, lec in enumerate(lectures) if not lec["done"]]
        notes: list[str] = []
        watched_total = 0.0
        finished = 0

        for index in pending_idx:
            if time.time() >= deadline:
                notes.append("时间预算用尽，剩余讲次留到下次")
                break
            if not self._click_lecture(index):
                notes.append(f"第{index + 1}讲点不开")
                continue
            self._wait_idle(3_000)

            watched, note = self._consume_lecture(deadline)
            watched_total += watched

            fresh = self._list_lectures()
            done_now = bool(fresh[index]["done"]) if index < len(fresh) else False
            if done_now:
                finished += 1
            elif note:
                # 备注里可能自带前导冒号（各模式返回风格不一），统一清掉免得出现「：：」
                notes.append(f"第{index + 1}讲：{note.lstrip('：: ')}")

        confirmed, percent_now = self._verify_course(title)
        detail_parts: list[str] = []
        if pending_idx:
            detail_parts.append(f"本次处理 {len(pending_idx)} 讲，确认完成 {finished} 讲")
        if watched_total > 0:
            detail_parts.append(f"观看 {watched_total / 60:.1f} 分钟")
        detail_parts.append(f"平台总进度 {percent_now if percent_now is not None else '?'}%")
        if notes:
            detail_parts.append("；".join(notes[:3]))

        if confirmed:
            return TaskResult(label, "done", "，".join(detail_parts) or "平台已确认完成", watched_total)
        if finished > 0 or watched_total > 0:
            return TaskResult(
                label,
                "done",
                "，".join(detail_parts) + "（总进度可能有结转延迟，下次运行自动复查）",
                watched_total,
            )
        return TaskResult(label, "failed", "，".join(detail_parts) or "未能推进任何讲次")

    def _open_course(self, title: str) -> bool:
        try:
            locator = self.page.get_by_text(title, exact=False)
            if locator.count() == 0:
                return False
            locator.first.click(timeout=10_000)
        except Exception:
            return False
        self._wait_idle(4_000)
        # 课程详情会出现「课程目录/继续学习/播放器」，三占其一即认为进来了
        body = self._body_text(3_000)
        return any(hint in body for hint in ("课程目录", "继续学习", "再次学习", "播放"))

    def _list_lectures(self, stable: bool = False) -> list[dict]:
        """解析课程目录。stable=True 时多等几轮，直到每条讲次都带状态
        （百分比或 开始/继续/再次学习），避免把已完成的讲误判成待学。"""
        items: list[dict] = []
        for attempt in range(4 if stable else 1):
            try:
                items = self.page.evaluate(LECTURE_LIST_JS)
            except Exception:
                items = []
            items = [i for i in items if isinstance(i, dict) and i.get("text")]
            if not stable or (
                items and all(i.get("hasStatus") for i in items)
            ):
                return items
            self.page.wait_for_timeout(2_500)
        return items

    def _click_lecture(self, index: int) -> bool:
        try:
            return bool(self.page.evaluate(LECTURE_CLICK_JS, {"index": index}))
        except Exception:
            return False

    # ------------------------------------------------------------ 播放策略（子类可覆盖）

    def _consume_lecture(self, deadline: float) -> tuple[float, str]:
        """把当前讲看完，返回 (观看秒数, 备注)。基类=真实播放到结束。"""
        video = self._next_video(set())
        if video is None:
            return 0.0, "页面上没有 video 元素"
        played = self._play_one(video, deadline)
        if played <= 0:
            return 0.0, "播放器没有动（可能被平台禁自动播放）"
        return played, ""

    # ------------------------------------------------------------ 视频工具（供各模式共用）

    def _all_videos(self) -> list:
        found = []
        for frame in self._frames():
            try:
                videos = frame.query_selector_all("video")
            except Exception:
                continue
            for video in videos:
                found.append(video)
        return found

    def _video_key(self, video) -> str:
        try:
            return video.evaluate(
                "(el) => { if (!el.__wbId) { el.__wbId = 'v' + Math.random().toString(36).slice(2); } return el.__wbId; }"
            )
        except Exception:
            return "unknown"

    def _video_state(self, video) -> dict:
        return video.evaluate(
            "(el) => ({ cur: el.currentTime || 0, dur: el.duration || 0,"
            " ended: !!el.ended, paused: !!el.paused, ready: el.readyState || 0 })"
        )

    def _next_video(self, played: set[str]):
        for video in self._all_videos():
            try:
                if not video.is_visible():
                    continue
            except Exception:
                continue
            if self._video_key(video) in played:
                continue
            return video
        return None

    def _play_one(self, video, deadline: float) -> float:
        """把单个 video 真实播放到结束，返回播放到的秒数。"""
        try:
            video.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass

        rate = max(1.0, float(self.settings.playback_rate))
        try:
            video.evaluate(
                "(el, rate) => { try { el.muted = true; } catch (e) {}"
                " try { el.playbackRate = rate; } catch (e) {}"
                " const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }",
                rate,
            )
        except Exception:
            try:
                video.evaluate("(el) => { const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }")
            except Exception:
                return 0.0

        last_time = -1.0
        stalled = 0
        current = 0.0

        while time.time() < deadline:
            self.page.wait_for_timeout(3_000)
            try:
                state = self._video_state(video)
            except Exception:
                break  # 元素被替换掉了，交给外层重新找

            current = float(state.get("cur") or 0.0)
            duration = float(state.get("dur") or 0.0)

            if state.get("ended") or (duration > 0 and duration - current < 1.0):
                return current
            if state.get("paused") or current <= last_time + 0.05:
                stalled += 1
                if stalled >= 4:  # 连续 12 秒没动静：当作卡死，别死等
                    break
                try:
                    video.evaluate(
                        "(el) => { const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }"
                    )
                except Exception:
                    break
            else:
                stalled = 0

            last_time = current

        return current

    # ------------------------------------------------------------ 完成校验

    def _verify_course(self, title: str) -> tuple[bool, int | None]:
        """回到「我的课程」回读这门课的总进度 —— 结论以平台自己的状态为准。"""
        percent: int | None = None
        try:
            if not self.go_my_courses():
                return False, percent
            for course in self.scan_courses():
                if course.get("title") == title:
                    percent = int(course.get("percent") or 0)
                    break
        except Exception:
            return False, percent
        return percent is not None and percent >= COURSE_DONE_PERCENT, percent

    # ------------------------------------------------------------ 勘察模式

    def inspect(self) -> dict:
        """把真实 DOM / 截图 / 课程与讲次结构全部落盘（第一次接页面必跑）。"""
        report: dict[str, Any] = {"entry_url": self.page.url}

        def snap(name: str) -> None:
            self.page.screenshot(path=str(self.settings.artifact_dir / f"{name}.png"), full_page=True)
            (self.settings.artifact_dir / f"{name}.dom.html").write_text(
                self.page.content(), encoding="utf-8"
            )

        snap("entry")
        report["entry_title"] = self.page.title()

        if self.go_my_courses():
            snap("my_courses")
            report["courses"] = self.scan_courses()
            pending = [c for c in report["courses"] if int(c.get("percent") or 0) < COURSE_DONE_PERCENT]
            if pending:
                first = pending[0]
                if self._open_course(str(first["title"])):
                    self.page.wait_for_timeout(3_000)
                    snap("course_detail")
                    report["first_pending_course"] = first
                    report["lectures"] = self._list_lectures()
                    report["videos"] = [
                        v.evaluate("(el) => ({ src: (el.currentSrc || el.src || '').slice(0, 120), dur: el.duration || 0, t: el.currentTime || 0 })")
                        for v in self._all_videos()
                    ]
        else:
            report["courses"] = []
            report["note"] = "进不去「我的课程」，请看 entry.png"

        report["matched_pending"] = [t.label for t in self.scan_pending()] if report.get("courses") else []
        (self.settings.artifact_dir / "inspect.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report
