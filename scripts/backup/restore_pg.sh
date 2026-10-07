#!/usr/bin/env bash
# restore_pg.sh —— PostgreSQL 全量逻辑恢复（读取 backup_pg.sh 产出的 .sql.gz）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/restore_pg.sh [备份文件] [--clean-db] [--yes]
#     不带备份文件时，默认取 backups/pg/ 下时间戳最新的一份。
#     --clean-db ：恢复前 DROP 并重建目标库（整库级恢复，会丢失目标库当前全部数据！）
#     不带 --clean-db 时直接向现有库回放 SQL，调用方须自行确认目标库已清空/删表；
#     脚本开始前会打印风险提示并要求二次确认（交互输入库名，或显式 --yes）。
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_pg.sh >> backups/logs/cron_pg.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

CLEAN_DB=0
ASSUME_YES=0
DUMP_FILE=""
for arg in "$@"; do
  case $arg in
    --clean-db) CLEAN_DB=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -*) die "[PG] 未知参数：$arg（支持 --clean-db / --yes）" ;;
    *) DUMP_FILE=$arg ;;
  esac
done

load_var PG_USER ai_collection
load_var PG_DB ai_collection
load_var PG_PASSWORD
require_vars PG_USER PG_DB PG_PASSWORD

# 默认取最新备份
if [[ -z $DUMP_FILE ]]; then
  DUMP_FILE=$(ls -t "$REPO_ROOT"/backups/pg/pg_*.sql.gz 2>/dev/null | head -n 1 || true)
  [[ -n $DUMP_FILE ]] || die "[PG] backups/pg/ 下找不到备份文件"
fi
[[ -s $DUMP_FILE ]] || die "[PG] 备份文件不存在或为空：$DUMP_FILE"
# 允许相对路径，统一成绝对路径
[[ $DUMP_FILE = /* ]] || DUMP_FILE="$REPO_ROOT/$DUMP_FILE"

log "[PG] 即将恢复：$(basename "$DUMP_FILE") -> 数据库 $PG_DB"
if [[ $CLEAN_DB -eq 1 ]]; then
  echo "[WARN] --clean-db 将 DROP DATABASE \"$PG_DB\" 并重建，库内现有全部数据将被销毁！" >&2
else
  echo "[WARN] 将直接向现有数据库 \"$PG_DB\" 回放 SQL，请确认目标库/目标表已按演练计划清空！" >&2
fi

if [[ $ASSUME_YES -ne 1 ]]; then
  # 非交互环境（无 tty）下不得误删，要求显式 --yes
  if [[ ! -r /dev/tty ]]; then
    die "[PG] 当前无交互终端；确认执行恢复请显式加 --yes"
  fi
  printf '确认恢复请输入数据库名 %s ：' "$PG_DB" >&2
  read -r answer < /dev/tty
  [[ $answer == "$PG_DB" ]] || die "[PG] 输入不匹配，已取消"
fi

if [[ $CLEAN_DB -eq 1 ]]; then
  # dropdb 默认连维护库 postgres；-f 先踢掉其它连接，--if-exists 幂等
  log "[PG] 重建目标库：dropdb --if-exists -f $PG_DB && createdb"
  compose exec -T postgres dropdb -U "$PG_USER" --if-exists --force "$PG_DB"
  compose exec -T postgres createdb -U "$PG_USER" "$PG_DB"
fi

# ON_ERROR_STOP=1：任一 SQL 报错即失败；--single-transaction：全量回放原子化
log "[PG] 开始回放 SQL ..."
gunzip -c "$DUMP_FILE" \
  | compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" \
      -v ON_ERROR_STOP=1 --single-transaction --quiet \
      --echo-errors

log "[PG] 恢复完成，请立即执行 count/抽样比对校验"
