# platform/architecture-narrative

## Purpose

Locks project-wide architecture narrative and capacity messaging so Demo, docs, and interview claims stay consistent with the monorepo’s real implementation depth.

## Requirements

### Requirement: RAG is a business subsystem not a common library

Canonical architecture documents and README-level project summaries MUST describe `rag-service` (port 8002) as an independently deployable business subsystem. Shared platform packages under `common` MUST NOT be described as containing the RAG product (ingest, hybrid search, Gradio). Shared pieces are limited to cross-cutting concerns (e.g. LLM HTTP transport, security, tracing).

#### Scenario: Reader checks public capability layer diagram

- **WHEN** an engineer reads the overall architecture description of the “public capability layer”
- **THEN** RAG product capabilities are attributed to `rag-service`, not to `common`

### Requirement: Implementation depth messaging

Project overviews used for Demo and career materials MUST state that rag-service and complaint-service are deep implementations while the other five subsystems MAY be health-only scaffolds until their milestones. Scaffold services MUST NOT be described as having full business APIs.

#### Scenario: Stakeholder asks whether seven systems are production-complete

- **WHEN** someone reads the project overview after this change
- **THEN** the text distinguishes deep slices vs placeholder services and does not claim all seven are feature-complete

### Requirement: Single capacity narrative

Documented concurrency and scale numbers for MVP/Demo MUST align across step1 overview, step3 architecture, and complaint DD capacity sections. Canonical docs MUST include a short **capacity model** table (or equivalent) that separates outbound call concurrency from QC/transcription throughput when both numbers appear. Unverified claims such as “100k tickets/month” or “100k knowledge documents” MUST NOT appear as verified project facts unless separately evidenced.

#### Scenario: Capacity numbers compared across docs

- **WHEN** an engineer compares outbound concurrency and QC throughput claims across canonical docs
- **THEN** the numbers are consistent **or** explicitly labeled as distinct link types in a capacity model table (e.g. outbound call concurrency vs offline QC throughput)

### Requirement: New-subsystem onboarding checklist (documentation)

The repository MUST provide a maintainer-facing checklist document for adding a new subsystem under `services/` that covers at least: integration style choice (MCP vs HTTP vs events), settings ownership (platform vs service), error-code band, and mandatory use of shared `common` capabilities (transport/reliability/guardrails/security/trace as applicable). This checklist DOES NOT require implementing scaffold business APIs or data-platform features.

#### Scenario: Engineer plans a sixth business service

- **WHEN** an engineer prepares to implement a previously scaffold-only subsystem
- **THEN** they can follow the checklist document without inventing a fourth ad-hoc integration style

### Requirement: Judgment Demo path requires human review

Demo and acceptance instructions for complaint judgment MUST require a full graph run with human-review interrupt (or equivalent explicit human gate). Default stub-only judge paths MUST NOT be presented as complete liability determination.

#### Scenario: Operator follows V5 Demo script

- **WHEN** an operator runs the documented judgment Demo path
- **THEN** instructions require `run_graph=true` (or successor flag) and a human-review step before treating disposition as final
