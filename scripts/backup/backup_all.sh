#!/usr/bin/env bash
# backup_all.sh —— 串行全量备份：PostgreSQL -> Milvus -> Elasticsearch
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/backup_all.sh
# 约定：
#   - 三类备份共用同一秒级时间戳（导出 BACKUP_TS，子脚本 backup_ts 自动复用），
#     因此同日连跑两次会生成两套不同时间戳产物，互不覆盖；
#   - 全程输出同时写终端与统一日志 backups/logs/backup_<ts>.log；
#   - 每一步执行后对约定产物做 test -s 非空校验；
#   - 任一步骤脚本非零退出或产物为空，立即以非零码退出，日志明确标出失败存储名，
#     后续存储不再执行（已经完成的产物保留不动）；
#   - 全部成功后清理 7 天前的旧备份与旧日志（find -mtime +7 -delete）。
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_all.sh >> backups/logs/cron.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

TS=$(backup_ts)
export BACKUP_TS="$TS"
ensure_backup_dirs
LOG="$REPO_ROOT/backups/logs/backup_${TS}.log"

# 全程 stdout/stderr 同时落统一日志（tee 追加）；重定向不影响本脚本退出码
exec > >(tee -a "$LOG") 2>&1

log "================ 全量备份开始 时间戳=${TS} ================"

# run_step <存储名> <脚本相对路径> <约定产物相对路径>
# 存储名用于失败时明确点名；产物路径按统一时间戳推导
run_step() {
  local store=$1 script=$2 artifact=$3
  log "---- [${store}] 开始：${script} ----"
  if ! bash "$REPO_ROOT/$script"; then
    log "[FATAL] 存储【${store}】备份失败：脚本非零退出（${script}）；后续存储取消执行。统一日志：backups/logs/backup_${TS}.log"
    exit 1
  fi
  if [[ ! -s "$REPO_ROOT/$artifact" ]]; then
    log "[FATAL] 存储【${store}】备份失败：约定产物缺失或为空（${artifact}）；后续存储取消执行。统一日志：backups/logs/backup_${TS}.log"
    exit 1
  fi
  log "---- [${store}] 成功，产物 test -s PASS：${artifact} ----"
}

# 顺序严格为 pg -> milvus -> es（与 tasks 3.7 一致）
run_step "postgres"      "scripts/backup/backup_pg.sh"      "backups/pg/pg_${TS}.sql.gz"
run_step "milvus"        "scripts/backup/backup_milvus.sh"  "backups/milvus/${TS}/backup_manifest.txt"
run_step "elasticsearch" "scripts/backup/backup_es.sh"      "backups/es/manifests/snap_${TS}.json"

# 保留期清理：删除 pg/milvus/es 三类产物及日志中 7 天前的普通文件（-type f 不动目录）
log "---- 清理 7 天前的旧备份/旧日志（find -mtime +7） ----"
find "$REPO_ROOT/backups/pg" "$REPO_ROOT/backups/milvus" \
     "$REPO_ROOT/backups/es" "$REPO_ROOT/backups/logs" \
     -type f -mtime +7 -print -delete || true

log "================ 全量备份成功完成 时间戳=${TS} ================"
log "统一日志：backups/logs/backup_${TS}.log"
echo "$LOG"
