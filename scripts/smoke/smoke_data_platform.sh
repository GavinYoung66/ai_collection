#!/bin/bash
# Data Platform 冒烟测试

set -e

PORT=8001
NAME="data-platform"
PASS=0
FAIL=0

check() {
    local name=$1
    local cmd=$2
    if eval "$cmd" >/dev/null 2>&1; then
        echo "[PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "========================================"
echo "  Data Platform Smoke Test (port $PORT)"
echo "========================================"

check "/health 200" "curl -sf http://localhost:$PORT/health >/dev/null"
check "/health service name" "curl -sf http://localhost:$PORT/health | grep -q "$NAME""
check "/health status healthy" "curl -sf http://localhost:$PORT/health | grep -q healthy"
check "/api/v1/health 200" "curl -sf http://localhost:$PORT/api/v1/health >/dev/null"
check "X-Trace-Id header" "curl -sfI http://localhost:$PORT/health | grep -qi X-Trace-Id"

echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
