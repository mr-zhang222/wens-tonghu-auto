#!/usr/bin/env bash
# 把本机导出的登录态推送到 GitHub 仓库 secret（用于每周刷新）。
#
# 前提：已装 GitHub CLI（gh）并登录过一次。
#   Windows 安装：winget install --id GitHub.cli
#   登录：        gh auth login
#
# 用法（在项目根目录）：
#   ./local/push_secret.sh                 # 用导出好的 login_state/storage_state.gz.b64
#   ./local/push_secret.sh --re-export     # 先重新跑一次导出脚本，再推送
#
# 为什么用 gh 而不是网页粘贴：那串 base64 有 18 KB，网页表单里手动复制极易截断，
# 而截断后的报错（"不是合法 base64"）会让你误以为登录态过期，白折腾一轮。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STATE_FILE="login_state/storage_state.gz.b64"
PY="${PYTHON:-python}"

if [[ "${1:-}" == "--re-export" ]]; then
  echo "==> 重新导出登录态（会弹出浏览器，请用手机号+短信登录）"
  "$PY" local/export_login.py
fi

if [[ ! -s "$STATE_FILE" ]]; then
  echo "❌ 找不到 $STATE_FILE"
  echo "   先跑一次：python local/export_login.py"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ 没找到 gh（GitHub CLI）。"
  echo "   Windows: winget install --id GitHub.cli    （装完要重开终端）"
  echo "   或改走网页手动路径：Settings → Secrets and variables → Actions"
  exit 1
fi

SIZE=$(wc -c < "$STATE_FILE" | tr -d ' ')
echo "==> 登录态：$STATE_FILE（${SIZE} 字节）"

if (( SIZE > 48 * 1024 )); then
  echo "❌ 超过 GitHub secret 的 48 KB 硬上限，会被拒收。"
  echo "   你可能导出成了未压缩的 storage_state.b64，请用 .gz.b64。"
  exit 1
fi

echo "==> 写入 secret WENS_STORAGE_STATE"
gh secret set WENS_STORAGE_STATE < "$STATE_FILE"

echo
echo "==> 当前仓库的 secret / variable 一览"
gh secret list || true
gh variable list || true

echo
echo "✅ 完成。去 Actions 页手动触发一次确认能跑通。"
