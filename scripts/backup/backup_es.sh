#!/usr/bin/env bash
# backup_es.sh —— Elasticsearch 快照备份（fs shared repository）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/backup_es.sh
# 前置：docker-compose.yml 已为 elasticsearch 配置
#       -Epath.repo=/usr/share/elasticsearch/backup 与 ./backups/es bind mount（见 3.3）
# 流程：幂等注册 fs 仓库 ai_collection -> 创建秒级时间戳快照（wait_for_completion=true）
#       -> _cat/snapshots 校验 SUCCESS -> 快照详情写 manifests 清单 -> 宿主目录非空校验
# 产物：宿主机 backups/es/backup/（ES 仓库实际数据）、backups/es/manifests/snap_<ts>.json（机器校验清单）
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_es.sh >> backups/logs/cron_es.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

# 容器内访问本机 9200；xpack.security.enabled=false，无需鉴权
ES_BASE="http://localhost:9200"
ES_REPO="${ES_REPO:-ai_collection}"      # fs 仓库名
ES_REPO_LOCATION="${ES_REPO_LOCATION:-backup}"  # 相对 path.repo 的子目录（对齐 design D6）

TS=$(backup_ts)
SNAP="snap_${TS}"
MANIFEST_DIR="$REPO_ROOT/backups/es/manifests"
MANIFEST="$MANIFEST_DIR/${SNAP}.json"
ensure_backup_dirs
mkdir -p "$MANIFEST_DIR"

# 统一在 elasticsearch 容器内调 curl（D6：不依赖宿主 curl 版本）
es_curl() {
  compose exec -T elasticsearch curl -sS -H 'Content-Type: application/json' "$@"
}

log "[ES] 幂等注册 fs 快照仓库：name=$ES_REPO location=$ES_REPO_LOCATION"
# 重复 PUT 同名同配置仓库是幂等的；ES 会做一次可写性校验
es_curl -XPUT "$ES_BASE/_snapshot/$ES_REPO" \
  -d "{\"type\":\"fs\",\"settings\":{\"location\":\"$ES_REPO_LOCATION\",\"compress\":true}}" \
  | grep -q '"acknowledged":true' \
  || die "[ES] 快照仓库注册失败"

log "[ES] 创建快照并等待完成：${SNAP}（wait_for_completion=true）"
# include_global_state=false：单节点 dev 环境只备份索引，不夹带集群全局状态，恢复更干净
es_curl -XPUT "$ES_BASE/_snapshot/$ES_REPO/$SNAP?wait_for_completion=true" \
  -d '{"include_global_state":false}' > /dev/null \
  || die "[ES] 快照创建失败：$SNAP"

# 8.11 的 _cat/snapshots 只接受 <repo> 路径段（不支持 <repo>/<snapshot> 两段路径），
# 列出整仓库后按快照名精确匹配状态
STATUS=$(es_curl "$ES_BASE/_cat/snapshots/$ES_REPO?h=snapshot,status" \
  | awk -v s="$SNAP" '$1==s {print $2; found=1} END {if (!found) exit 1}')
[[ $STATUS == "SUCCESS" ]] || die "[ES] 快照状态非 SUCCESS：${STATUS:-NOT_FOUND}"
log "[ES] 快照状态：SUCCESS"

# 快照详情落盘，供编排脚本 test -s 机器校验（位于 manifests/，不污染 ES 仓库目录）
es_curl "$ES_BASE/_snapshot/$ES_REPO/$SNAP?pretty" > "$MANIFEST"
[[ -s "$MANIFEST" ]] || die "[ES] 快照清单为空：$MANIFEST"

# 宿主侧 ES 仓库目录必须已有实际文件
DATA_FILE_CNT=$(find "$REPO_ROOT/backups/es/$ES_REPO_LOCATION" -type f 2>/dev/null | wc -l | tr -d ' ')
[[ $DATA_FILE_CNT -gt 0 ]] || die "[ES] 宿主 backups/es/$ES_REPO_LOCATION 无快照数据文件"

log "[ES] 备份完成：${SNAP}（仓库数据文件 ${DATA_FILE_CNT} 个；清单 backups/es/manifests/${SNAP}.json）"
echo "$MANIFEST"
