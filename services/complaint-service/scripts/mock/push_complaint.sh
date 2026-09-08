#!/usr/bin/env bash
# Mock 推送脚本：模拟传统业务系统向 complaint-service 推送投诉工单。
#
# 注意（批次0 边界）：
#   - 本脚本仅验证脚本形态与参数，不要求 API 已通。
#   - curl POST / GET status 端到端联调属【批次7】验收项，本批不执行。
#   - 默认 base URL: http://localhost:8006/api/v1
set -euo pipefail

BASE_URL="${COMPLAINT_SERVICE_URL:-http://localhost:8006/api/v1}"
COMPLAINT_ID="${1:-CP-2026-0001}"

echo "[mock-push] POST ${BASE_URL}/complaints  complaint_id=${COMPLAINT_ID}"
echo "[mock-push] 注意：端到端 curl 联调属批次7，本批仅验证脚本可执行。"

# 实际推送（API 未就绪时会失败，属预期）
curl -s -X POST "${BASE_URL}/complaints" \
  -H "Content-Type: application/json" \
  -d "{\"complaint_id\": \"${COMPLAINT_ID}\", \"user_id\": \"U10001\", \"complaint_text\": \"mock complaint\"}" \
  || echo "[mock-push] curl 失败（预期：API 未就绪，批次7联调）"
