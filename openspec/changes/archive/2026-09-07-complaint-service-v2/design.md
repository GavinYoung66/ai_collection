## Context

See `proposal.md` for motivation. V1 complaint-service at `services/complaint-service/` already supports create → `run_graph=true` → review → close on port **8006** with Mock MCP/LLM. V2 is an incremental overlay: richer seeds/fixtures, real LLM when `USE_MOCK_LLM=false`, and a minimal review UI. Spec contracts: `specs/complaint-service/v2-*` plus ADDED deltas on `llm-layer` / `mock-deps`.

Constraints: personal local project (Ellis Guo); keep Mock MCP; no Skill-as-runtime; do not rebuild LangGraph topology unless real LLM is blocked; secrets via env only.

## Goals / Non-Goals

**Goals:**

- Segmented delivery aligned with V2-1 → V2-2 → V2-3 → V2-4 (one OpenSpec change, apply in segments)
- Observable non-empty Mock evidence for true-graph runs
- Real model outputs distinguishable from Mock templates; success lands in `pending_review`
- Browser-complete approve **and** one of (`reject_rejudge` | `escalate`), plus close

**Non-Goals:**

- Real MCP / real RAG production KB / real biz push
- Heavy auth, batch ops console, interrupt↔frontend resume as acceptance gate
- Parallel root-level `complaint-service/` tree outside `services/`
- Vite/React/Vue/Gradio stacks (pinned to static page)

## Decisions

### D1: Single change, segmented apply

- **Choice**: One change `complaint-service-v2`; implement via `/opsx-apply` in three segments (seed → LLM → UI+acceptance).
- **Why**: Keeps one proposal/spec surface; reduces thrash vs three tiny changes.
- **Alternatives**: Three separate changes — rejected for overhead on a personal project.

### D2: Seed + fixtures under existing mock tree

- **Choice**: Extend `tests/mock/data/` and `tests/mock/mcp/fixtures/`; add `scripts/mock/seed_complaint_v2.*` (or documented curl batch) posting to 8006.
- **Why**: Reuses V1 loader paths; no new mock framework.
- **Alternatives**: Separate fixture service — rejected as overkill.

### D3: Real LLM via existing Provider + env flags

- **Choice**: Wire factory so `USE_MOCK_LLM=false` selects real `ComplaintLLMProvider`; keep `USE_MOCK_MCP=true`; document Key/base_url in `docs/complaint-service/`; fix `requirements.runtime.txt` if packages missing.
- **Why**: Matches V1 architecture and `model_router.yaml`.
- **Alternatives**: New LLM client module — rejected to avoid duplicate stacks.

### D4: Minimal static review UI (pinned)

- **Choice**: `services/complaint-service/web/` **static single-page** (vanilla HTML/JS, no build). Serve with `python -m http.server` (or any static file server). `API_BASE=http://localhost:8006`. Add CORS for local origin if needed.
- **Why**: Smallest path to browser acceptance; no npm toolchain.
- **Alternatives**: Vite/React/Vue/Gradio — deferred out of V2; full admin SPA — out of scope.

### D5: Review actions P0 = approve + one secondary

- **Choice**: P0 MUST implement `approve` and **exactly one** of `reject_rejudge` or `escalate` (implementer picks; default preference `reject_rejudge`). Close via `PUT .../close` after resolved.
- **Why**: Aligns with DD-05-V2 §3.3 / M3.5 V2-3.3; closes prior “optional if time” ambiguity.
- **Alternatives**: approve-only — rejected against PRD P0.

### D6: Acceptance as doc + smoke gate

- **Choice**: `docs/complaint-service/v2-acceptance.md` + existing `scripts/smoke/smoke_complaint_service.sh` regression.
- **Why**: Matches PRD REQ-CJ-V2-004 without new CI product.

## Risks / Trade-offs

- [Real LLM cost/latency] → Mitigation: seed only a few cases; keep Mock LLM as default for CI/smoke unless explicitly configured.
- [CORS/browser blocked] → Mitigation: document origin; add FastAPI CORSMiddleware for local only.
- [Fixtures still empty after seed] → Mitigation: V2-1 acceptance requires observable non-empty evidence before starting UI segment.
- [Docker missing LLM deps] → Mitigation: patch runtime requirements and rebuild as part of V2-2.
- [Scope creep into interrupt resume] → Mitigation: API review state machine remains acceptance path.

## Migration Plan

1. Apply segment A (V2-1): seeds + fixtures + seed script; verify Mock graph still works.
2. Apply segment B (V2-2): config docs + real Provider path + env secrets; verify one seed → `pending_review` with real LLM.
3. Apply segment C (V2-3 + V2-4): static `web/` + acceptance doc + smoke.
4. Rollback: set `USE_MOCK_LLM=true`; stop frontend; seeds are additive data only.

Shared verify commands live in `tasks.md`（环境与验证命令）；do not invent alternate ports.

## Open Questions

None — frontend stack and secondary review action policy resolved in D4/D5.
