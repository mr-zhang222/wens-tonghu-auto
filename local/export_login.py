"""本机刷新脚本：把同呼的登录态导出成一段 base64，喂给 GitHub secret。

为什么必须有这一步：
云端 runner 没有你的本机登录态，而你的登录方式是「手机号 + 短信验证码」——
短信码没法自动化。所以采用原文方案三的做法：
**本机导出凭据 → 注入仓库 secret → Actions 从 secret 读取**。
明文 token 既不进仓库，也不进日志。

用法（在项目根目录）：
    python local/export_login.py              # 交互登录：登录成功即自动导出（推荐）
    python local/export_login.py --manual     # 交互登录：登录后回终端按回车
    python local/export_login.py --refresh    # 免登录：用已有登录态打开门户，补齐 cookie 后重新导出

自动模式下每 2 秒检测一次会话票据（KERPSESSIONID*），
**你在浏览器里登录完、看到同呼首页，脚本就会自己收工**，不需要回终端敲回车。
超时时间默认 900 秒，可用 --timeout 调整。

导出产物在 login_state/：
    storage_state.json     ← 完整登录态
    storage_state.gz.b64   ← ★ 复制它，填进 secret WENS_STORAGE_STATE（gzip 压缩，约 18 KB）
    storage_state.b64      ← 未压缩版（约 139 KB，超过 GitHub secret 的 48 KB 上限，仅留档）
    cookie.txt             ← 备用的纯 Cookie 字符串（给 WENS_COOKIE 用）
登录失败时产物写到 login_state/failed/，**绝不会覆盖上一次可用的凭据**。

关于那个 .gz.b64：GitHub 对单个 secret 有 48 KB 硬上限，而 Playwright 导出的
storage_state 里 95% 都是前端页面缓存（irep-m-*），原始 base64 约 139 KB 会被直接拒收。
gzip 后只剩约 18 KB，无损，服务端（src/settings.py）会自动识别并解压。
"""

from __future__ import annotations

import argparse
import base64
import gzip
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.settings import DEFAULT_ENTRY_URL  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "login_state"
SESSION_COOKIE_PREFIXES = ("KERPSESSIONID", "IsolatorSpan")
# 苍穹/门户的会话票据与辅助 cookie（2026-09 实测抓包确认）
SESSION_PREFIX_CHECKS = ("KERPSESSIONID", "IsolatorSpan", "JSESSIONID", "kdservice-sessionid")
AUX_COOKIE_CHECKS = ("at", "uuid", "gl", "accessToken")


def _session_ok(cookies: list[dict]) -> bool:
    """拿到主会话票据才算真的登录成功（辅助 cookie 不计入判定）。"""
    return any(c["name"].startswith("KERPSESSIONID") for c in cookies)


def _launch(playwright, headless: bool):
    """与 src/browser.py 同一套降级顺序：Chrome → 系统 Edge → 自带 Chromium。"""
    for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
        try:
            return playwright.chromium.launch(headless=headless, **kwargs)
        except Exception:  # noqa: BLE001 - 逐个候选降级
            continue
    return None


def _dump(context) -> bool:
    """导出登录态并自检。返回是否真正登录成功。"""
    cookies = context.cookies()
    logged_in = _session_ok(cookies)

    target_dir = OUT_DIR if logged_in else OUT_DIR / "failed"
    target_dir.mkdir(parents=True, exist_ok=True)

    state_path = target_dir / "storage_state.json"
    context.storage_state(path=str(state_path))

    raw = state_path.read_bytes()
    plain_b64 = base64.b64encode(raw).decode("ascii")
    (target_dir / "storage_state.b64").write_text(plain_b64, encoding="utf-8")

    # GitHub secret 硬上限 48 KB，原始 base64 远超 → 压成 gzip 再 base64（无损）
    gz_b64 = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
    (target_dir / "storage_state.gz.b64").write_text(gz_b64, encoding="utf-8")

    cookie_line = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    (target_dir / "cookie.txt").write_text(cookie_line, encoding="utf-8")

    print()
    if logged_in:
        gz_path = target_dir / "storage_state.gz.b64"
        print(f"已生成：{state_path}")
        print(f"已生成：{gz_path}")
        print(f"        ↑ ★ 复制这个文件的全文，填到 secret WENS_STORAGE_STATE（{len(gz_b64) / 1024:.1f} KB）")
        print(f"已生成：{target_dir / 'storage_state.b64'}（{len(plain_b64) / 1024:.1f} KB，超 GitHub 48 KB 上限，仅留档）")
        print(f"已生成：{target_dir / 'cookie.txt'}          ← 备用，可填到 secret WENS_COOKIE")
        print()
        print("!! 这些文件等同于你的登录态，别贴到任何公开地方、别提交进仓库 !!")
    else:
        print("⚠️ 没拿到 KERPSESSIONID* —— 说明还没真正登录成功。")
        print(f"   为免覆盖上一次可用的凭据，这次的产物写到：{target_dir}")
        print("   请重新运行本脚本，并在浏览器里登录到能看见学习平台的入口。")

    print()
    print(f"本次拿到 {len(cookies)} 个 cookie，关键会话 cookie 自检：")
    for prefix in SESSION_PREFIX_CHECKS:
        hits = [c for c in cookies if c["name"].startswith(prefix)]
        detail = f"  (域名 {hits[0].get('domain', '?')})" if hits else ""
        print(f"  - {prefix}* : {'✅ 有' if hits else '— 无'}{detail}")
    for exact in AUX_COOKIE_CHECKS:
        hit = next((c for c in cookies if c["name"] == exact), None)
        print(f"  - {exact} : {'✅ 有' if hit else '— 无'}")
    return logged_in


def _open_entry(context) -> None:
    """打开入口页并等它把门户侧 cookie（at/uuid/gl 等）下发完。"""
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(DEFAULT_ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(6_000)
    except Exception as exc:  # noqa: BLE001 - 打不开也照样导出已有 cookie
        print(f"（入口页加载异常，继续导出已有 cookie：{str(exc)[:80]}）")


def _run_interactive(playwright, timeout: int, manual: bool) -> int:
    browser = _launch(playwright, headless=False)
    if browser is None:
        print("无法启动浏览器。请安装 Google Chrome 或 Edge，或执行 python -m playwright install chromium")
        return 1

    context = browser.new_context(
        viewport={"width": 414, "height": 896},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    page = context.new_page()
    page.goto(DEFAULT_ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

    print("=" * 60)
    print("浏览器已打开。请在弹出的窗口里用【手机号 + 短信验证码】登录同呼，")
    print("一直点到能看见学习平台的入口为止。")
    print("登录完成后回到这个终端按回车。" if manual else "登录成功后脚本会自动收工，不需要回终端敲回车。")
    print("=" * 60)

    if manual:
        try:
            input("登录完成后按回车继续...")
        except EOFError:
            print("（终端不可交互，自动切换为等待登录成功的检测模式）")
            manual = False

    if not manual:
        deadline = time.monotonic() + max(30, timeout)
        started = time.monotonic()
        last_note = 0.0
        while True:
            if _session_ok(context.cookies()):
                print()
                print("✅ 检测到会话票据，正在收尾导出...")
                break
            if time.monotonic() > deadline:
                print()
                print(f"⚠️ 等待 {timeout} 秒仍未检测到会话票据。")
                print("   浏览器窗口不会自动关闭，你可以继续登录；若已关闭，请重新运行本脚本。")
                break
            waited = int(time.monotonic() - started)
            if waited - last_note >= 15:
                last_note = waited
                print(f"   等待登录中… 已等待 {waited} 秒（{page.url[:60]}）")
            time.sleep(2)

    # 再打开一次入口页，让门户把 at/uuid/gl 这些辅助 cookie 一并下发，抓全一点
    _open_entry(context)
    logged_in = _dump(context)

    context.close()
    browser.close()
    return 0 if logged_in else 1


def _run_refresh(playwright, state_path: Path) -> int:
    """免登录模式：拿已有登录态打开门户，补齐 cookie 后重新导出。"""
    if not state_path.exists():
        print(f"找不到 {state_path}，请先跑一次交互登录：python local/export_login.py")
        return 1

    browser = _launch(playwright, headless=True)
    if browser is None:
        print("无法启动浏览器。请安装 Google Chrome 或 Edge，或执行 python -m playwright install chromium")
        return 1

    context = browser.new_context(
        storage_state=str(state_path),
        viewport={"width": 414, "height": 896},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    _open_entry(context)

    if "login" in context.pages[0].url:
        print("登录态已失效（被重定向到登录页），请跑一次交互登录：python local/export_login.py")
        context.close()
        browser.close()
        return 1

    logged_in = _dump(context)
    context.close()
    browser.close()
    return 0 if logged_in else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="导出同呼登录态，喂给 GitHub secret")
    parser.add_argument("--manual", action="store_true", help="登录后手动回终端按回车")
    parser.add_argument("--refresh", action="store_true", help="免登录：用已有登录态补齐 cookie 后重新导出")
    parser.add_argument("--timeout", type=int, default=900, help="等待登录的最长秒数，默认 900")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright，请先执行：pip install -r requirements.txt")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        if args.refresh:
            code = _run_refresh(playwright, OUT_DIR / "storage_state.json")
        else:
            code = _run_interactive(playwright, args.timeout, args.manual)

    print()
    if code == 0:
        print("下一步：把 login_state/storage_state.gz.b64 的全文填到仓库 secret WENS_STORAGE_STATE。")
    print("提示：登录态会过期（通常几天到两周）。建议每周重跑一次本脚本并覆盖 secret。")
    return code


if __name__ == "__main__":
    sys.exit(main())
