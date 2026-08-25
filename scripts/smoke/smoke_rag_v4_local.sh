#!/bin/bash
# ============================================================
# RAG 多模态知识库 V4 —— 摘要元数据能力 本地冒烟测试（宿主终端执行）
# ============================================================
# 覆盖 V4 核心契约场景：
#   ① 上传文档携带 summary 摘要（POST /kb/documents form 字段）
#   ② GET /kb/documents/{doc_id} 详情返回 summary 字段
#   ③ KB_SUMMARY_ENABLED=true  时检索 results[].summary 填充文档级摘要
#   ④ KB_SUMMARY_ENABLED=false 时检索 results[].summary 恒为 null
#   ⑤ 无摘要（存量兼容）文档：不携带 summary 上传正常、详情 summary=null、
#      检索结果中该文档 summary=null、不抛异常
#
# 内置就绪等待：先轮询 /health 直至 healthy（SMOKE_WAIT_RETRIES x SMOKE_WAIT_INTERVAL），
#   服务未就绪绝不发起业务请求，避免"容器刚起就发请求"导致的误报。
# 退出码：全部用例通过 exit 0；任一用例失败立即 exit 1（fail-fast，便于定位）。
#
# 环境变量（均有默认值）：
#   SMOKE_BASE_URL       rag-service API 前缀   默认 http://localhost:8002/api/v1
#   SMOKE_HEALTH_URL     /health 地址           默认 http://localhost:8002/health
#   SMOKE_WAIT_RETRIES   就绪等待轮询次数       默认 60
#   SMOKE_WAIT_INTERVAL  就绪等待间隔（秒）      默认 5
#   KB_SUMMARY_ENABLED   true/false，必须与服务端开关一致：true→断言③，false→断言④
#   SMOKE_ADMIN_TOKEN    可选；不设置则用 common.security.rbac 本地签发 admin token
#
# 前置：docker compose -f docker-compose.smoke-v4-local.yml up -d --build
# 用法：bash scripts/smoke/smoke_rag_v4_local.sh
# ============================================================

BASE_URL="${SMOKE_BASE_URL:-http://localhost:8002/api/v1}"
HEALTH_URL="${SMOKE_HEALTH_URL:-http://localhost:8002/health}"
WAIT_RETRIES="${SMOKE_WAIT_RETRIES:-60}"
WAIT_INTERVAL="${SMOKE_WAIT_INTERVAL:-5}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SAMPLE_FILE="$PROJECT_ROOT/scripts/seed/sample_rules.txt"
PASS=0

# ---------- 辅助函数 ----------

fail() {
    echo "   FAIL: $1"
    echo ">>> 冒烟中止（fail-fast），退出码 1"
    exit 1
}

step_pass() {
    echo "   PASS"
    PASS=$((PASS + 1))
}

# py_eval：从 stdin 读 JSON，执行片段（片段内已定义 d=json 对象）；解析失败输出 __JSON_ERR__
py_eval() {
    python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('__JSON_ERR__')
    raise SystemExit
$1
" 2>/dev/null || echo "__ERR__"
}

# ---------- 模式解析：KB_SUMMARY_ENABLED 决定 ③/④ 断言方向 ----------
SUMMARY_ON="${KB_SUMMARY_ENABLED:-true}"
case "$SUMMARY_ON" in
    true|1|True|TRUE|yes) SUMMARY_ON=1 ;;
    false|0|False|FALSE|no) SUMMARY_ON=0 ;;
    *) echo "ERROR: KB_SUMMARY_ENABLED=$SUMMARY_ON 非法（仅 true/false）"; exit 1 ;;
esac
echo "模式: KB_SUMMARY_ENABLED=$SUMMARY_ON  （1=断言③摘要填充，0=断言④摘要恒 null）"
echo "      BASE_URL=$BASE_URL"

# ---------- Step 0: 等待服务就绪（内置等待逻辑） ----------
echo "============================================================"
echo "Step 0: 等待 rag-service 就绪（最长 ${WAIT_RETRIES}x${WAIT_INTERVAL}s）..."
echo "============================================================"
READY=0
for i in $(seq 1 "$WAIT_RETRIES"); do
    if curl -sf --max-time 3 "$HEALTH_URL" 2>/dev/null | grep -q "healthy"; then
        READY=1
        break
    fi
    if [ "$i" -eq 1 ] || [ $((i % 10)) -eq 0 ]; then
        echo "  [wait] 第 $i/$WAIT_RETRIES 次探测 $HEALTH_URL ..."
    fi
    sleep "$WAIT_INTERVAL"
done
if [ "$READY" != "1" ]; then
    echo "   FAIL: 服务在 ${WAIT_RETRIES}x${WAIT_INTERVAL}s 内未就绪"
    echo "   排查: docker compose -f docker-compose.smoke-v4-local.yml ps"
    echo "         docker compose -f docker-compose.smoke-v4-local.yml logs --tail=200 rag-service"
    exit 1
fi
echo "   OK: $HEALTH_URL -> healthy"
step_pass

# ---------- 签发 ADMIN token（本地 .env 的 JWT_SECRET 与服务端一致） ----------
if [ -n "$SMOKE_ADMIN_TOKEN" ]; then
    ADMIN_TOKEN="$SMOKE_ADMIN_TOKEN"
else
    PY_BIN=""
    for cand in "$CONDA_PREFIX/bin/python" "$HOME/my_software/miniconda3/envs/ai_collection/bin/python" python3; do
        if [ -n "$cand" ] && command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
    done
    if [ -z "$PY_BIN" ]; then
        echo "FAIL: 未找到可用 python，可设置 SMOKE_ADMIN_TOKEN 环境变量绕过签发"
        exit 1
    fi
    # 注意：cwd 必须是项目根（根目录 .env 才被 common.config.settings 加载）
    ADMIN_TOKEN=$(cd "$PROJECT_ROOT" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT" "$PY_BIN" -c \
        "from common.security.rbac import create_token; print(create_token({'user_id':'smoke','username':'smoke_tester','role':'admin'}))")
    if [ -z "$ADMIN_TOKEN" ]; then
        echo "FAIL: admin token 签发失败（检查根目录 .env 的 JWT_SECRET 是否已配置）"
        exit 1
    fi
fi

# 测试用摘要（< KB_SUMMARY_MAX_LEN=200）
SUMMARY_TEXT="V4 本地冒烟摘要：逾期协商还款催收业务规则要点"
QUERY="协商还款"

# ============================================================
echo "============================================================"
echo "Step 1/5 [①] 上传文档携带 summary 摘要（POST /kb/documents）"
echo "============================================================"
UPLOAD=$(curl -sf --max-time 30 -X POST "$BASE_URL/kb/documents" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -F "file=@$SAMPLE_FILE" \
    -F "category=regulation" \
    -F "summary=$SUMMARY_TEXT" 2>/dev/null || echo "")
DOC_ID=$(echo "$UPLOAD" | py_eval "print(d['data']['doc_id'])")
if [ "$DOC_ID" = "__ERR__" ] || [ "$DOC_ID" = "__JSON_ERR__" ] || [ -z "$DOC_ID" ]; then
    fail "上传携带摘要失败，响应: $(echo "$UPLOAD" | head -c 300)"
fi
echo "   doc_id=$DOC_ID"
STATUS=$(echo "$UPLOAD" | py_eval "print(d['data'].get('status', '__MISSING__'))")
[ "$STATUS" = "active" ] || fail "上传返回 status=$STATUS（期望 active）: $(echo "$UPLOAD" | head -c 300)"
step_pass

# ============================================================
echo "============================================================"
echo "Step 2/5 [②] GET 文档详情返回 summary 字段（与上传值一致）"
echo "============================================================"
DETAIL=$(curl -sf --max-time 15 "$BASE_URL/kb/documents/$DOC_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null || echo "")
DETAIL_SUMMARY=$(echo "$DETAIL" | py_eval "print('None' if d['data'].get('summary') is None else d['data']['summary'])")
if [ "$DETAIL_SUMMARY" = "$SUMMARY_TEXT" ]; then
    echo "   详情 summary 与上传值一致"
    step_pass
else
    fail "详情 summary 不一致: got '$DETAIL_SUMMARY'（期望 '$SUMMARY_TEXT'）响应: $(echo "$DETAIL" | head -c 300)"
fi

# ============================================================
SEARCH_JSON='{"query":"'"$QUERY"'","category":"regulation","top_k":20}'
SEARCH() {
    curl -sf --max-time 30 -X POST "$BASE_URL/kb/search" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H "X-Session-Id: smoke-v4-local" \
        -d "$SEARCH_JSON" 2>/dev/null || echo ""
}

if [ "$SUMMARY_ON" = "1" ]; then
    # ============================================================
    echo "============================================================"
    echo "Step 3/5 [③] 检索 results[].summary：开关开启 → 填充该文档摘要"
    echo "============================================================"
    SEARCH_RESP=$(SEARCH)
    KEY_CONTRACT=$(echo "$SEARCH_RESP" | py_eval "
results = d['data']['results']
missing = [i for i, r in enumerate(results) if 'summary' not in r]
print('OK' if not missing else 'MISSING_AT_' + str(missing))
")
    [ "$KEY_CONTRACT" = "OK" ] || fail "results[] 存在缺 summary 字段项: $KEY_CONTRACT 响应: $(echo "$SEARCH_RESP" | head -c 300)"
    FOUND_SUMMARY=$(echo "$SEARCH_RESP" | py_eval "
for r in d['data']['results']:
    if r.get('doc_id') == '$DOC_ID':
        print('None' if r.get('summary') is None else r['summary'])
        raise SystemExit
print('__NOT_FOUND__')
")
    if [ "$FOUND_SUMMARY" = "$SUMMARY_TEXT" ]; then
        echo "   results[].summary 命中文档且值一致（doc_id=$DOC_ID）"
        step_pass
    elif [ "$FOUND_SUMMARY" = "__NOT_FOUND__" ]; then
        fail "检索结果中未命中 doc_id=$DOC_ID（regulation 分类应仅含本脚本上传文档）响应: $(echo "$SEARCH_RESP" | head -c 300)"
    else
        fail "开关开启但 results[].summary 异常: got '$FOUND_SUMMARY'（期望 '$SUMMARY_TEXT'）"
    fi
else
    # ============================================================
    echo "============================================================"
    echo "Step 4/5 [④] 检索 results[].summary：开关关闭 → 恒为 null（含已存摘要文档）"
    echo "============================================================"
    SEARCH_RESP=$(SEARCH)
    NULL_CONTRACT=$(echo "$SEARCH_RESP" | py_eval "
results = d['data']['results']
bad = [r.get('doc_id') for r in results if r.get('summary') is not None]
print('OK' if not bad else 'NOT_NULL_DOCS_' + str(bad))
")
    [ "$NULL_CONTRACT" = "OK" ] || fail "开关关闭但存在非 null 摘要项: $NULL_CONTRACT（检查服务端是否已重建为 false）"
    TARGET_SUMMARY=$(echo "$SEARCH_RESP" | py_eval "
for r in d['data']['results']:
    if r.get('doc_id') == '$DOC_ID':
        print('None' if r.get('summary') is None else r['summary'])
        raise SystemExit
print('__NOT_FOUND__')
")
    [ "$TARGET_SUMMARY" = "None" ] || fail "开关关闭但 doc_id=$DOC_ID 的 results[].summary=$TARGET_SUMMARY（期望 null）"
    echo "   全部 results[].summary 为 null（含已存摘要文档 doc_id=$DOC_ID）"
    step_pass
fi

# ============================================================
echo "============================================================"
echo "Step 5/5 [⑤] 无摘要（存量兼容）文档：上传/详情/检索均不异常"
echo "============================================================"
LEGACY_UPLOAD=$(curl -sf --max-time 30 -X POST "$BASE_URL/kb/documents" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -F "file=@$SAMPLE_FILE" \
    -F "category=script" 2>/dev/null || echo "")
LEGACY_ID=$(echo "$LEGACY_UPLOAD" | py_eval "print(d['data']['doc_id'])")
if [ "$LEGACY_ID" = "__ERR__" ] || [ "$LEGACY_ID" = "__JSON_ERR__" ] || [ -z "$LEGACY_ID" ]; then
    fail "无摘要上传失败（存量行为应不变）: $(echo "$LEGACY_UPLOAD" | head -c 300)"
fi
echo "   无摘要上传成功 legacy_id=$LEGACY_ID"

LEGACY_DETAIL=$(curl -sf --max-time 15 "$BASE_URL/kb/documents/$LEGACY_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null || echo "")
LEGACY_SUMMARY=$(echo "$LEGACY_DETAIL" | py_eval "print('None' if d['data'].get('summary') is None else d['data']['summary'])")
[ "$LEGACY_SUMMARY" = "None" ] || fail "无摘要文档详情 summary 应为 null: got '$LEGACY_SUMMARY'"
echo "   无摘要文档详情 summary=null"

LEGACY_SEARCH=$(curl -sf --max-time 30 -X POST "$BASE_URL/kb/search" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "X-Session-Id: smoke-v4-local" \
    -d '{"query":"'"$QUERY"'","category":"script","top_k":20}' 2>/dev/null || echo "")
LEGACY_SEARCH_SUMMARY=$(echo "$LEGACY_SEARCH" | py_eval "
for r in d['data']['results']:
    if r.get('doc_id') == '$LEGACY_ID':
        print('None' if r.get('summary') is None else r['summary'])
        raise SystemExit
print('__NOT_FOUND__')
")
if [ "$LEGACY_SEARCH_SUMMARY" = "None" ]; then
    echo "   检索结果中无摘要文档 summary=null（不影响命中与排序）"
    step_pass
elif [ "$LEGACY_SEARCH_SUMMARY" = "__NOT_FOUND__" ]; then
    fail "检索结果中未命中无摘要文档 legacy_id=$LEGACY_ID"
else
    fail "无摘要文档检索 summary 异常: got '$LEGACY_SEARCH_SUMMARY'"
fi

# ---------- 清理：删除冒烟产生的文档（双索引清理） ----------
curl -sf -X DELETE "$BASE_URL/kb/documents/$DOC_ID" -H "Authorization: Bearer $ADMIN_TOKEN" >/dev/null 2>&1 || true
curl -sf -X DELETE "$BASE_URL/kb/documents/$LEGACY_ID" -H "Authorization: Bearer $ADMIN_TOKEN" >/dev/null 2>&1 || true
echo ""
echo "   （冒烟文档已清理）"

# ---------- 汇总 ----------
echo ""
echo "=== V4 Local Smoke Results: $PASS passed, 0 failed ==="
echo "=== 结论: 全部契约场景通过，exit 0 ==="
exit 0
