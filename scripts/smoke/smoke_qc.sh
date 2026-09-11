#!/usr/bin/env bash
# qc-service M1 冒烟（SSOT）：health → seed 通话 → replay/raw → records → alerts
#
# 依赖：rag-dict-mock（current.dict）+ data-platform + PostgreSQL + Redis。
# MUST NOT 启动 call-service（qc 不 ingest、无 WS、不把 call 当冒烟依赖）。
# MUST NOT 为词库去起 rag-service / ES / milvus。
#
# 前置：
#   1. postgres / redis 已起；qc-service 映射 localhost:8003（容器内 8000）
#   2. QC_MOCK_LLM=true（compose.dev 已钉死；跳过真模型、不熔断、不误报 A2011）
#   3. 词库探活需要 rag-dict-mock（宿主机 8002）；路径 replay 需要 data-platform。本脚本主路径用 replay/raw
#   4. seed 需要能连 PG（psql，或 docker exec 正在跑的 postgres 容器）
#   5. alerts 签发 JWT 需要能 import common.security.rbac（jose）。pick_python 优先 conda ai_collection；
#        也可 PYTHON=/path/to/ai_collection/bin/python bash scripts/smoke/smoke_qc.sh
#
# 起栈（仓库根，不要起 call-service / rag-service）：
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d \
#       postgres redis data-platform rag-dict-mock qc-service

#
# 用法（仓库根）：
#   bash scripts/smoke/smoke_qc.sh
# 旧名 scripts/smoke/smoke_qc_service.sh 会转调本脚本。

set -uo pipefail

PORT="${QC_SMOKE_PORT:-8003}"
BASE="http://localhost:${PORT}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PASS=0
FAIL=0
SESSION_ID="sess_smoke_qc_$(date +%s)"
CASE_NUMBER="SMOKE-QC-$(date +%s)"

pass() {
    echo "[PASS] $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL=$((FAIL + 1))
}

pick_python() {
    local conda_base
    if [ -n "${PYTHON:-}" ]; then
        echo "$PYTHON"
        return
    fi
    if [ -n "${CONDA_PREFIX:-}" ] && [ -x "${CONDA_PREFIX}/bin/python" ]; then
        echo "${CONDA_PREFIX}/bin/python"
        return
    fi
    if [ -x "/Users/ellisguo/my_software/miniconda3/envs/ai_collection/bin/python" ]; then
        echo "/Users/ellisguo/my_software/miniconda3/envs/ai_collection/bin/python"
        return
    fi
    conda_base="$(conda info --base 2>/dev/null || true)"
    if [ -n "$conda_base" ] && [ -x "${conda_base}/envs/ai_collection/bin/python" ]; then
        echo "${conda_base}/envs/ai_collection/bin/python"
        return
    fi
    if [ -x "${HOME}/miniconda3/envs/ai_collection/bin/python" ]; then
        echo "${HOME}/miniconda3/envs/ai_collection/bin/python"
        return
    fi
    command -v python3 || command -v python
}

echo "========================================"
echo "  QC Service Smoke (port ${PORT})"
echo "  不起 call-service；探活为根 GET /health（不要用 /api/v1/health）"
echo "========================================"

HEALTH_OK=0
if python3 - "$BASE" <<'PY'
import json, sys, urllib.error, urllib.request
base = sys.argv[1]
url = f"{base}/health"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=8) as resp:
        status = resp.getcode()
        body = json.loads(resp.read().decode())
except Exception as exc:
    print(f"health request failed: {exc}", file=sys.stderr)
    sys.exit(1)
if status != 200:
    print(f"HTTP {status}", file=sys.stderr)
    sys.exit(1)
if body.get("code") != "0":
    print(f"code={body.get('code')!r} (want 0)", file=sys.stderr)
    sys.exit(1)
data = body.get("data")
if not isinstance(data, dict):
    print("data missing", file=sys.stderr)
    sys.exit(1)
for key in ("ac_version", "llm_degraded", "rag_connected"):
    if key not in data:
        print(f"missing data.{key}", file=sys.stderr)
        sys.exit(1)
if data.get("status") not in {"ok", "healthy", "degraded"}:
    print(f"unexpected status={data.get('status')!r}", file=sys.stderr)
    sys.exit(1)
print("health envelope ok")
PY
then
    HEALTH_OK=1
    pass "GET /health code=0 + ac_version/llm_degraded/rag_connected"
else
    fail "GET /health code=0 + ac_version/llm_degraded/rag_connected（需 qc-service 映射 localhost:${PORT}）"
fi

# 权威探活不在 /api/v1/health。仅在根 /health 已通后断言，避免未监听时 curl 失败被记成 PASS。
if [ "$HEALTH_OK" -eq 1 ]; then
    API_HEALTH_CODE="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "${BASE}/api/v1/health" || true)"
    if [ "$API_HEALTH_CODE" = "404" ]; then
        pass "/api/v1/health is not the probe (HTTP 404)"
    elif [ "$API_HEALTH_CODE" = "200" ] || [ "$API_HEALTH_CODE" = "201" ] || [ "$API_HEALTH_CODE" = "204" ]; then
        fail "/api/v1/health should not be the probe (got HTTP ${API_HEALTH_CODE})"
    else
        fail "/api/v1/health expected HTTP 404, got ${API_HEALTH_CODE:-unreachable}"
    fi
else
    echo "根 /health 未通，跳过 /api/v1/health 断言" >&2
    echo "[SKIP] /api/v1/health is not the probe（根 /health 未通）" >&2
fi

run_psql() {
    local sql=$1
    local pg_host="${PG_HOST:-localhost}"
    local pg_port="${PG_PORT:-5432}"
    local pg_user="${PG_USER:-ai_collection}"
    local pg_db="${PG_DB:-ai_collection}"
    local pg_pass="${PG_PASSWORD:-change_me}"
    if command -v psql >/dev/null 2>&1; then
        if PGPASSWORD="$pg_pass" psql -h "$pg_host" -p "$pg_port" -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 -c "$sql"; then
            return 0
        fi
    fi
    if command -v docker >/dev/null 2>&1; then
        # 不用 docker compose -f docker-compose.yml：base 里 gradio depends_on rag-service（未在该文件定义）。
        local cid=""
        cid="$(docker ps --filter ancestor=postgres:15-alpine --format '{{.ID}}' 2>/dev/null | head -1 || true)"
        if [ -z "$cid" ]; then
            cid="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '(^|-)postgres(-|$)|postgres' | head -1 || true)"
        fi
        if [ -n "$cid" ]; then
            docker exec -i "$cid" psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 -c "$sql"
            return $?
        fi
    fi
    echo "need psql or docker compose exec postgres to seed call_records" >&2
    return 1
}

CASE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
SEED_SQL="
INSERT INTO cases (id, case_number, customer_name, phone_encrypted, phone_hash, overdue_amount, overdue_days, account_stage)
VALUES ('${CASE_ID}'::uuid, '${CASE_NUMBER}', '张三', 'enc', 'smokehash0000000000000000000000000000000000000000000000000000', 100.00, 10, 'M1');
INSERT INTO call_records (session_id, case_id, call_type, call_status, start_time, end_time, duration_seconds, result)
VALUES ('${SESSION_ID}', '${CASE_ID}'::uuid, 'ai', 'completed', NOW() - INTERVAL '8 minutes', NOW(), 480, 'completed');
INSERT INTO dialog_turns (session_id, turn_number, speaker, text)
VALUES ('${SESSION_ID}', 1, 'ai', '让你妈妈接电话');
"

echo "--- seed call_records + dialog_turns (${SESSION_ID}) ---"
if run_psql "$SEED_SQL"; then
    pass "seed 通话主数据"
else
    fail "seed 通话主数据（先起 postgres；qc 禁止自己 INSERT call_records）"
fi

RAW_JSON="$(python3 - "$SESSION_ID" "$CASE_ID" <<'PY'
import json, sys
print(json.dumps({
    "session_id": sys.argv[1],
    "case_id": sys.argv[2],
    "call_ended": True,
    "text_ready": True,
    "debtor_name": "张三",
    "result": None,
    "turns": [
        {"turn_id": "t1", "speaker": "agent", "text": "让你妈妈接电话", "turn_index": 1},
    ],
}, ensure_ascii=False))
PY
)"

echo "--- POST /api/v1/qc/replay/raw ---"
if python3 - "$BASE" "$RAW_JSON" "$SESSION_ID" <<'PY'
import json, sys, urllib.error, urllib.request, uuid
base, payload, session_id = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    f"{base}/api/v1/qc/replay/raw",
    data=payload.encode(),
    headers={
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4()),
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.getcode()
        body = json.loads(resp.read().decode())
except urllib.error.HTTPError as exc:
    status = exc.code
    body = json.loads(exc.read().decode()) if exc.fp else {}
except Exception as exc:
    print(f"raw request failed: {exc}", file=sys.stderr)
    sys.exit(1)
data = body.get("data") or {}
code = body.get("code")
ok = status == 200 and code in {"0", "A2011"} and data.get("compliance_score") is not None
if not ok:
    print(f"raw failed HTTP={status} code={code} body={body}", file=sys.stderr)
    sys.exit(1)
if data.get("session_id") not in (None, session_id) and data.get("session_id") != session_id:
    print(f"session mismatch {data.get('session_id')}", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"code": code, "compliance_score": data.get("compliance_score"), "qc_record_id": data.get("qc_record_id")}, ensure_ascii=False))
open("/tmp/qc_smoke_raw.json", "w").write(json.dumps(body, ensure_ascii=False))
PY
then
    pass "replay/raw code=0 or A2011 with compliance_score"
else
    fail "replay/raw code=0 or A2011 with compliance_score"
fi

echo "--- GET /api/v1/qc/records/{session_id} ---"
if python3 - "$BASE" "$SESSION_ID" <<'PY'
import json, sys, urllib.error, urllib.request
base, session_id = sys.argv[1], sys.argv[2]
url = f"{base}/api/v1/qc/records/{session_id}"
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        status = resp.getcode()
        body = json.loads(resp.read().decode())
except urllib.error.HTTPError as exc:
    status = exc.code
    body = json.loads(exc.read().decode()) if exc.fp else {}
except Exception as exc:
    print(f"records request failed: {exc}", file=sys.stderr)
    sys.exit(1)
if status != 200 or body.get("code") != "0":
    print(f"records failed HTTP={status} body={body}", file=sys.stderr)
    sys.exit(1)
data = body.get("data") or {}
if not data.get("qc_record_id"):
    print("missing qc_record_id", file=sys.stderr)
    sys.exit(1)
print(f"qc_record_id={data['qc_record_id']}")
PY
then
    pass "GET records detail has qc_record_id"
else
    fail "GET records detail has qc_record_id"
fi

echo "--- GET /api/v1/qc/alerts (SUPV) ---"
PYBIN="$(pick_python || true)"
TOKEN=""
if [ -z "$PYBIN" ]; then
    fail "could not mint SUPV token: 找不到 python"
elif [ ! -x "$PYBIN" ] && ! command -v "$PYBIN" >/dev/null 2>&1; then
    fail "could not mint SUPV token: 找不到 python (${PYBIN})"
else
    TOKEN_ERR="$(mktemp)"
    TOKEN="$(
        PYTHONPATH="$ROOT" "$PYBIN" - <<'PY' 2>"$TOKEN_ERR"
import sys
import traceback
from uuid import uuid4

try:
    import jose  # noqa: F401
except ImportError as exc:
    print(f"missing jose: {exc}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(2)

try:
    from common.security.rbac import create_token
except Exception:
    traceback.print_exc()
    sys.exit(3)

try:
    print(create_token({"sub": str(uuid4()), "role": "SUPV"}))
except Exception:
    traceback.print_exc()
    sys.exit(4)
PY
    )"
    TOKEN_STATUS=$?
    if [ -z "$TOKEN" ] || [ "$TOKEN_STATUS" -ne 0 ]; then
        cat "$TOKEN_ERR" >&2
        ERR_TXT="$(cat "$TOKEN_ERR" 2>/dev/null || true)"
        rm -f "$TOKEN_ERR"
        if [ "$TOKEN_STATUS" -eq 127 ] || echo "$ERR_TXT" | grep -qi "No such file"; then
            fail "could not mint SUPV token: 找不到 python (${PYBIN})"
        elif [ "$TOKEN_STATUS" -eq 2 ] || echo "$ERR_TXT" | grep -qi "jose"; then
            fail "could not mint SUPV token: 缺 jose (${PYBIN})"
        elif echo "$ERR_TXT" | grep -qi "JWT_SECRET\|ValidationError\|pydantic"; then
            fail "could not mint SUPV token: create_token 抛错（JWT_SECRET / .env）using ${PYBIN}"
        else
            fail "could not mint SUPV token: create_token 抛错（JWT_SECRET / .env 或 import 失败）using ${PYBIN} exit=${TOKEN_STATUS}"
        fi
        TOKEN=""
    else
        rm -f "$TOKEN_ERR"
    fi
fi

if [ -n "$TOKEN" ]; then
    if python3 - "$BASE" "$TOKEN" "$SESSION_ID" <<'PY'
import json, sys, urllib.error, urllib.request
base, token, session_id = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    f"{base}/api/v1/qc/alerts?session_id={session_id}",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
        body = json.loads(resp.read().decode())
except urllib.error.HTTPError as exc:
    status = exc.code
    body = json.loads(exc.read().decode()) if exc.fp else {}
except Exception as exc:
    print(f"alerts request failed: {exc}", file=sys.stderr)
    sys.exit(1)
if status != 200 or body.get("code") != "0":
    print(f"alerts failed HTTP={status} body={body}", file=sys.stderr)
    sys.exit(1)
items = (body.get("data") or {}).get("items")
if items is None:
    print("alerts data.items missing", file=sys.stderr)
    sys.exit(1)
print(f"alerts total={len(items)}")
PY
    then
        pass "GET /qc/alerts 200 (empty or NEW high)"
    else
        fail "GET /qc/alerts 200 (empty or NEW high)"
    fi
fi

echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
