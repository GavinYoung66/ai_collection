#!/usr/bin/env bash
# backup_pg.sh —— PostgreSQL 全量逻辑备份（pg_dump 明文 SQL 流式 gzip）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/backup_pg.sh
# 产物：backups/pg/pg_<YYYYmmdd_HHMMSS>.sql.gz（秒级时间戳，同日连跑不覆盖）
# 恢复：见 restore_pg.sh
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_pg.sh >> backups/logs/cron_pg.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

# 连接参数：环境变量 > .env；postgres 服务在 compose 中固定为 ai_collection 库/用户
load_var PG_USER ai_collection
load_var PG_DB ai_collection
load_var PG_PASSWORD
require_vars PG_USER PG_DB PG_PASSWORD

TS=$(backup_ts)
OUT_DIR="$REPO_ROOT/backups/pg"
OUT="$OUT_DIR/pg_${TS}.sql.gz"
TMP="$OUT.tmp.$$"
ensure_backup_dirs
trap 'rm -f "$TMP"' EXIT

log "[PG] 备份开始：用户=$PG_USER 数据库=$PG_DB 时间戳=$TS"
# pg_dump 在 postgres 容器内经本地 unix socket 执行（容器内本地连接为 trust，无需传密码）；
# pipefail 保证 pg_dump 失败时整条管道失败；先写临时文件，成功后原子改名
compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" \
  | gzip -c > "$TMP"

# 产物非空校验，空文件视为失败
[[ -s "$TMP" ]] || die "[PG] 备份产物为空：$TMP"
mv "$TMP" "$OUT"
trap - EXIT

log "[PG] 备份完成：backups/pg/$(basename "$OUT")（$(du -h "$OUT" | cut -f1)）"
# 末行输出产物绝对路径，供编排脚本 test -s 校验
echo "$OUT"
