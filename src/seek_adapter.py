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

⚠️ 2026-10-01 实测发现的坑（尾段必须"密集轮询 + 认复位"，否则整门课都刷不动）：

    平台播放器在视频播到结尾后会**立刻把 currentTime 复位成 0**，
    同时 duration 变成 NaN、readyState/networkState 归 0（等于把 source 卸了）。
    这中间只隔约 1 秒。

    而原来的实现每 **3 秒**才轮询一次 —— 视频恰好在这个窗口里
    「播完 → 复位」，轮询看到的永远是复位后的 `cur=0`，
    于是把"已经播完"误判成"播放器卡死"，直接放弃。

    后果：每一条讲次都停在 `duration - tail + 1` 秒（实测 75 秒的讲停在 68 秒 = 91%），
    永远到不了 100% → 平台不计入完成 → **课程总进度纹丝不动（一直是 0%）**，
    看起来就像"seek 完全没生效"。实际上 seek 一直是好的。

    修法见 `_finish_tail()`：250ms 密集轮询 + 记录峰值 + 把
    「先冲到结尾附近、随后突然归零」直接判为已完成。
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

# ---- 尾段收尾用的 JS ----
# 只读状态：密集轮询要够轻，字段越少越好
TAIL_STATE_JS = """(el) => ({
  cur: el.currentTime || 0,
  dur: el.duration || 0,
  ended: !!el.ended,
  paused: !!el.paused,
  ready: el.readyState || 0
})"""

# 播放尾段：按下 play 并保持静音，不做任何跳转
TAIL_PLAY_JS = """(el) => {
  try { el.muted = true; } catch (e) {}
  const p = el.play(); if (p && p.catch) { p.catch(() => {}); }
  return el.currentTime || 0;
}"""

# 轮询间隔：必须远小于"播完→复位"的窗口（实测约 1 秒），
# 否则会像旧实现那样刚好错过 ended 的那一刻。
TAIL_POLL_MS = 250

# 距结尾多少秒算"已经冲到末尾"
TAIL_NEAR_END = 1.5

# 尾段收尾最多花多久（正常只需要 tail 秒，留足缓冲后仍远小于一讲的时长）
TAIL_BUDGET_SECONDS = 120.0


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
            peak, ok = self._finish_tail(video, min(deadline, time.time() + TAIL_BUDGET_SECONDS))
            best = max(current, peak, duration if ok else 0.0)
            return best, f"（已在尾段，直接收尾{'完成' if ok else '未确认'}）{note}"

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

        # 尾段自然播完 —— 平台自己的「播完」逻辑要靠这一段触发。
        # ⚠️ 必须走 _finish_tail：这里曾经用基类的 _play_one（3 秒轮询），
        #    会因为播放器「播完即复位成 0」而刚好错过 ended，把讲次永远卡在 91%。
        try:
            peak, ok = self._finish_tail(video, min(deadline, time.time() + TAIL_BUDGET_SECONDS))
        except Exception:
            peak, ok = current, False
        best = max(current, peak, duration if ok else 0.0)
        return best, f"（尾段自然播完{'完成' if ok else '未确认'}）{note}"

    def _finish_tail(self, video, deadline: float) -> tuple[float, bool]:
        """把尾段真正播完，返回 (观测到的最大播放位置, 是否确认播完)。

        为什么不能用简单的「每隔几秒看一眼」：
        平台播放器播到结尾后会**立刻把 currentTime 复位成 0**，同时 duration 变 NaN、
        readyState 归 0 —— 整段过程只持续约 1 秒。按 3 秒的节奏轮询，
        看到的永远是复位后的 0，于是"已完成"被判成"卡死"。

        所以这里同时认三种「完成」信号：
          1. `ended === true`（最干净，密集轮询基本都能抓到）；
          2. 已经冲到结尾附近（dur - cur <= 1.5）后 currentTime **突然掉回 0** —— 复位；
          3. video 元素直接读不到了（平台重建了播放器 / 换了页）。
        另外全程记录峰值，避免把复位后的 0 当成真实进度返回给上层。
        """
        try:
            video.evaluate(TAIL_PLAY_JS)
        except Exception:
            return 0.0, False

        peak = 0.0
        armed = False      # 是否已经冲到结尾附近（之后才能把"归零"当复位）
        last = -1.0
        last_poke = time.time()

        while time.time() < deadline:
            self.page.wait_for_timeout(TAIL_POLL_MS)
            try:
                state = video.evaluate(TAIL_STATE_JS)
            except Exception:
                # 元素被换掉：平台播完重建播放器时会这样，按已完成处理
                return peak, peak > 0

            cur = float(state.get("cur") or 0.0)
            dur = float(state.get("dur") or 0.0)

            if state.get("ended"):
                return max(peak, cur, dur), True

            if cur > peak:
                peak = cur

            if dur > 0 and dur - cur <= TAIL_NEAR_END:
                armed = True
                continue

            # 复位：刚还在结尾附近，忽然回到 0
            if armed and cur < 1.0:
                return max(peak, dur), True

            # 没往前走（暂停 / 缓冲）：每 2 秒重新推一把 play()，别死等
            if cur <= last + 0.01 and time.time() - last_poke >= 2.0:
                try:
                    video.evaluate(TAIL_PLAY_JS)
                except Exception:
                    return peak, peak > 0
                last_poke = time.time()
            last = cur

        return peak, False

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
