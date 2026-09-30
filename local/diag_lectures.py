"""临时诊断：看课程目录里「第 N 讲是否已完成」到底靠什么标记，供 _list_lectures 修正。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from src.learn_adapter import LECTURE_LIST_JS, LearnAdapter
from src.settings import Settings, load_settings

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "probe"


def main() -> int:
    settings: Settings = load_settings()
    settings.headless = True
    with sync_playwright() as p:
        browser = None
        for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                browser = p.chromium.launch(headless=True, **kwargs)
                break
            except Exception:
                continue
        context = browser.new_context(
            storage_state=str(ROOT / "login_state" / "storage_state.json"),
            viewport={"width": 414, "height": 896},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.set_default_timeout(30_000)
        adapter = LearnAdapter(page, settings)

        adapter._goto_entry()
        print("我的课程列表可达：", adapter.go_my_courses())
        courses = adapter.scan_courses()
        print("课程卡片解析：", json.dumps(courses, ensure_ascii=False))

        if courses:
            title = courses[0]["title"]
            print("打开课程：", title, "→", adapter._open_course(title))
            page.wait_for_timeout(4_000)
            page.screenshot(path=str(OUT / "diag_course.png"), full_page=True)

            print("\n=== 当前 LECTURE_LIST_JS 解析结果 ===")
            for i, lec in enumerate(adapter._list_lectures(), 1):
                print(f"{i:2d}. done={lec['done']}  {lec['text'][:90]}")

            print("\n=== 目录条目原始 HTML 片段（看完成状态的真实标记）===")
            htmls = page.evaluate(
                r"""() => {
                  const out = [];
                  const seen = new Set();
                  for (const el of document.querySelectorAll('div,li')) {
                    const raw = (el.innerText || '').trim();
                    if (!raw.startsWith('视频')) continue;
                    const t = raw.replace(/\s+/g, ' ').trim();
                    const durs = t.match(/\d{1,2}:\d{2}(:\d{2})?/g) || [];
                    if (durs.length !== 1 || t.length > 150 || seen.has(t)) continue;
                    seen.add(t);
                    out.push({ text: t.slice(0, 70), html: el.outerHTML.slice(0, 900) });
                  }
                  return out;
                }"""
            )
            for i, item in enumerate(htmls, 1):
                print(f"\n--- 第 {i} 条 ---\n{item['text']}\n{item['html']}")
            (OUT / "diag_lectures_html.json").write_text(
                json.dumps(htmls, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        context.close()
        browser.close()
    print(f"\n产物：{OUT / 'diag_course.png'} / diag_lectures_html.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
