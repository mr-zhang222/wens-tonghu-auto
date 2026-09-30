"""接口直刷模式（FAST_MODE=1 时启用）。

思路 —— 不猜接口，全部以实际请求为准：

1. 用真实浏览器把课件页打开，让页面自己吐一次心跳；
   我们在网络层把它抓下来当「模板」：URL、表单字段、pageId、事件名全是真的。
2. 抓到模板后立刻暂停视频，改为在页面上下文里直接 POST 同一个接口，
   只把 currentTime 往前推，把进度从当前位置一路推到视频时长。
3. 推完刷新验证；平台没标「已完成」（说明服务端还校验了真实时长）就退回真实播放兜底。

已知请求结构（2026-09 用户实测抓包）：
    POST /ierp/.../batchInvokeAction.do?appId=nbj_portal&f=nbj_user_mob_course&ac=customEvent
    pageId=<课件页会话ID>&appId=nbj_portal&params=[{"key":"","methodName":"customEvent",
        "args":["nbj_play","saveView","{\\"currentTime\\":280.272528,\\"type\\":\\"VIDEO\\"}"],
        "postData":[]}]
    —— 服务端靠 pageId 反查当前课件，进度字段是 args[2] 里的 currentTime。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote

from .learn_adapter import LearnAdapter

HEARTBEAT_URL_HINT = "batchInvokeAction.do"
HEARTBEAT_AC_HINT = "ac=customEvent"

FETCH_JS = """(data) => fetch(data.url, {
  method: 'POST',
  headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
  body: data.body,
  credentials: 'include'
}).then(r => r.text().then(t => ({status: r.status, text: t.slice(0, 400)})))
  .catch(e => ({status: 0, text: String(e)}))"""

PLAY_JS = "(el) => { const p = el.play(); if (p && p.catch) { p.catch(() => {}); } }"
PAUSE_JS = "(el) => { try { el.pause(); } catch (e) {} }"


@dataclass
class Heartbeat:
    url: str
    page_id: str
    app_id: str
    key: str
    method_name: str
    event_key: str
    action: str
    extra: dict = field(default_factory=dict)
    post_data: list = field(default_factory=list)


def parse_heartbeat(url: str, post_data: str | None) -> Heartbeat | None:
    """把抓到的心跳请求体解成模板。解析失败返回 None，调用方走真实播放兜底。"""
    if not post_data or "params" not in post_data:
        return None
    try:
        form = parse_qs(post_data, keep_blank_values=True)
        items = json.loads(form.get("params", [""])[0] or "null")
        if not isinstance(items, list) or not items:
            return None
        item = items[0]
        args = item.get("args") or []
        if len(args) < 3:
            return None
        extra = json.loads(args[2]) if isinstance(args[2], str) else dict(args[2])
        if not isinstance(extra, dict):
            return None
        return Heartbeat(
            url=url,
            page_id=form.get("pageId", [""])[0],
            app_id=form.get("appId", [""])[0],
            key=str(item.get("key", "")),
            method_name=str(item.get("methodName", "customEvent")),
            event_key=str(args[0]),
            action=str(args[1]),
            extra=extra,
            post_data=item.get("postData") or [],
        )
    except Exception:
        return None


def build_body(hb: Heartbeat, current_time: float) -> str:
    """按模板重建请求体：只改 currentTime，其余字段原样保留。"""
    payload = dict(hb.extra or {})
    payload["currentTime"] = round(float(current_time), 6)
    args = [
        hb.event_key,
        hb.action,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    ]
    item = {
        "key": hb.key,
        "methodName": hb.method_name,
        "args": args,
        "postData": hb.post_data,
    }
    params = json.dumps([item], separators=(",", ":"), ensure_ascii=False)
    return (
        f"pageId={quote(str(hb.page_id))}"
        f"&appId={quote(str(hb.app_id))}"
        f"&params={quote(params)}"
    )


class FastLearnAdapter(LearnAdapter):
    """在真实打开的课件页里抢跑：先抓一条真心跳，再原地快进上报。"""

    # ------------------------------------------------------------ 主流程

    def _consume_lecture(self, deadline: float) -> tuple[float, str]:  # type: ignore[override]
        hb = self._sniff_heartbeat(deadline)
        if hb is None:
            watched, note = super()._consume_lecture(deadline)
            if watched > 0:
                return watched, f"没抓到心跳，退回真实播放（{note}）"
            return 0.0, "没抓到心跳，页面上也没有可播放的 video"

        duration = self._video_duration() or self.settings.max_video_minutes * 60.0
        start = float((hb.extra or {}).get("currentTime") or 0.0)
        sent, first_resp = self._push_progress(hb, start, duration, deadline)

        if sent > 0:
            # 是否真的记上，由外层回读讲次的「再次学习/100%」标记判断
            return start, f"直刷上报 {sent} 次，首包响应 {first_resp}"

        # 服务端大概率还校验了真实观看时长/签名 → 退回真实播放兜底
        watched, note = super()._consume_lecture(deadline)
        if watched > 0:
            return watched, f"直刷未生效（首包 {first_resp}），退回真实播放（{note}）"
        return 0.0, f"直刷未确认且无法播放（上报 0 次，{first_resp}）"

    # ------------------------------------------------------------ 抓模板

    def _sniff_heartbeat(self, deadline: float) -> Heartbeat | None:
        holder: dict[str, tuple[str, str]] = {}

        def on_request(request) -> None:
            url = request.url
            if HEARTBEAT_URL_HINT in url and HEARTBEAT_AC_HINT in url:
                post_data = request.post_data
                if post_data and "params" in post_data:
                    holder.setdefault("hit", (url, post_data))

        self.page.on("request", on_request)
        try:
            end = min(deadline, time.time() + 45.0)
            while time.time() < end and "hit" not in holder:
                video = self._next_video(set())
                if video is not None:
                    try:
                        video.evaluate(PLAY_JS)
                    except Exception:
                        pass
                self.page.wait_for_timeout(600)
            # 无论抓没抓到，都把视频停了，别让它真播下去
            for _frame, video in self._all_videos():
                try:
                    video.evaluate(PAUSE_JS)
                except Exception:
                    pass
        finally:
            try:
                self.page.remove_listener("request", on_request)
            except Exception:
                pass

        if "hit" not in holder:
            return None
        url, post_data = holder["hit"]
        return parse_heartbeat(url, post_data)

    # ------------------------------------------------------------ 快进上报

    def _video_duration(self) -> float | None:
        for _frame, video in self._all_videos():
            try:
                value = video.evaluate("(el) => el.duration")
                if value and value == value and value > 0:  # 过滤 NaN
                    return float(value)
            except Exception:
                continue
        return None

    def _push_progress(
        self, hb: Heartbeat, start: float, duration: float, deadline: float
    ) -> tuple[int, str]:
        step = max(1.0, self.settings.heartbeat_step_seconds)
        delay = max(0.0, self.settings.heartbeat_delay_ms) / 1000.0
        # 墙钟预算 = 视频时长 / 压缩倍率，既快又不至于一秒打几百发
        wall = min(900.0, max(30.0, duration / max(1.0, self.settings.fast_rate)))
        cap = time.time() + wall

        sent = 0
        first_resp = "-"
        position = max(0.0, start)

        while position < duration - 0.5 and time.time() < deadline and time.time() < cap:
            position = min(duration, position + step)
            body = build_body(hb, position)
            try:
                response = self.page.evaluate(FETCH_JS, {"url": hb.url, "body": body})
            except Exception:
                break
            sent += 1
            if sent == 1:
                first_resp = json.dumps(
                    {
                        "status": (response or {}).get("status"),
                        "text": ((response or {}).get("text") or "")[:120],
                    },
                    ensure_ascii=False,
                )
            if not response or response.get("status") != 200:
                break
            if delay:
                self.page.wait_for_timeout(int(delay * 1000))
        return sent, first_resp
