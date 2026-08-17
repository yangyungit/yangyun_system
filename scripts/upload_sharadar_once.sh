#!/bin/bash
# 一次性把 sharadar 原始 zip 传到 R2 低频存储类。
# 数据是 2026-06-14 下载的静态快照且订阅已到期，不可再生，但也不会再变，
# 所以不进每天的备份脚本。parquet/ 是本地转换产物，跳过。
# 断了可以重跑，rclone copy 会跳过已传完的文件。
set -euo pipefail

ROOT="/Users/zhanghao/yangyun/Code_Projects"
SRC="$ROOT/data/sharadar"

set -a
source "$ROOT/.env"
set +a

: "${R2_ACCESS_KEY_ID:?根 .env 里没有 R2_ACCESS_KEY_ID}"
: "${R2_BUCKET:?根 .env 里没有 R2_BUCKET}"

export RCLONE_CONFIG=/dev/null
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

rclone copy "$SRC" "R2:$R2_BUCKET/sharadar/" \
    --include "*.zip" \
    --include "_manifest.json" \
    --s3-storage-class STANDARD_IA \
    --transfers 2 \
    --progress

echo "--- 传完后对账 ---"
rclone size "R2:$R2_BUCKET/sharadar/"
echo -n "本地 zip 总字节: "
(cd "$SRC" && ls -l *.zip _manifest.json | awk '{s+=$5} END {print s}')
