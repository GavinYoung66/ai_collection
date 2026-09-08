## 1. Phase A — Integration narrative + compose

- [x] 1.1 Update `requirements_document/step3_架构设计/02_模块调用关系与流式 RTC 管道设计.md`: revise complaint→rag / complaint→qc matrix rows to MCP Agent-tool path (note MCP MAY HTTP downstream); add Agent/MCP row in §三 通信协议
- [x] 1.2 Update `requirements_document/step3_架构设计/01_整体技术架构 (含大模型 Guardrails 防护栏).md`: add short 集成分层准则 (MCP / HTTP / 事件)
- [x] 1.3 Update `openspec/specs/01_arch_constraint.md`: tighten 强制6 (gateway/public API includes layered MCP|HTTP|events; still ban cross-service import); rewrite 强制8 to unified transport+reliability (not single god Provider)
- [x] 1.4 Remove `rag-service` from `complaint-service.depends_on` in `docker-compose.dev.yml`; add comment that Mock MCP demos do not need rag stack
- [x] 1.5 Update `docs/complaint-service/external-deps/README.md` (or equivalent) noting Mock MCP ⇒ no rag container required for complaint demos

## 2. Phase B — Settings ownership

- [x] 2.1 Add rag-owned settings module (`RagSettings` or equivalent) holding all `KB_*` flags currently on `common.config.settings.Settings`
- [x] 2.2 Point rag-service runtime reads of `KB_*` at rag settings (dual-read from common allowed temporarily if needed)
- [x] 2.3 Remove RAG-only `KB_*` fields from `common/config/settings.py` once dual-read unused; keep platform fields only
- [x] 2.4 Segment `.env.example` with `# --- platform ---` / `# --- rag ---` / `# --- complaint ---` comments; confirm `ComplaintSettings` remains complaint-local

## 3. Phase C — Shared LLM transport

- [x] 3.1 Extract shared OpenAI-compatible HTTP transport (+ key/base_url resolution hooks) under `common/llm/`
- [x] 3.2 Refactor `services/complaint-service/app/llm/real_transport.py` to use shared transport; keep ModelRouter / schema / agent_role in complaint
- [x] 3.3 Refactor rag `llm_providers` to use shared transport for chat-completions calls; keep answer gating/citations orchestration local
- [x] 3.4 Smoke: complaint health + optional `scripts/mock/verify_complaint_v2_real_llm.sh` when keys present; rag unit/smoke touching LLM path still pass

## 4. Validation / handoff

- [x] 4.1 Grep gate: no `depends_on: rag-service` under complaint in `docker-compose.dev.yml`; step3 matrix no longer claims Agent-path HTTP for complaint evidence
- [x] 4.2 Grep gate: `KB_` product flags not defined on common Settings (or only deprecated aliases documented)
- [x] 4.3 Record follow-ups out of scope: real MCP→HTTP adapter, compose `with-rag` profile, PG schema split
