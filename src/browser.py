"""浏览器会话：登录态注入 + 真机浏览器优先。

为什么要「真机浏览器优先」：
Playwright 自带的 Chromium 是开源构建，**不含 H.264 / AAC 等专有编解码器**。
国内学习平台的视频绝大多数是 H.264 MP4 / HLS，用自带 Chromium 跑会出现
「video 元素在、但 currentTime 永远不动」的假死现象。

所以按 **Chrome → 系统 Edge → 自带 Chromium** 的顺序降级：
前两者都是带编解码器的正式发行版（Edge 与 Chrome 同源，一样能解 H.264）。
GitHub 的 ubuntu runner 预装了 Google Chrome，云端第一项即中；
本机若只有 Edge，也能直接跑，不必额外下载浏览器构建。
"""

from __future__ import annotations

import contextlib
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .settings import Settings

# 按手机视口打开 —— mobile.html 是移动端门户，桌面 UA 容易踩到布局差异
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)

LAUNCH_ARGS = [
    "--autoplay-policy=no-user-gesture-required",  # 不点也能自动播放
    "--mute-audio",                                # 静音，省得云端音频设备报错
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


def _launch(playwright, settings: Settings) -> Browser:
    last_error: Exception | None = None
    # 顺序：真机 Chrome → 系统 Edge（同为 Chromium 内核、自带 H.264）→ 自带 Chromium。
    # 云端 runner 预装 Chrome，第一项即中；本机没装 Chrome 也能用 Edge，不必下载浏览器。
    for channel in ("chrome", "msedge", None):
        kwargs: dict = {"headless": settings.headless, "args": LAUNCH_ARGS}
        if channel:
            kwargs["channel"] = channel
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 逐个候选降级
            last_error = exc
    raise RuntimeError(f"无法启动浏览器（请确认已执行 playwright install）：{last_error}")


def parse_cookie_header(raw: str, url: str = "https://cq.wens.com.cn") -> list[dict]:
    """把 DevTools 里复制出来的 `a=1; b=2` 转成 Playwright 的 cookie 列表。"""
    cookies: list[dict] = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "url": url})
    return cookies


def _new_context(browser: Browser, settings: Settings) -> BrowserContext:
    storage_state = (
        str(settings.storage_state_path) if settings.storage_state_path.exists() else None
    )
    context = browser.new_context(
        storage_state=storage_state,
        viewport={"width": 414, "height": 896},
        user_agent=MOBILE_UA,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        is_mobile=True,
        has_touch=True,
    )
    if settings.cookie_header:
        cookies = parse_cookie_header(settings.cookie_header)
        if cookies:
            context.add_cookies(cookies)
    return context


@contextlib.contextmanager
def browser_page(settings: Settings) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = _launch(playwright, settings)
        context = _new_context(browser, settings)
        page = context.new_page()
        page.set_default_timeout(45_000)
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                context.close()
            with contextlib.suppress(Exception):
                browser.close()
