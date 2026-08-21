#!/bin/bash
# 全量冒烟测试：基础设施 + 7 服务

set -e

SCRIPT_DIR=$(dirname "$0")
PASS=0
FAIL=0

run_smoke() {
    local name=$1
    local script=$2
    echo "
========================================"
    echo "  Running: $name"
    echo "========================================"
    if bash "$SCRIPT_DIR/$script"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
}

echo "========================================"
echo "  Full Smoke Test Suite"
echo "========================================"

run_smoke "Infrastructure" "smoke_infra.sh"
run_smoke "Data Platform" "smoke_data_platform.sh"
run_smoke "RAG Service" "smoke_rag_service.sh"
run_smoke "QC Service" "smoke_qc_service.sh"
run_smoke "Call Service" "smoke_call_service.sh"
run_smoke "Case Service" "smoke_case_service.sh"
run_smoke "Complaint Service" "smoke_complaint_service.sh"
run_smoke "Review Service" "smoke_review_service.sh"

echo "
========================================"
echo "  Final Results: $PASS suites passed, $FAIL suites failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
