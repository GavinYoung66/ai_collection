#!/usr/bin/env bash
# P0 复验：recreate complaint-service（读入 .env）+ 真图；可选 build。
# 用法（仓库根）：
#   bash scripts/mock/verify_complaint_v2_real_llm.sh
#   REBUILD=1 bash scripts/mock/verify_complaint_v2_real_llm.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "[v2-llm] missing .env — copy from .env.example and set keys" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
vals={}
for line in Path('.env').read_text().splitlines():
    s=line.strip()
    if not s or s.startswith('#') or '=' not in s: continue
    k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
assert vals.get('USE_MOCK_LLM','').lower() in ('false','0','no'), 'USE_MOCK_LLM must be false'
assert vals.get('USE_MOCK_MCP','true').lower() in ('true','1','yes'), 'USE_MOCK_MCP must stay true'
dash = vals.get('LLM_DASHSCOPE_API_KEY') or vals.get('LLM_API_KEY') or ''
assert dash and dash not in ('change_me','<change_me>'), 'need DashScope/LLM_API_KEY'
print('[v2-llm] env checks OK (USE_MOCK_LLM=false, MCP=true, dashscope key present)')
PY

if [[ "${REBUILD:-0}" == "1" ]]; then
  echo "[v2-llm] rebuild complaint-service…"
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build complaint-service
fi

echo "[v2-llm] recreate complaint-service to load .env…"
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate postgres redis complaint-service
sleep 8
curl -sf http://localhost:8006/health >/dev/null
echo "[v2-llm] health OK"

CID="v2-real-$(date +%s)"
curl -sf -X POST http://localhost:8006/api/v1/complaints \
  -H 'Content-Type: application/json' \
  -d "{\"complaint_text\":\"交叉验证真LLM：坐席半夜威胁上门并扬言告知单位同事\",\"case_id\":\"${CID}\",\"channel\":\"phone\"}" >/dev/null

echo "[v2-llm] judge run_graph=true id=${CID}"
RESP=$(curl -sf -X POST "http://localhost:8006/api/v1/complaints/${CID}/judge" \
  -H 'Content-Type: application/json' -d '{"run_graph":true}')
python3 -c "
import json,sys
d=json.loads(sys.argv[1]); x=d.get('data') or d
print('task_status', x.get('task_status'), 'verdict', x.get('verdict'), 'message', (x.get('message') or '')[:80])
" "$RESP"

DETAIL=$(curl -sf "http://localhost:8006/api/v1/complaints/${CID}")
python3 -c "
import json,sys
d=json.loads(sys.argv[1]); x=d.get('data') or d
st=x.get('task_status'); draft=x.get('response_draft') or ''
print('detail_status', st)
print('draft_snip', repr(draft[:160]))
mock_fixed = any(s in draft for s in (
    '坐席行为合规，用户对计息规则存在误解',
    '经核查，坐席存在不当话术，建议向用户致歉并按流程升级处理',
))
print('looks_like_fixed_mock', mock_fixed)
if st != 'pending_review':
    raise SystemExit('[v2-llm] FAIL: expected pending_review')
if mock_fixed:
    raise SystemExit('[v2-llm] FAIL: draft still looks like Mock fixed text — check logs for real_llm_invoke')
print('[v2-llm] PASS: pending_review + draft distinguishable from Mock fixtures')
" "$DETAIL"

echo "[v2-llm] tip: docker compose logs complaint-service 2>&1 | grep real_llm_invoke | tail"
