#!/usr/bin/env bash
# V5 acceptance runner (platform-arch-unification-v5 Apply-4)
# Usage (repo root, conda ai_collection activated recommended):
#   bash scripts/smoke/v5_acceptance.sh
# Env for optional real bridge smoke:
#   RAG_SERVICE_URL=http://127.0.0.1:8002  (rag /health must be 200)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*"; exit 1; }
info() { echo "[INFO] $*"; }
block() { echo "[BLOCKER] $*"; }

echo "=== V5 acceptance $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

info "Mock health + Mock MCP batch0"
pytest services/complaint-service/tests/test_health.py \
       services/complaint-service/tests/test_mock_batch0.py -q --tb=line \
  || fail "mock suite"

info "Fail-fast + bridge httpx contract"
pytest services/complaint-service/tests/unit/test_mcp_real_path.py -q --tb=line \
  || fail "fail-fast / bridge contract"

info "Common transport/reliability/guardrails"
pytest common/tests/test_llm_transport.py \
       common/tests/test_llm_reliability.py \
       common/tests/test_guardrails.py -q --tb=line \
  || fail "common suite"

info "Doc greps (narrative)"
rg -n "USE_MOCK_MCP|RAG_SERVICE_URL|Mock vs Real" docs/complaint-service/external-deps/ >/dev/null \
  || fail "external-deps Mock vs Real missing"
rg -n "50|并发|容量" requirements_document/step1_项目总览 requirements_document/step3_架构设计 >/dev/null \
  || fail "capacity model keywords missing"

RAG_URL="${RAG_SERVICE_URL:-http://127.0.0.1:8002}"
info "Optional real bridge smoke against ${RAG_URL}"
code="$(curl -sS -m 3 -o /tmp/v5_rag_health.json -w '%{http_code}' "${RAG_URL}/health" 2>/dev/null || true)"
if [[ ! "$code" =~ ^[0-9]+$ ]]; then
  code="000"
fi
if [ "$code" != "200" ]; then
  block "rag-service not reachable at ${RAG_URL}/health (http=${code}). Skip live bridge."
  block "Reproduce: docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis milvus elasticsearch embedding rag-service"
  block "Then: RAG_SERVICE_URL=${RAG_URL} bash scripts/smoke/v5_acceptance.sh"
  echo "=== RESULT: mock+fail-fast PASS; real smoke BLOCKED ==="
  exit 0
fi

info "Live rag_kb bridge (USE_MOCK_MCP=false path in-process)"
export PYTHONPATH="${ROOT}/services/complaint-service:${PYTHONPATH:-}"
python - <<PY
import asyncio, os, sys
from app.mcp.real_transport import RealMCPTransport

base = os.environ.get("RAG_SERVICE_URL", "${RAG_URL}")

async def main():
    t = RealMCPTransport(rag_base_url=base, gateway_url="")
    regs = await t.call("rag_kb", "search_regulations", {"query": "催收时段", "top_k": 3})
    tpls = await t.call("rag_kb", "search_response_templates", {"query": "投诉答辩", "top_k": 3})
    assert isinstance(regs, list), type(regs)
    assert isinstance(tpls, list), type(tpls)
    for name, hits in (("regulations", regs), ("templates", tpls)):
        if hits:
            assert hits[0].get("doc_id"), f"{name} missing doc_id: {hits[0]}"
            print(f"[PASS] {name} hits={len(hits)} doc_id={hits[0]['doc_id']}")
        else:
            print(f"[PASS] {name} empty hits (HTTP OK, corpus may be empty)")

asyncio.run(main())
PY

echo "=== RESULT: mock+fail-fast+real smoke PASS ==="
