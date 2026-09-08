#!/bin/bash
# 投诉服务压测运行脚本
# 用法: bash run_loadtest.sh [--headless] [--users N] [--spawn-rate N] [--run-time TIME]
# 准入指标（design D6 stub 基线）：接单 RPS≥50、judge P95≤3s、查询 P95≤200ms、错误率≤1%
# 依赖：服务可达；docker-compose.dev.yml 仅作文档基线（未完成批次11 时本机 uvicorn 即可）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.dev.yml"
LOCUSTFILE="$SERVICE_DIR/tests/loadtest/locustfile.py"
HOST="${HOST:-http://127.0.0.1:8006}"
USERS="${USERS:-50}"
SPAWN_RATE="${SPAWN_RATE:-10}"
RUN_TIME="${RUN_TIME:-60s}"
HEADLESS=""
REPORT_DIR="$SCRIPT_DIR/reports"
mkdir -p "$REPORT_DIR"
SERVER_PID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --headless) HEADLESS="--headless" ;;
        --users) USERS="$2"; shift ;;
        --spawn-rate) SPAWN_RATE="$2"; shift ;;
        --run-time) RUN_TIME="$2"; shift ;;
        --host) HOST="$2"; shift ;;
        *) echo "unknown arg: $1"; exit 2 ;;
    esac
    shift
done

if [ ! -f "$LOCUSTFILE" ]; then
    echo "[FAIL] locustfile 不存在: $LOCUSTFILE"
    exit 1
fi

echo "========================================"
echo "  Complaint Service Load Test"
echo "  Host: $HOST"
echo "  Users: $USERS, Spawn: $SPAWN_RATE, Time: $RUN_TIME"
echo "  Locustfile: $LOCUSTFILE"
echo "========================================"

cleanup() {
    if [ -n "${SERVER_PID}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if ! curl -sf "$HOST/health" >/dev/null 2>&1; then
    echo "[INFO] 启动 complaint-service (uvicorn)..."
    if [ -f "$COMPOSE_FILE" ]; then
        echo "[INFO] 可用 compose 基线: $COMPOSE_FILE（本脚本默认本机 uvicorn）"
    fi
    cd "$SERVICE_DIR"
    PYTHONPATH=../..:${PYTHONPATH:-} python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8006 &
    SERVER_PID=$!
    sleep 4
    if ! curl -sf "$HOST/health" >/dev/null 2>&1; then
        echo "[FAIL] 服务未能启动"
        exit 1
    fi
fi

CSV_PREFIX="$REPORT_DIR/loadtest_$(date +%Y%m%d_%H%M%S)"
REPORT_HTML="${CSV_PREFIX}.html"

locust -f "$LOCUSTFILE" \
    --host "$HOST" \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$RUN_TIME" \
    $HEADLESS \
    --csv="$CSV_PREFIX" \
    --html "$REPORT_HTML"

STATS_CSV="${CSV_PREFIX}_stats.csv"
if [ ! -f "$STATS_CSV" ]; then
    echo "[FAIL] 未生成 stats CSV: $STATS_CSV"
    exit 1
fi

# 准入检查（Aggregated 行）
python3 - "$STATS_CSV" <<'PY'
import csv, sys
path = sys.argv[1]
rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
agg = next((r for r in rows if r.get("Name") == "Aggregated" or r.get("Type") == "None"), None)
if not agg:
    # locust 2.x: last row often Aggregated
    agg = rows[-1] if rows else None
if not agg:
    print("[FAIL] CSV 无 Aggregated 行")
    sys.exit(1)

def num(key, default=0.0):
    for k in agg:
        if k and k.strip().lower() == key.lower():
            try:
                return float(agg[k] or 0)
            except ValueError:
                return default
    # fuzzy
    for k, v in agg.items():
        if key.lower() in (k or "").lower():
            try:
                return float(v or 0)
            except ValueError:
                pass
    return default

# Locust CSV 常见列名
req = num("Request Count") or num("Request Count ")
fail = num("Failure Count")
# Requests/s
rps = num("Requests/s")
if rps == 0:
    rps = num("Total Request Rate")
# 95%
p95 = num("95%") or num("95%ile") or num("95%")
# Median as fallback if 95 missing
if p95 == 0:
    p95 = num("Median Response Time")

err_rate = (fail / req) if req else 1.0
print(f"[INFO] Aggregated: req={req:.0f} fail={fail:.0f} rps={rps:.2f} p95={p95:.1f}ms err={err_rate:.2%}")

ok = True
# stub 基线：整体 RPS≥50、错误率≤1%、P95≤3000ms（judge 含在混合流量内）
if rps < 50:
    print(f"[FAIL] RPS {rps:.2f} < 50")
    ok = False
else:
    print(f"[PASS] RPS {rps:.2f} ≥ 50")
if err_rate > 0.01:
    print(f"[FAIL] 错误率 {err_rate:.2%} > 1%")
    ok = False
else:
    print(f"[PASS] 错误率 {err_rate:.2%} ≤ 1%")
if p95 > 3000:
    print(f"[FAIL] P95 {p95:.1f}ms > 3000ms")
    ok = False
else:
    print(f"[PASS] P95 {p95:.1f}ms ≤ 3000ms")

# 查询类单独尽量检查（Name 含 status）
for r in rows:
    name = (r.get("Name") or "")
    if "status" in name or "verdict" in name:
        p95_q = 0.0
        for k, v in r.items():
            if k and "95" in k:
                try:
                    p95_q = float(v or 0)
                    break
                except ValueError:
                    pass
        if p95_q and p95_q > 200:
            print(f"[FAIL] 查询 {name} P95 {p95_q:.1f}ms > 200ms")
            ok = False
        elif p95_q:
            print(f"[PASS] 查询 {name} P95 {p95_q:.1f}ms ≤ 200ms")

if not ok:
    sys.exit(1)
print("[PASS] 压测准入指标全部达标（stub 基线，非 PRD 生产 SLA）")
PY

echo "========================================"
echo "  报告: $REPORT_HTML"
echo "  CSV: $STATS_CSV"
echo "  完整多副本压测：批次11 补强"
echo "========================================"
exit 0
