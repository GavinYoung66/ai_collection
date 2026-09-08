## Context

See proposal.md — Why. Current constraints that shape approach:

- `common.llm.transport` (`post_chat_completions` / `post_json`) already exists; do not replace it.
- complaint graph nodes already call `mcp.call("rag_kb", …)`; MockMCPClient fixtures work for Demo.
- `MCPGateway` standalone transport is still unimplemented; prod-like env can disable mock and hit stubs.
- rag-service exposes `POST /api/v1/kb/search`; complaint must not import rag packages.
- Deep services: rag + complaint; five others remain scaffolds this milestone.
- Review deltas applied: batch letter alignment (A–G), DEBT-03, DEP-05 checklist, PLAT-01/02 decisions, Batch-D boundary.

## Goals / Non-Goals

**Goals:**

- One written narrative for RAG ownership, scaffold depth, and MVP **capacity model table**.
- Switchable real MCP path: gateway/bridge → rag HTTP; graph nodes unchanged.
- Shared reliability + Guardrails; **both** deep services wire at least one call site each.
- Fail-fast when real mode selected without config; mock Demo non-regressive.
- Lightweight `docs/platform/new-service-checklist.md` as DEP-05 anchor (no data-platform APIs).
- Resolve DEBT-03 shadow `rag-service/common/` and empty `complaint-service-v5` change folder confusion.

**Non-Goals:**

- Batch-E flesh (ARCH-04, COMP-07); Batch-D full (ENG-02/07, COMP-01/02); Batch-G entire set.
- Kafka/KMS/Nacos/k8s; merging rag+complaint; claiming observability “done” via ENG-01 stretch alone.

## Decisions

### D1 — Change name vs complaint milestone

- **Choice:** Change id `platform-arch-unification-v5`. Complaint Demo gates under `complaint-service/v5-demo-acceptance`.
- **Alt:** `complaint-service-v5` only — rejected (spans common + rag + docs).
- **Hygiene:** Empty `openspec/changes/complaint-service-v5/` MUST be deleted or marked obsolete in a task so apply does not pick the wrong folder.

### D2 — Bridge placement: in-process adapter vs separate MCP server process

- **Choice:** In-process / sidecar-light bridge behind complaint MCP gateway → HTTP rag-service.
- **Alt:** Separate `mcp-server-rag-kb` container — follow-up.
- **Constraint:** Graph nodes never gain rag HTTP clients.

```text
[compliance / generate_package]
        │ mcp.call(rag_kb, tool, args)
        ▼
[MCPClient] → [Gateway transport]
        │  Mock: fixtures
        │  Real: rag_kb bridge
        ▼
[HTTP POST rag-service /api/v1/kb/search]
        ▼
[tool-shaped results → node degrade rules]
```

### D3 — Reliability module shape

- **Choice:** `common/llm/reliability.py` with retry + Redis degrade helpers; service-owned key prefixes (`rag:…`, `csmp:…`).
- **Alt:** One global pybreaker for all services — rejected.

### D4 — Guardrails minimal surface

- **Choice:** `common/guardrails` `precheck_text` / `postcheck_text` + domain callbacks. complaint L4 and rag `llm_service` both compose shared hooks.
- **Alt:** Move all L4 into common — rejected.

### D5 — Prompt relocation

- **Choice:** Move RealLLMTransport inline system prompt to prompt/YAML loader. Transport only sends `messages`.

### D6 — Fail-fast policy

- **Choice:** On `USE_MOCK_MCP=false` and/or `USE_MOCK_LLM=false`, validate non-placeholder secrets and required URLs at startup or first real invoke (hard error). Prefer startup for MCP gateway URL when mock off.

### D7 — Documentation edits

- **Choice:** Patch step3 `01`/`02` + external-deps; add **capacity model** table (outbound ~50 concurrent vs QC throughput claim reconciled or explicitly separated); no unverified 100k facts.

### D8 — Reserved MCP tool

- **Choice:** Label `search_complaint_handling_guidelines` reserved-unused unless trivial wire.

### D9 — Dual LLM entry + breaker semantics (PLAT-01 / PLAT-02)

- **Choice:**
  - `common.llm.provider.chat` / `chat_json` = **legacy thin wrapper** over transport + reliability; new service code SHOULD prefer `post_chat_completions`/`post_json` + reliability directly (or service RealLLMTransport).
  - **Redis degrade flags are authoritative across instances**; process-local `pybreaker` (`common.llm.circuit_breaker`) is optional single-process assist only — must not contradict Redis state, and must not be the sole multi-instance control plane.
- **Alt:** Delete `chat_json` this milestone — rejected (compat); document + stop new call sites.

### D10 — DEP-05 without Batch-E flesh

- **Choice:** Deliver `docs/platform/new-service-checklist.md` covering: MCP vs HTTP vs events, PlatformSettings + XxxSettings, error-code band, must-use common (transport/reliability/guardrails/security/trace). No data-platform API implementation.
- **Alt:** Silent “extension points” in Why only — rejected (R4).

### D11 — DEBT-03 shadow package

- **Choice:** Remove or quarantine `services/rag-service/common/` so only repo-root `common/` is importable; add grep gate in acceptance.
- **Alt:** Defer to Non-Goals — rejected (was silent gap).

### D12 — Batch-D / COMP-03 labeling

- **Choice:** Stretch § = “D 的最小切片（ENG-01）+ 非 A–G 必选加分（COMP-03）”; never market as observability/compliance complete.

## Risks / Trade-offs

- [Bridge shape ≠ mock] → Contract tests on required keys.
- [Real path needs rag up] → fail-fast always; live smoke optional.
- [Guardrails extraction breaks L4] → domain callbacks + golden tests.
- [Wrong OpenSpec folder] → D1 hygiene task.
- [Checklist mistaken for data-platform done] → title “onboarding checklist”, Non-Goals ARCH-04.

## Migration Plan

1. Docs + common helpers (no behavior change until call sites switch).
2. Wire **both** rag and complaint call sites; mock default.
3. Gateway real transport + bridge; env feature flags.
4. Fail-fast validators; compose comments/depends; DEBT-03 cleanup.
5. New-service checklist + V5 acceptance; rollback = `USE_MOCK_MCP=true`.

## Open Questions

- Exact HTTP category filters for `search_response_templates` — finalize at apply against rag search API.
- compose.complaint stub rag: document or remove false `depends_on` during task 4.4 (not a spec fork).
