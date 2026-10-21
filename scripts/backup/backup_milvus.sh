#!/usr/bin/env bash
# backup_milvus.sh —— Milvus 备份（milvus-backup sidecar 2.4 API 优先；不可用降级 etcd+minio 冷备）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/backup_milvus.sh
# 备份路径（自动二选一，日志会显式标注本次走的是哪条）：
#   API      ：探测到 milvus-backup sidecar（默认 http://milvus-backup:8080）时，
#              调官方 2.4 HTTP API（POST /api/v1/create，契约以官方 swagger 为准），
#              再用一次性 minio/mc 容器把 sidecar 备份 bucket mirror 回宿主机。
#   DEGRADED ：无 sidecar 时执行冷备——
#              1) etcd 容器内 etcdctl snapshot save（元数据一致性快照）并 cp 回宿主；
#              2) 一次性 minio/mc 容器加入 compose 网络，mc mirror Milvus bucket 后打 tar.gz。
#              冷备语义：建议在无写入窗口执行；恢复见 restore_milvus.sh。
# 产物：backups/milvus/<ts>/{api,fallback}/... 以及 backups/milvus/<ts>/backup_manifest.txt（机器校验入口）
#
# 可覆盖配置（环境变量 > 仓库根 .env > 默认值）：
#   MILVUS_BACKUP_URL     sidecar 地址（默认 http://milvus-backup:8080）
#   MILVUS_BACKUP_BUCKET  sidecar 备份桶名（默认 milvus-backup）
#   MINIO_BUCKET          Milvus 业务桶名（默认 a-bucket，与 docker-compose.yml 一致）
#   MINIO_ACCESS_KEY / MINIO_SECRET_KEY（默认 minioadmin/minioadmin，与 compose 一致）
#   MC_IMAGE              一次性 mc 镜像（默认 minio/mc:latest）
#   ETCD_ENDPOINTS        容器内 etcd 端点（默认 http://127.0.0.1:2379）
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，脚本不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_milvus.sh >> backups/logs/cron_milvus.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

load_var MINIO_ACCESS_KEY minioadmin
load_var MINIO_SECRET_KEY minioadmin
load_var MINIO_BUCKET a-bucket
MILVUS_BACKUP_URL="${MILVUS_BACKUP_URL:-http://milvus-backup:8080}"
MILVUS_BACKUP_BUCKET="${MILVUS_BACKUP_BUCKET:-milvus-backup}"
ETCD_ENDPOINTS="${ETCD_ENDPOINTS:-http://127.0.0.1:2379}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"
require_vars MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_BUCKET

TS=$(backup_ts)
BACKUP_NAME="backup_${TS}"
BASE="$REPO_ROOT/backups/milvus/$TS"
MANIFEST="$BASE/backup_manifest.txt"
ensure_backup_dirs
mkdir -p "$BASE"

# ---------------------------------------------------------------------------
# sidecar 探测：从 milvus 容器内 curl 官方无副作用端点 GET /api/v1/list。
# 该路由与参数以 zilliztech/milvus-backup 官方 docs/swagger.yaml 为准（basePath=/api/v1）。
# 输出形如 "6:<http_code>"；curl 无法连接/解析时退出码非 0、HTTP 码为 000。
# ---------------------------------------------------------------------------
probe_sidecar() {
  local out
  out=$(compose exec -T milvus \
          curl -sS -m 3 -o /dev/null -w '%{http_code}' \
          "$MILVUS_BACKUP_URL/api/v1/list" 2>/dev/null) || true
  printf '%s' "$out"
}

# 取 minio 容器实际接入的第一个 compose 网络名（一次性 mc 容器需加入同一网络才能解析主机名 minio）
compose_network() {
  local cid net
  cid=$(compose ps -q minio)
  [[ -n $cid ]] || die "未找到 minio 容器，无法确定 compose 网络"
  net=$(docker inspect "$cid" \
          -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
  [[ -n $net ]] || die "无法从 minio 容器读取网络名"
  printf '%s' "$net"
}

# 一次性 mc 容器执行 mirror（镜像无 shell，用官方 MC_HOST_<alias> 环境变量注入连接信息）
# 参数：$1=源 bucket/前缀（local/xxx） $2=容器内目标目录 $3=宿主挂载源目录
mc_mirror() {
  local src=$1 dst=$2 hostdir=$3 net
  net=$(compose_network)
  mkdir -p "$hostdir"
  # 凭证经容器环境变量传入（仅进程内可见）；--overwrite 以源端为准重传差异对象
  docker run --rm --network "$net" \
    -e "MC_HOST_local=http://${MINIO_ACCESS_KEY}:${MINIO_SECRET_KEY}@minio:9000" \
    -v "$hostdir:/out" \
    "$MC_IMAGE" mirror --overwrite "$src" "$dst"
}

HTTP_CODE=$(probe_sidecar)
log "[Milvus] sidecar 探测：GET ${MILVUS_BACKUP_URL}/api/v1/list -> HTTP ${HTTP_CODE:-000}"

# ===========================================================================
# 路径一：API（milvus-backup sidecar）
# 说明：请求/响应字段以官方 swagger（CreateBackupRequest/BackupInfoResponse）为准：
#   POST /api/v1/create  body {"backup_name":"...","async":false}
#   GET  /api/v1/get_backup?backup_name=...
#   成功判定：JSON 中 code=0；备份状态 data.state_code=2（BACKUP_SUCCESS）。
#   不传 collection_names 字段（nil）= 备份全部 collection（见 swagger 字段说明）。
# ===========================================================================
if [[ $HTTP_CODE == "200" ]]; then
  log "[Milvus] PATH=API（sidecar 可用，调用 2.4 backup API）"
  API_DIR="$BASE/api"
  mkdir -p "$API_DIR"

  # async=false：同步等待；存储预检失败时代码返回 code=503（见 swagger /create 描述）
  RESP=$(compose exec -T milvus curl -sS -m 600 \
           -H 'Content-Type: application/json' \
           -XPOST "$MILVUS_BACKUP_URL/api/v1/create" \
           -d "{\"backup_name\":\"$BACKUP_NAME\",\"async\":false}") \
    || die "[Milvus] API 创建备份请求失败：$BACKUP_NAME"
  echo "$RESP" | grep -q '"code":0' \
    || die "[Milvus] API 创建备份返回非成功：$RESP"

  # 复核备份终态：state_code=2 为 BACKUP_SUCCESS（枚举来自 swagger BackupTaskStateCode）
  INFO=$(compose exec -T milvus curl -sS -m 30 \
           "$MILVUS_BACKUP_URL/api/v1/get_backup?backup_name=$BACKUP_NAME") \
    || die "[Milvus] API 查询备份失败：$BACKUP_NAME"
  echo "$INFO" | grep -Eq '"state_code":2|"state_code": 2' \
    || die "[Milvus] API 备份未到达 SUCCESS：$INFO"

  # sidecar 备份实际落在 minio 备份桶，mirror 回宿主归档
  mc_mirror "local/$MILVUS_BACKUP_BUCKET" "/out/$MILVUS_BACKUP_BUCKET" "$API_DIR"
  FILE_CNT=$(find "$API_DIR" -type f | wc -l | tr -d ' ')
  [[ $FILE_CNT -gt 0 ]] || die "[Milvus] API 备份 mirror 回宿主后为空"

  {
    echo "# Milvus 备份产物清单（机器校验入口，backup_all 对本文件 test -s）"
    echo "ts=$TS"
    echo "path=API"
    echo "sidecar_url=$MILVUS_BACKUP_URL"
    echo "backup_name=$BACKUP_NAME"
    echo "backup_bucket=$MILVUS_BACKUP_BUCKET"
    echo "artifact_dir=backups/milvus/$TS/api"
    echo "files=$FILE_CNT"
    echo "create_response=$RESP"
  } > "$MANIFEST"
  log "[Milvus] PATH=API 备份完成：$BACKUP_NAME（文件 ${FILE_CNT} 个）"

# ===========================================================================
# 路径二：DEGRADED（etcd 一致性快照 + mc mirror minio 业务桶冷备）
# ===========================================================================
else
  REASON="sidecar 不可用（GET ${MILVUS_BACKUP_URL}/api/v1/list -> HTTP ${HTTP_CODE:-000}），按 design D6 走降级冷备"
  log "[Milvus] PATH=DEGRADED 原因=${REASON}"
  FB_DIR="$BASE/fallback"
  mkdir -p "$FB_DIR"

  # ---- 步骤 1：etcd 快照 -----------------------------------------------------
  # etcd v3.5 官方镜像为 distroless（无 sh/rm），只能直接 exec etcdctl；
  # 快照写入容器内 /tmp（可写层，不污染 /etcd 数据卷），随后 docker compose cp 回宿主。
  ETCD_CTR_PATH="/tmp/etcd_${TS}.db"
  log "[Milvus][DEGRADED] 1/3 etcdctl snapshot save -> ${ETCD_CTR_PATH}"
  compose exec -T etcd etcdctl --endpoints="$ETCD_ENDPOINTS" \
    snapshot save "$ETCD_CTR_PATH"
  # fields 输出含 hash/revision/totalKey/totalSize，作为快照完整性凭据落 manifest
  ETCD_STATUS=$(compose exec -T etcd etcdctl snapshot status "$ETCD_CTR_PATH" --write-out=fields)

  ETCD_FILE="$FB_DIR/etcd_snapshot.db"
  compose cp "etcd:${ETCD_CTR_PATH}" "$ETCD_FILE"
  [[ -s $ETCD_FILE ]] || die "[Milvus][DEGRADED] etcd 快照为空：$ETCD_FILE"

  # ---- 步骤 2：mc mirror Milvus 业务桶 --------------------------------------
  log "[Milvus][DEGRADED] 2/3 一次性 mc 容器 mirror 桶 ${MINIO_BUCKET}"
  STAGING="$FB_DIR/_staging"
  mkdir -p "$STAGING"
  mc_mirror "local/$MINIO_BUCKET" "/out/$MINIO_BUCKET" "$STAGING"
  FILE_CNT=$(find "$STAGING" -type f | wc -l | tr -d ' ')

  # ---- 步骤 3：打 tar.gz 并清理暂存目录 -------------------------------------
  log "[Milvus][DEGRADED] 3/3 打包 minio 桶镜像为 tar.gz"
  MINIO_ARCHIVE="$FB_DIR/minio_${MINIO_BUCKET}_${TS}.tar.gz"
  tar -czf "$MINIO_ARCHIVE" -C "$STAGING" "$MINIO_BUCKET"
  [[ -s $MINIO_ARCHIVE ]] || die "[Milvus][DEGRADED] minio 桶归档为空：$MINIO_ARCHIVE"
  rm -rf "$STAGING"

  {
    echo "# Milvus 备份产物清单（机器校验入口，backup_all 对本文件 test -s）"
    echo "ts=$TS"
    echo "path=DEGRADED"
    echo "reason=$REASON"
    echo "etcd_endpoints=$ETCD_ENDPOINTS"
    echo "etcd_snapshot=backups/milvus/$TS/fallback/etcd_snapshot.db"
    echo "etcd_snapshot_status_begin"
    echo "$ETCD_STATUS" | sed 's/^/  /'
    echo "etcd_snapshot_status_end"
    echo "minio_bucket=$MINIO_BUCKET"
    echo "minio_archive=backups/milvus/$TS/fallback/$(basename "$MINIO_ARCHIVE")"
    echo "mirrored_files=$FILE_CNT"
  } > "$MANIFEST"
  log "[Milvus] PATH=DEGRADED 备份完成：etcd 快照 + minio 桶 ${MINIO_BUCKET}（${FILE_CNT} 个对象）"
fi

# 最终机器校验：清单与产物必须非空
[[ -s $MANIFEST ]] || die "[Milvus] 备份清单为空：$MANIFEST"
echo "$MANIFEST"
