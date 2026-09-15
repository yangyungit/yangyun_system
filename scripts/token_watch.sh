#!/bin/sh
# 先扫 Cursor 本地会话记录，再出报告推 Discord。
# 参数原样转给 token_report.py：不带参数 = 复盘前一天，--today = 复盘今天到现在。
set -e
cd /Users/zhanghao/yangyun/Code_Projects
PY=system/venv/bin/python

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始 ==="
# 扫 3 天而不是 1 天：Mac 休眠错过时间点时 launchd 会补跑，多扫两天兜住空档
$PY system/scripts/cursor_chat_scan.py 3
$PY system/scripts/token_report.py "$@"
