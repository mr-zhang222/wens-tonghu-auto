"""拽进度条模式（FAST_MODE=1 且 FAST_METHOD=seek，默认）。

为什么不直接重放请求：
2026-09 抓包显示每个业务请求都带这两个头 ——

    signature:     <sha256 十六进制>-<数字>__length__<数字>
    kd-csrf-token: <32 位令牌>

`signature` 是前端拿私钥对请求算出来的，我们一改 currentTime 就等于改了请求体，
签名立刻失效。逆向它的性价比很低：私钥在打包 JS 里，服务端随时能换。

所以换个思路：**不解签名，只拽进度条。**
我们只把 video 元素的 currentTime 往前推，上报请求（saveView/saveHour）由页面自己发出，
签名、CSRF、userId 全部天然合法 —— 我们全程不碰网络层。

尾段（默认最后 8 秒）不跳，让它自然播完，保证平台自己的「播完」逻辑能触发；
讲次是否记上由 learn_adapter 回读目录里的「再次学习/100%」标记判断。
"""

from __future__ import annotations

import time

from .learn_adapter import LearnAdapter

# 静音 + 设倍速 + 跳到 target，然后继续播放
STEER_JS = """(el, data) => {
  try { el.muted = true; } catch (e) {}
  try { el.playbackRate = data.rate; } catch (e) {}
  if (typeof data.target === 'number' && isFinite(data.target)) {
    try { el.currentTime = data.target; } catch (e) {}
  }
  const p = el.play(); if (p && p.catch) { p.catch(() => {}); }
  return { cur: el.currentTime || 0, dur: el.duration || 0,
           ended: !!el.ended, paused: !!el.paused, ready: el.readyState || 0 };
}"""


class SeekLearnAdapter(LearnAdapter):
    """真实播放器在手，只负责把进度往前拽 —— 最快的稳妥解。"""

    def _consume_lecture(self, deadline: float) -> tuple[float, str]:  # type: ignore[override]
        video = self._next_video(set())
        if video is None:
            return 0.0, "页面上没有 video 元素"

        duration = self._wait_duration(video, deadline)
        note = ""
        if duration is None:
            # 拿不到真实时长就按兜底值往前拽，反正尾段会自然播到真结束
            duration = float(self.settings.max_video_minutes * 60)
            note = "（未取到视频时长，按兜底值处理）"

        try:
            current = float(self._video_state(video).get("cur") or 0.0)
        except Exception:
            return 0.0, "播放器元素不可用"

        tail = max(3.0, self.settings.final_tail_seconds)

        # 已经在尾段（比如平台自动续播到了结尾附近）：直接自然播完
        if duration - current <= tail:
            played = self._play_one(video, min(deadline, time.time() + 900))
            return max(current, played), "（已在尾段，直接自然播完）"

        step = max(1.0, self.settings.seek_step_seconds)
        interval = max(0.2, self.settings.seek_interval_ms / 1000.0)
        rate = max(1.0, self.settings.playback_rate)

        last = -1.0
        stalls = 0

        while time.time() < deadline:
            target = min(duration - tail, current + step)
            try:
                state = video.evaluate(STEER_JS, {"target": target, "rate": rate})
            except Exception:
                return current, "：播放器元素已失效（可能换页了）"

            current = float(state.get("cur") or 0.0)
            live_duration = float(state.get("dur") or 0.0)
            if live_duration > 0:
                duration = live_duration

            if state.get("ended") or duration - current <= tail:
                break

            # 卡死看门狗：连续几轮 currentTime 不动 → 平台可能禁了跳转
            if current <= last + 0.05:
                stalls += 1
                if stalls >= 6:
                    return current, f"：播放器不响应跳转{note}"
            else:
                stalls = 0
            last = current

            self.page.wait_for_timeout(int(interval * 1000))
        else:
            return current, f"：超出时间预算{note}"

        # 尾段自然播完 —— 平台自己的「播完」逻辑要靠这一段触发
        try:
            played = self._play_one(video, min(deadline, time.time() + 900))
        except Exception:
            played = current
        return max(current, played), f"（尾段自然播完）{note}"

    def _wait_duration(self, video, deadline: float) -> float | None:
        end = min(deadline, time.time() + 25.0)
        while time.time() < end:
            try:
                value = video.evaluate("(el) => el.duration")
            except Exception:
                return None
            if value and value == value and value > 0:  # 过滤 NaN
                return float(value)
            self.page.wait_for_timeout(800)
        return None
