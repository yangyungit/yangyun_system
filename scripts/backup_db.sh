#!/bin/bash
# 每天导出 valuation-radar 的数据库和各项目 .env，压缩后上传 Cloudflare R2。
# 未配置 R2 密钥时只做本地导出，不上传。
set -euo pipefail

ROOT="/Users/zhanghao/yangyun/Code_Projects"
STAGE="$HOME/backup_stage"
DATE=$(date +%Y%m%d)
DOW=$(date +%u)

DBS=(
    "$ROOT/valuation-radar/data/narrative.db"
    "$ROOT/valuation-radar/data/universe.db"
    "$ROOT/valuation-radar/data/estimates.db"
)

# .env 相对 ROOT 的路径，打包时保留目录结构便于恢复
ENV_FILES=(
    ".env"
    "valuation-radar/.env"
    "ledger/.env"
)

KEYCHAIN_SERVICE="r2-backup-pass"
DAILY_KEEP="14d"
WEEKLY_KEEP="56d"

log() { echo "[$(date '+%F %T')] $*"; }
die() { log "错误：$*"; exit 1; }

mkdir -p "$STAGE"

# 机器每天晚上关机，凌晨的定时点跑不到，所以改成开机即跑。
# 打点文件保证一天只成功跑一次，开机触发和 16:00 兜底触发不会重复备份。
STAMP="$STAGE/.last_run"
if [[ "${1:-}" != "--force" && -f "$STAMP" && "$(cat "$STAMP")" == "$DATE" ]]; then
    log "今天已备份过，跳过"
    exit 0
fi

command -v sqlite3 >/dev/null || die "缺 sqlite3"
command -v zstd >/dev/null || die "缺 zstd，跑 brew install zstd"

# --- 导出数据库 ---
# 用 .dump 而不是 .backup：narrative.db 的索引占 400MB，dump 出来只是 CREATE INDEX
# 语句，压缩后体积减半。代价是恢复时要重建索引。
for db in "${DBS[@]}"; do
    [[ -f "$db" ]] || die "找不到 $db"
    name=$(basename "$db" .db)
    out="$STAGE/${name}-${DATE}.sql.zst"

    log "导出 $name"
    sqlite3 "$db" .dump | zstd -3 -T4 -q -f -o "$out"

    # dump 正常结束一定以 COMMIT; 收尾，据此判断有没有导到一半就断
    if ! zstd -dc "$out" | tail -c 100 | grep -q "COMMIT;"; then
        die "$name 导出不完整"
    fi
    log "$name 完成 $(du -h "$out" | cut -f1)"
done

# --- 加密打包 .env ---
if PASS=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null); then
    export PASS
    existing=()
    for f in "${ENV_FILES[@]}"; do
        [[ -f "$ROOT/$f" ]] && existing+=("$f")
    done
    if [[ ${#existing[@]} -gt 0 ]]; then
        tar -czf - -C "$ROOT" "${existing[@]}" \
            | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
                -pass env:PASS -out "$STAGE/env-${DATE}.tar.gz.enc"
        log "已加密 ${#existing[@]} 个 .env"
    fi
    unset PASS
else
    log "钥匙串里没有 ${KEYCHAIN_SERVICE}，跳过 .env 备份"
fi

# --- 上传 R2 ---
if [[ -f "$ROOT/.env" ]]; then
    set -a
    source "$ROOT/.env"
    set +a
fi

if [[ -n "${R2_ACCESS_KEY_ID:-}" && -n "${R2_BUCKET:-}" ]]; then
    command -v rclone >/dev/null || die "缺 rclone，跑 brew install rclone"

    export RCLONE_CONFIG=/dev/null
    export RCLONE_CONFIG_R2_TYPE=s3
    export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
    export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
    export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
    export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
    export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

    log "上传 daily"
    rclone copy "$STAGE" "R2:$R2_BUCKET/daily/" --include "*-${DATE}.*" --stats-one-line

    # 周一那份留一份长期的。从 daily 服务端拷贝，不重新上传一遍
    if [[ "$DOW" == "1" ]]; then
        log "周一，从 daily 服务端拷一份到 weekly"
        rclone copy "R2:$R2_BUCKET/daily/" "R2:$R2_BUCKET/weekly/" \
            --include "*-${DATE}.*" --stats-one-line
    fi

    rclone delete "R2:$R2_BUCKET/daily" --min-age "$DAILY_KEEP" --rmdirs || true
    rclone delete "R2:$R2_BUCKET/weekly" --min-age "$WEEKLY_KEEP" --rmdirs || true
    log "R2 当前用量 $(rclone size "R2:$R2_BUCKET" --json 2>/dev/null || echo '查询失败')"
else
    log "根 .env 里没有 R2_ACCESS_KEY_ID，跳过上传，文件留在 $STAGE"
fi

# --- 清理本地暂存 ---
find "$STAGE" -type f \( -name "*.sql.zst" -o -name "*.enc" \) -mtime +2 -delete
echo "$DATE" > "$STAMP"
log "全部完成"
