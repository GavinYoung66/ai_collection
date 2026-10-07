#!/usr/bin/env bash
# restore_es.sh —— Elasticsearch 快照恢复（POST _snapshot/<repo>/<snapshot>/_restore）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/restore_es.sh [快照名] [--index <索引名>] [--yes]
#     不带快照名时，默认取 backups/es/manifests/ 下时间戳最新的 snap_<ts>。
#     不带 --index 时恢复快照内全部索引（危险：同名索引须已关闭/删除，否则 400）；
#     建议显式 --index 精准恢复。脚本开始前打印风险并要求二次确认。
# 参考：ES 8.11 restore-snapshot-api（wait_for_completion / indices / include_global_state）
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_es.sh >> backups/logs/cron_es.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ES_BASE="http://localhost:9200"
ES_REPO="${ES_REPO:-ai_collection}"
SNAP=""
TARGET_INDEX=""
ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --index) TARGET_INDEX=${2:-}; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -*) die "[ES] 未知参数：$1（支持 --index <name> / --yes）" ;;
    *) SNAP=$1; shift ;;
  esac
done

# 默认取最新快照（manifests 清单文件名即快照名）
if [[ -z $SNAP ]]; then
  SNAP=$(ls -t "$REPO_ROOT"/backups/es/manifests/snap_*.json 2>/dev/null | head -n 1 | xargs basename 2>/dev/null | sed 's/\.json$//' || true)
  [[ -n $SNAP ]] || die "[ES] backups/es/manifests/ 下找不到快照清单"
fi

if [[ -n $TARGET_INDEX ]]; then
  SCOPE_DESC="索引 $TARGET_INDEX"
  RESTORE_BODY="{\"indices\":\"$TARGET_INDEX\",\"include_global_state\":false,\"include_aliases\":false}"
else
  SCOPE_DESC="快照内全部索引"
  RESTORE_BODY='{"include_global_state":false,"include_aliases":false}'
fi

log "[ES] 即将从快照 ${SNAP} 恢复：${SCOPE_DESC}"
echo "[WARN] 恢复会覆盖/重建同名索引（现存同名 open 索引会导致恢复 400，请先删除或关闭）！" >&2

if [[ $ASSUME_YES -ne 1 ]]; then
  if [[ ! -r /dev/tty ]]; then
    die "[ES] 当前无交互终端；确认执行恢复请显式加 --yes"
  fi
  printf '确认恢复请输入快照名 %s ：' "$SNAP" >&2
  read -r answer < /dev/tty
  [[ $answer == "$SNAP" ]] || die "[ES] 输入不匹配，已取消"
fi

# wait_for_completion=true：主分片恢复完成才返回。
# 注意：该模式成功响应体是快照恢复描述 {"snapshot":{...,"shards":{"failed":0,...}}}，
# 没有 accepted 字段（accepted:true 仅在 wait_for_completion=false 时返回，实测 8.11）；
# -f/--fail 保证 HTTP 4xx/5xx 直接非零退出
RESP=$(compose exec -T elasticsearch curl -fsS -XPOST \
  "$ES_BASE/_snapshot/$ES_REPO/$SNAP/_restore?wait_for_completion=true" \
  -H 'Content-Type: application/json' -d "$RESTORE_BODY") \
  || die "[ES] 恢复请求失败（检查同名索引是否仍存在、快照状态是否 SUCCESS）"
echo "$RESP"
echo "$RESP" | grep -q '"failed" *: *0' \
  || die "[ES] 恢复响应中存在失败分片：$RESP"

log "[ES] 恢复请求完成：${SNAP} -> ${SCOPE_DESC}，请立即执行索引存在性/文档数/检索校验"
