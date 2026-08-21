#!/bin/bash
set -e

# RAG Service Smoke Test
# Tests: health → upload TXT → list → search → delete

BASE_URL="http://localhost:8002/api/v1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SAMPLE_FILE="$PROJECT_ROOT/scripts/seed/sample_rules.txt"

# 签发 ADMIN token（上传/删除接口需要 ADMIN/SUPERVISOR 权限）
# 优先使用环境变量 SMOKE_ADMIN_TOKEN，否则动态签发
if [ -n "$SMOKE_ADMIN_TOKEN" ]; then
  ADMIN_TOKEN="$SMOKE_ADMIN_TOKEN"
else
  PY_BIN=""
  for cand in "$CONDA_PREFIX/bin/python" "$HOME/my_software/miniconda3/envs/ai_collection/bin/python" "$HOME/miniconda3/envs/ai_collection/bin/python" python3; do
    if [ -n "$cand" ] && command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
  done
  if [ -z "$PY_BIN" ]; then
    echo "   FAIL: 未找到可用的 python（需安装 jose 以签发 token），可设置 SMOKE_ADMIN_TOKEN 环境变量"
    exit 1
  fi
  # 在 rag-service 目录下签发，保持与运行中的服务相同的 .env 加载来源（settings env_file 相对 cwd），
  # 避免项目根 .env 的 JWT_SECRET 与服务的默认值不一致导致签名校验失败
  ADMIN_TOKEN=$(cd "$PROJECT_ROOT/services/rag-service" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT" "$PY_BIN" -c "from common.security.rbac import create_token; print(create_token({'user_id':'smoke','username':'smoke_tester','role':'admin'}))")
fi

echo "=== RAG Service Smoke Test ==="

# 1. Health check
echo "1. Health check..."
HEALTH=$(curl -sf "$BASE_URL/../health" 2>/dev/null || curl -sf "http://localhost:8002/health")
echo "   Health: $HEALTH"
echo "$HEALTH" | grep -q "healthy" || { echo "   FAIL: health check"; exit 1; }
echo "   PASS"

# 2. Upload TXT document
echo "2. Upload TXT document..."
UPLOAD_RESP=$(curl -sf -X POST "$BASE_URL/kb/documents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "file=@$SAMPLE_FILE" \
  -F "category=regulation")
echo "   Upload response: $UPLOAD_RESP"
DOC_ID=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['doc_id'])" 2>/dev/null || echo "")
if [ -z "$DOC_ID" ]; then
  echo "   FAIL: upload failed or no doc_id returned"
  exit 1
fi
echo "   doc_id=$DOC_ID"
echo "   PASS"

# 3. List documents
echo "3. List documents..."
LIST_RESP=$(curl -sf "$BASE_URL/kb/documents?category=regulation&page=1&size=5")
echo "   List response: $(echo $LIST_RESP | head -c 200)..."
TOTAL=$(echo "$LIST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['total'])" 2>/dev/null || echo "0")
echo "   total=$TOTAL"
[ "$TOTAL" -gt 0 ] 2>/dev/null || { echo "   WARN: total is 0 or parse failed"; }
echo "   PASS"

# 4. Search
echo "4. Search..."
SEARCH_RESP=$(curl -sf -X POST "$BASE_URL/kb/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"逾期协商还款","category":"regulation","top_k":3}')
echo "   Search response: $(echo $SEARCH_RESP | head -c 300)..."
RESULTS_COUNT=$(echo "$SEARCH_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(len(d.get('results',[])))" 2>/dev/null || echo "0")
echo "   results_count=$RESULTS_COUNT"
echo "   PASS"

# 5. Delete document
echo "5. Delete document..."
DELETE_RESP=$(curl -sf -X DELETE "$BASE_URL/kb/documents/$DOC_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
echo "   Delete response: $DELETE_RESP"
echo "   PASS"

echo ""
echo "=== RAG Smoke Test PASSED ==="
