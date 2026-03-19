#!/bin/bash
set -e

# 后台运行监控进程
python monitor.py &

# 后台运行 Discord 情报机器人
python discord_agent.py &

# 前台运行 Streamlit（保持容器存活，任何崩溃都能被 Fly.io 探测到）
exec streamlit run Home.py --server.port 8080 --server.address 0.0.0.0
