#!/usr/bin/env bash
# restore_milvus.sh —— Milvus 恢复（API restore / DEGRADED etcd+minio 冷恢复，与 backup_milvus.sh 成对）
#
# 用法（在任意目录执行均可，脚本自动定位仓库根）：
#   bash scripts/backup/restore_milvus.sh [时间戳 | 备份目录] [--yes]
#     不带参数 = 取 backups/milvus/ 下最新一次备份；
#     --yes    = 跳过交互确认（无 tty 的自动化场景必须显式提供，防误删）。
#
# 路径选择：读取备份目录内 backup_manifest.txt 的 path= 字段：
#   API      ：POST /api/v1/restore（RestoreBackupRequest：backup_name/async/dropExistCollection，
#              契约以官方 docs/swagger.yaml 为准），并轮询 GET /api/v1/get_restore 至 SUCCESS(2)。
#   DEGRADED ：灾难恢复级冷恢复，流程——
#              1) 先停 milvus 再停 etcd（minio 保持运行，用于回灌数据）；
#              2) 一次性 etcd 容器执行 etcdctl snapshot restore 到宿主暂存目录
#                 （默认 --name=default --initial-cluster=default=http://localhost:2380，
#                   与 docker-compose.yml 单机 etcd 引导参数默认值一致）；
#              3) etcd 官方镜像为 distroless（无 rm/cp），用一次性 alpine 系 shell 容器
#                 挂载 etcd_data 命名卷，清空旧 member 并拷入恢复后的 member；
#              4) 一次性 mc 容器 mc mirror --overwrite --remove 把桶镜像精确回灌（回到备份点）；
#              5) 依次启动 etcd、milvus，轮询 etcd endpoint health 与 milvus /healthz。
#
# 破坏性提示：DEGRADED 恢复会停机并替换 etcd 数据卷当前内容、使 minio 业务桶回到备份点，
#             执行前必须人工确认影响范围。
#
# 定时示例（恢复脚本不建议上 cron；此处仅给出对应备份的手工启用示例，脚本绝不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_milvus.sh >> backups/logs/cron_milvus.log 2>&1
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

load_var MINIO_ACCESS_KEY minioadmin
load_var MINIO_SECRET_KEY minioadmin
load_var MINIO_BUCKET a-bucket
MILVUS_BACKUP_URL="${MILVUS_BACKUP_URL:-http://milvus-backup:8080}"
ETCD_ENDPOINTS="${ETCD_ENDPOINTS:-http://127.0.0.1:2379}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"
ETCD_IMAGE="${ETCD_IMAGE:-quay.io/coreos/etcd:v3.5.16}"
# etcd 镜像为 distroless 无 shell；用本栈自带的 alpine 系镜像做卷内容替换（可用 SHELL_IMAGE 覆盖）
SHELL_IMAGE="${SHELL_IMAGE:-postgres:15-alpine}"
require_vars MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_BUCKET

ASSUME_YES=0
SEL=""
for arg in "$@"; do
  case $arg in
    --yes) ASSUME_YES=1 ;;
    -*) die "未知参数：$arg" ;;
    *)  [[ -z $SEL ]] || die "只能指定一个备份（时间戳或目录）"
        SEL=$arg ;;
  esac
done

# ---------------------------------------------------------------------------
# 定位备份目录与 manifest
# ---------------------------------------------------------------------------
MILVUS_ROOT="$REPO_ROOT/backups/milvus"
if [[ -z $SEL ]]; then
  BACKUP_DIR=$(ls -1d "$MILVUS_ROOT"/*/ 2>/dev/null | sort | tail -n 1 | sed 's#/$##')
elif [[ -d $SEL && -f $SEL/backup_manifest.txt ]]; then
  BACKUP_DIR=$(cd "$SEL" && pwd)
elif [[ -d $MILVUS_ROOT/$SEL && -f $MILVUS_ROOT/$SEL/backup_manifest.txt ]]; then
  BACKUP_DIR="$MILVUS_ROOT/$SEL"
else
  die "找不到备份：${SEL}（应是 backups/milvus/<ts> 时间戳或含 backup_manifest.txt 的目录）"
fi
[[ -n ${BACKUP_DIR:-} && -f $BACKUP_DIR/backup_manifest.txt ]] || die "备份清单缺失：$BACKUP_DIR/backup_manifest.txt"
MANIFEST="$BACKUP_DIR/backup_manifest.txt"
manifest_val() { grep "^$1=" "$MANIFEST" | head -n 1 | cut -d= -f2- || true; }
BPATH=$(manifest_val path)
TS_NAME=$(manifest_val ts)
[[ -n $BPATH ]] || die "manifest 缺少 path 字段：$MANIFEST"

# ---------------------------------------------------------------------------
# 人工确认（破坏性操作）
# ---------------------------------------------------------------------------
if (( ASSUME_YES == 0 )); then
  echo "================================================================"
  echo "即将对 Milvus 执行【${BPATH}】恢复，备份时间戳：${TS_NAME:-未知}"
  if [[ $BPATH == "DEGRADED" ]]; then
    echo "影响范围："
    echo "  - 停止 milvus、etcd 容器（停机数分钟），minio/postgres/elasticsearch 不受影响"
    echo "  - etcd_data 数据卷当前内容将被快照恢复结果整体替换"
    echo "  - minio 桶 ${MINIO_BUCKET} 将被回灌并删除备份点之后的多余对象（--remove）"
  else
    echo "影响范围：经 sidecar 恢复备份 ${TS_NAME}，dropExistCollection=true 会先删除同名 collection"
  fi
  echo "================================================================="
  printf '确认恢复请输入 yes（其余任意输入取消）：'
  read -r ans
  [[ $ans == "yes" ]] || die "用户取消恢复"
fi

# 取 minio 容器实际网络（一次性 mc/etcd 容器需要服务名解析）
compose_network() {
  local cid net
  cid=$(compose ps -q minio)
  [[ -n $cid ]] || die "未找到运行中的 minio 容器"
  net=$(docker inspect "$cid" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
  [[ -n $net ]] || die "无法读取 compose 网络名"
  printf '%s' "$net"
}

# 读取容器挂载的命名卷名：$1=服务 $2=容器内挂载点
volume_of() {
  local svc=$1 dst=$2 cid vol
  cid=$(compose ps -aq "$svc")
  [[ -n $cid ]] || die "未找到服务容器：$svc"
  vol=$(docker inspect "$cid" -f "{{range .Mounts}}{{if eq .Destination \"$dst\"}}{{.Name}}{{end}}{{end}}")
  [[ -n $vol ]] || die "未取到 $svc 的挂载卷：$dst"
  printf '%s' "$vol"
}

# ===========================================================================
# 路径一：API restore
# 字段依据 swagger RestoreBackupRequest / RestoreTaskStateCode（2=SUCCESS,3=FAIL）
# ===========================================================================
if [[ $BPATH == "API" ]]; then
  BACKUP_NAME=$(manifest_val backup_name)
  [[ -n $BACKUP_NAME ]] || die "API 备份 manifest 缺少 backup_name"
  log "[Milvus][API] 提交恢复请求：${BACKUP_NAME}（dropExistCollection=true，async=false）"
  RESP=$(compose exec -T milvus curl -sS -m 600 \
           -H 'Content-Type: application/json' \
           -XPOST "$MILVUS_BACKUP_URL/api/v1/restore" \
           -d "{\"backup_name\":\"$BACKUP_NAME\",\"async\":false,\"dropExistCollection\":true}") \
    || die "[Milvus][API] restore 请求失败"
  echo "$RESP" | grep -q '"code":0' || die "[Milvus][API] restore 返回非成功：$RESP"
  TASK_ID=$(echo "$RESP" | grep -o '"id":"[^"]*"' | head -n 1 | cut -d'"' -f4 || true)
  if [[ -n $TASK_ID ]]; then
    log "[Milvus][API] 轮询恢复任务状态：id=$TASK_ID"
    for _ in $(seq 1 120); do
      INFO=$(compose exec -T milvus curl -sS -m 30 \
               "$MILVUS_BACKUP_URL/api/v1/get_restore?id=$TASK_ID")
      if echo "$INFO" | grep -Eq '"state_code":3|"state_code": 3'; then
        die "[Milvus][API] 恢复任务 FAIL：$INFO"
      fi
      if echo "$INFO" | grep -Eq '"state_code":2|"state_code": 2'; then
        log "[Milvus][API] 恢复完成（state_code=SUCCESS）"
        break
      fi
      sleep 5
    done
    echo "$INFO" | grep -Eq '"state_code":2|"state_code": 2' \
      || die "[Milvus][API] 恢复任务超时未 SUCCESS：$INFO"
  fi
  log "[Milvus][API] 恢复流程结束，请用业务查询核对 collection 与向量数"
  exit 0
fi

# ===========================================================================
# 路径二：DEGRADED 冷恢复
# ===========================================================================
[[ $BPATH == "DEGRADED" ]] || die "未知备份路径类型：${BPATH}（仅支持 API/DEGRADED）"

ETCD_FILE="$BACKUP_DIR/fallback/etcd_snapshot.db"
MINIO_ARCHIVE=$(ls "$BACKUP_DIR/fallback/minio_"*".tar.gz" 2>/dev/null | head -n 1 || true)
[[ -s $ETCD_FILE ]]     || die "etcd 快照缺失或为空：$ETCD_FILE"
[[ -n $MINIO_ARCHIVE && -s $MINIO_ARCHIVE ]] || die "minio 桶归档缺失或为空"
log "[Milvus][DEGRADED] 备份完整性检查通过：$(basename "$ETCD_FILE")、$(basename "$MINIO_ARCHIVE")"

WORK="$BACKUP_DIR/_restore_ws"
rm -rf "$WORK"
mkdir -p "$WORK/etcd_restored" "$WORK/minio"
# 无论后续成败，清理宿主暂存目录（卷内容替换完成后无需保留）
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# ---- 步骤 1：停 milvus、etcd（顺序：先数据面后协调面） ----------------------
log "[Milvus][DEGRADED] 1/5 停止 milvus -> etcd"
compose stop milvus
compose stop etcd

# ---- 步骤 2：离线 restore etcd 快照到宿主暂存目录 --------------------------
log "[Milvus][DEGRADED] 2/5 etcdctl snapshot restore（离线，新 member 身份）"
docker run --rm \
  -v "$ETCD_FILE:/snapshot.db:ro" \
  -v "$WORK/etcd_restored:/restored" \
  "$ETCD_IMAGE" etcdctl snapshot restore /snapshot.db --data-dir /restored/etcd
[[ -f $WORK/etcd_restored/etcd/member/snap/db ]] || die "snapshot restore 未产出 member/snap/db"

# ---- 步骤 3：替换 etcd_data 卷内容（distroless 无 rm/cp，借 alpine 系镜像） -
ETCD_VOL=$(volume_of etcd /etcd)
log "[Milvus][DEGRADED] 3/5 替换 etcd 命名卷内容：${ETCD_VOL}（清空旧 member，拷入恢复 member）"
docker run --rm \
  -v "$ETCD_VOL:/target" \
  -v "$WORK/etcd_restored/etcd:/src:ro" \
  "$SHELL_IMAGE" sh -c 'rm -rf /target/* /target/..?* /target/.[!.]* 2>/dev/null || true; cp -a /src/. /target/ && test -f /target/member/snap/db'

# ---- 步骤 4：minio 桶精确回灌（--remove 使目标与备份点完全一致） -----------
log "[Milvus][DEGRADED] 4/5 解压桶归档并 mc mirror 回灌桶 ${MINIO_BUCKET}"
tar -xzf "$MINIO_ARCHIVE" -C "$WORK/minio"
[[ -d $WORK/minio/$MINIO_BUCKET ]] || die "归档内未找到桶目录：$MINIO_BUCKET"
NET=$(compose_network)
docker run --rm --network "$NET" \
  -e "MC_HOST_local=http://${MINIO_ACCESS_KEY}:${MINIO_SECRET_KEY}@minio:9000" \
  -v "$WORK/minio:/out" \
  "$MC_IMAGE" mirror --overwrite --remove "/out/$MINIO_BUCKET" "local/$MINIO_BUCKET"

# ---- 步骤 5：启动 etcd、milvus 并等待健康 ----------------------------------
log "[Milvus][DEGRADED] 5/5 启动 etcd -> milvus 并等待健康"
compose start etcd
for i in $(seq 1 30); do
  if compose exec -T etcd etcdctl --endpoints="$ETCD_ENDPOINTS" endpoint health >/dev/null 2>&1; then
    log "[Milvus][DEGRADED] etcd endpoint health OK"; break
  fi
  (( i == 30 )) && die "etcd 启动后 90 秒内 endpoint health 未通过"
  sleep 3
done

compose start milvus
for i in $(seq 1 40); do
  CODE=$(compose exec -T milvus curl -sS -m 3 -o /dev/null -w '%{http_code}' \
           http://localhost:9091/healthz 2>/dev/null || true)
  if [[ $CODE == "200" ]]; then
    log "[Milvus][DEGRADED] milvus /healthz 200，恢复完成"; break
  fi
  (( i == 40 )) && die "milvus 启动后 200 秒内 /healthz 未返回 200（最后 HTTP=${CODE:-000}）"
  sleep 5
done

log "[Milvus][DEGRADED] 冷恢复结束：etcd 快照 + minio 桶 ${MINIO_BUCKET} 已回到备份点 ${TS_NAME}"
echo "$BACKUP_DIR"
