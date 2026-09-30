"""One-off: verify check_more.classify() against representative round outputs."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "local"))
from check_more import classify  # noqa: E402

CASES = {
    "预算用尽": "14. ⏭️ 测试课（进度 0%） — 超出本次时间预算，留到下次运行补做",
    "无待学": "✅ 今日无待学任务，已完成，无需重复",
    "有完成": "✅ 完成：待学 5 项 → 完成 3，跳过 2，失败 0",
    "有完成+预算用尽": "✅ 完成：待学 5 项 → 完成 2，跳过 3，失败 0\n…超出本次时间预算",
    "全部失败(真实)": "⚠️ 有失败：待学 20 项 → 完成 0，跳过 0，失败 20",
    "全部跳过": "⏭️ 全部跳过：待学 4 项 → 完成 0，跳过 4，失败 0",
    "空输出": "",
}

expect = {
    "预算用尽": "MORE",
    "无待学": "DONE",
    "有完成": "DONE",
    "有完成+预算用尽": "MORE",
    "全部失败(真实)": "FAILED",
    "全部跳过": "FAILED",
    "空输出": "DONE",
}

bad = 0
for name, line in CASES.items():
    got = classify(line)
    ok = got == expect[name]
    bad += not ok
    print(f"{'OK ' if ok else 'BAD'} {name:12s} -> {got} (期望 {expect[name]})")
print("全部通过" if bad == 0 else f"{bad} 个不通过")
