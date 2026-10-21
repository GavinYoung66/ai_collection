#!/usr/bin/env bash
# _common.sh —— 备份/恢复脚本公共函数库（仅被 source，不直接执行）
#
# 统一约定：
#   1. 脚本内部自动定位仓库根并切换执行；统一经 docker compose 调用容器内客户端，
#      宿主机无需安装 pg_dump/etcdctl/mc 等工具（D6：备份脚本零宿主安装）。
#   2. 备份产物只允许落宿主机 ./backups/{pg,milvus,es}/，日志落 ./backups/logs/。
#   3. 连接参数优先级：已导出的环境变量 > 仓库根 .env > 脚本默认值；全程不回显敏感值。
#
# 定时示例（每日 02:00；仅为示例，需人工执行 crontab -e 注册，本脚本集绝不自动写 crontab）：
#   0 2 * * * cd <仓库根绝对路径> && bash scripts/backup/backup_all.sh >> backups/logs/cron.log 2>&1

# 仓库根 = 本文件所在目录的上两级（scripts/backup -> repo root）
BACKUP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BACKUP_LIB_DIR/../.." && pwd)"

# compose 封装：docker-compose.yml 中 gradio 依赖 rag-service（定义在 dev override），
# 只解析 base 文件会报 invalid compose project；dev override 不改变 infra 服务定义。
# 如需替换编排文件，可导出 COMPOSE_FILE_ARGS（如 "-f xxx.yml"）覆盖。
compose() {
  local args=${COMPOSE_FILE_ARGS:-"-f docker-compose.yml -f docker-compose.dev.yml"}
  # shellcheck disable=SC2086 # args 为受信任的内部默认值/人工覆盖值，需要分词
  docker compose $args "$@"
}

# 秒级时间戳；backup_all 通过导出 BACKUP_TS 让三类备份共用同一时间戳
backup_ts() {
  echo "${BACKUP_TS:-$(date +%Y%m%d_%H%M%S)}"
}

# 从仓库根 .env 读取单个键值（不 source：避免值中特殊字符触发命令替换等副作用）
_env_file_value() {
  local key=$1 val
  val=$(grep -E "^${key}=" "$REPO_ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)
  val=${val%$'\r'}
  # 去掉成对的首尾引号
  if [[ ${#val} -ge 2 ]]; then
    case $val in
      \"*\") val=${val#\"}; val=${val%\"} ;;
      \'*\') val=${val#\'}; val=${val%\'} ;;
    esac
  fi
  printf '%s' "$val"
}

# 加载配置：环境变量优先，其次 .env，最后默认值（第 2 个参数，可选）
# 用法：load_var PG_USER ai_collection
load_var() {
  local key=$1 default=${2-} current
  eval "current=\${$key-}"
  if [[ -z $current ]]; then
    current=$(_env_file_value "$key")
  fi
  if [[ -z $current && -n $default ]]; then
    current=$default
  fi
  eval "export $key=\$current"
}

# 非空校验：任一变量为空即非零退出（exit 2）
# 用法：require_vars PG_USER PG_DB PG_PASSWORD
require_vars() {
  local missing=() k cur
  for k in "$@"; do
    eval "cur=\${$k-}"
    if [[ -z $cur ]]; then
      missing+=("$k")
    fi
  done
  if ((${#missing[@]} > 0)); then
    echo "[FATAL] 连接变量为空，请先导出或在仓库根 .env 配置: ${missing[*]}" >&2
    exit 2
  fi
}

# 自愈创建备份目录结构
ensure_backup_dirs() {
  mkdir -p "$REPO_ROOT/backups/pg" "$REPO_ROOT/backups/milvus" \
           "$REPO_ROOT/backups/es" "$REPO_ROOT/backups/logs"
}

log() { echo "[$(date '+%F %T')] $*"; }
die() { echo "[FAIL] $*" >&2; exit 1; }
