"""Capture the business-gateway responses on the course detail page.

The lecture list and courseware resources come from batchInvokeAction.do.
No HTTP-level failures were seen, so the error must live in the response
bodies. This script prints every gateway response's status + body head,
plus the full failure reason of any requestfailed event. Read-only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from src.browser import browser_page  # noqa: E402
from src.seek_adapter import SeekLearnAdapter  # noqa: E402
from src.settings import load_settings  # noqa: E402

GATEWAY_MARKS = ("batchInvokeAction", "customEvent", "attachment", ".m3u8", ".ts", "courseware")


def main() -> None:
    settings = load_settings()
    seen: list[str] = []

    with browser_page(settings) as page:
        def on_response(resp) -> None:
            url = resp.url
            if not any(m in url for m in GATEWAY_MARKS):
                return
            try:
                body = resp.text()[:300].replace("\n", " ")
            except Exception as exc:  # body may be gone by the time we read it
                body = f"<body unreadable: {exc}>"
            line = f"HTTP {resp.status}  {url[:120]}\n        body: {body}"
            seen.append(line)

        def on_failed(req) -> None:
            if not any(m in req.url for m in GATEWAY_MARKS):
                return
            seen.append(f"FAILED {req.failure}  {req.url[:150]}")

        page.on("response", on_response)
        page.on("requestfailed", on_failed)

        adapter = SeekLearnAdapter(page, settings)
        adapter.open_entry()
        report = adapter.inspect()
        print(f"[result] 讲次解析数 = {len(report.get('lectures', []))}")
        page.wait_for_timeout(3000)

    print(f"\n=== 网关相关请求 {len(seen)} 条 ===")
    for line in seen:
        print("  " + line)


if __name__ == "__main__":
    main()
