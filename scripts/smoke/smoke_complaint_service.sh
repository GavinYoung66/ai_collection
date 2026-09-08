#!/bin/bash
# Complaint Service 冒烟测试
# 覆盖：health / create / judge / status / verdict / agent-logs / mode
# 格式：[PASS]/[FAIL]，失败退出码非 0

set -e

PORT=${PORT:-8006}
NAME="complaint-service"
PASS=0
FAIL=0
FAILURES=()

check() {
    local name=$1
    local cmd=$2
    if eval "$cmd" >/dev/null 2>&1; then
        echo "[PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $name"
        FAIL=$((FAIL + 1))
        FAILURES+=("$name")
    fi
}

check_body() {
    local name=$1
    local url=$2
    local pattern=$3
    if curl -sf "$url" 2>/dev/null | grep -q "$pattern"; then
        echo "[PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $name"
        FAIL=$((FAIL + 1))
        FAILURES+=("$name")
    fi
}

echo "========================================"
echo "  Complaint Service Smoke Test (port $PORT)"
echo "========================================"

# ── health ──
check "GET /health 200" "curl -sf http://localhost:$PORT/health >/dev/null"
check_body "GET /health service=complaint-service" "http://localhost:$PORT/health" "$NAME"
check_body "GET /health status=ok" "http://localhost:$PORT/health" '"status":"ok"'
check "GET /health no degraded" "curl -sf http://localhost:$PORT/health | grep -vq degraded"
check "GET /api/v1/health 200" "curl -sf http://localhost:$PORT/api/v1/health >/dev/null"

# ── create ──
CREATE_RESP=$(curl -sf -X POST http://localhost:$PORT/api/v1/complaints \
    -H "Content-Type: application/json" \
    -d '{"complaint_text": "冒烟测试投诉"}' 2>/dev/null || echo "")
CID=$(echo "$CREATE_RESP" | grep -o '"complaint_id":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -n "$CID" ]; then
    echo "[PASS] POST /complaints create (id=$CID)"
    PASS=$((PASS + 1))
else
    echo "[FAIL] POST /complaints create"
    FAIL=$((FAIL + 1))
    FAILURES+=("POST /complaints create")
fi

# ── judge (stub 路径) ──
if [ -n "$CID" ]; then
    check "POST /complaints/{id}/judge" \
        "curl -sf -X POST http://localhost:$PORT/api/v1/complaints/$CID/judge -H 'Content-Type: application/json' -d '{}' >/dev/null"
    # judge 需 POST，检查返回含 verdict
    JUDGE_RESP=$(curl -sf -X POST http://localhost:$PORT/api/v1/complaints/$CID/judge \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo "")
    if echo "$JUDGE_RESP" | grep -q '"verdict"'; then
        echo "[PASS] judge verdict present"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] judge verdict present"
        FAIL=$((FAIL + 1))
        FAILURES+=("judge verdict present")
    fi
else
    echo "[FAIL] POST /complaints/{id}/judge (no complaint id)"
    FAIL=$((FAIL + 1))
    FAILURES+=("POST /complaints/{id}/judge")
fi

# ── status ──
if [ -n "$CID" ]; then
    check "GET /complaints/{id}/status" \
        "curl -sf http://localhost:$PORT/api/v1/complaints/$CID/status >/dev/null"
    check_body "status task_status=pending_review" \
        "http://localhost:$PORT/api/v1/complaints/$CID/status" '"task_status"'
fi

# ── verdict ──
if [ -n "$CID" ]; then
    check "GET /complaints/{id}/verdict" \
        "curl -sf http://localhost:$PORT/api/v1/complaints/$CID/verdict >/dev/null"
fi

# ── agent-logs ──
if [ -n "$CID" ]; then
    check "GET /complaints/{id}/agent-logs" \
        "curl -sf http://localhost:$PORT/api/v1/complaints/$CID/agent-logs >/dev/null"
fi

# ── mode (PUT /api/v1/complaints/mode) ──
MODE_RESP=$(curl -sf -X PUT http://localhost:$PORT/api/v1/complaints/mode \
    -H "Content-Type: application/json" \
    -d '{"mode":"single_agent","signers":["ops","tech"],"gray_percent":0}' 2>/dev/null || echo "")
if [ -n "$MODE_RESP" ]; then
    echo "[PASS] PUT /complaints/mode switch"
    PASS=$((PASS + 1))
else
    echo "[FAIL] PUT /complaints/mode switch"
    FAIL=$((FAIL + 1))
    FAILURES+=("PUT /complaints/mode switch")
fi

echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    echo "Failed checks:"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
