## Context

See proposal.md — Why. Today: complaint Agent uses MCP (often Mock); step3 matrix still says HTTP to rag/qc; `common/config/settings.py` holds many `KB_*`; LLM stacks exist in `common/llm`, rag `llm_providers`, and complaint `app/llm`. Only rag + complaint are deep; five services are scaffolds.

## Goals / Non-Goals

**Goals:**

- One written integration rulebook (MCP / HTTP / events) aligned with complaint reality
- Dev compose no longer forces rag stack for Mock complaint demos
- Settings ownership split (platform vs rag vs complaint)
- Shared LLM transport under `common`; thin service providers

**Non-Goals:**

- Implementing real MCP Servers or wiring Mock→HTTP adapters in this change (document the pattern only)
- Splitting Postgres instances / full schema migration tooling
- Merging rag answer orchestration and complaint multi-agent providers into one class
- Changing LangGraph topology or V4 disposition APIs

## Decisions

### D1 — Layered integration (not “HTTP only” or “MCP only”)

- **Choice:** Agent tools = MCP; management APIs = HTTP; async = events; MCP may HTTP downstream.
- **Why:** Matches complaint PRD + LangGraph; keeps normal microservice APIs; avoids forcing CRUD through MCP.
- **Alternatives:** (a) Rewrite complaint to direct HTTP — discards working Agent tool model; (b) Everything MCP — poor fit for admin/CRUD.

### D2 — Doc + compose first, then settings, then transport

- **Choice:** Phase A docs/`01_arch_constraint`/compose; Phase B settings move; Phase C transport extract.
- **Why:** Lowest risk; stops narrative drift before coding five more services.
- **Alternatives:** Big-bang refactor — high blast radius for little demo gain.

### D3 — Compose: remove complaint→rag `depends_on` under Mock

- **Choice:** Delete hard dependency in `docker-compose.dev.yml`; document that true MCP/`rag_kb` later may add profile-based depends.
- **Why:** Mock path never needs milvus/ES/embedding for judgment demos.
- **Alternatives:** Compose profiles `complaint-mock` / `complaint-with-rag` — nicer later; not required for Phase A.

### D4 — Settings: dual-read then delete

- **Choice:** Introduce/own `RagSettings` for `KB_*`; temporarily allow reading from common if unset; then remove from common.
- **Why:** Avoids one-shot breakage of rag env wiring.
- **Alternatives:** Hard cut — faster but riskier.

### D5 — LLM: unify transport, not providers

- **Choice:** Extract httpx OpenAI-compatible client (+ key/base URL) into `common.llm`; complaint `RealLLMTransport` and rag providers call it; keep complaint `ModelRouter` / schema retries and rag answer gating local.
- **Why:** Honors “no naked LLM calls” without a god class.
- **Alternatives:** Force all through `common.llm.provider.chat_json` only — poorly fits role routing and rag citation flows.

### D6 — Normative text updates

- **Choice:** Amend `01_arch_constraint` 强制6/8 wording; update step3 §二 matrix rows for complaint→rag/qc; add protocol row for Agent/MCP in §三.
- **Files:** listed in tasks.md.

## Risks / Trade-offs

- [Docs drift again] → Keep matrix and constraint file in same PR as compose change
- [Settings dual-read linger] → Task checklist requires deletion step + grep gate for `KB_` in common
- [Transport extract subtle behavior change] → Keep complaint real-LLM verify script as acceptance; no API contract change
- [Operators expect rag on `up complaint`] → Document in external-deps README / compose comment

## Migration Plan

1. Merge Phase A (docs + compose) independently — rollback = restore depends_on + docs
2. Phase B behind dual-read — rollback = keep common KB_* aliases
3. Phase C behind existing `USE_MOCK_LLM` — rollback = complaint local transport copy

## Open Questions

- Whether to add compose profile `with-rag` in a follow-up (defer)
- Exact package path name `common.llm.transport` vs extending `provider.py` (implementer choice; behavior-spec unchanged)

## Follow-ups (explicitly out of this change)

- Real MCP Server with HTTP adapter to rag-service / qc-service
- Compose profile `with-rag` for optional full stack demos
- PG schema / migration stream split per service
