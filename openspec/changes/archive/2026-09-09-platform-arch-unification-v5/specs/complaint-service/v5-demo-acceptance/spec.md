## Purpose

Defines V5 acceptance gates for architecture unification: narrative checks, mock/real path smoke, and optional evidence traceability visibility for Demo.

## ADDED Requirements

### Requirement: V5 acceptance checklist is executable

The change MUST provide an executable acceptance checklist or script covering at least: (1) mock MCP Demo still healthy, (2) real-path config fail-fast when misconfigured, (3) real-path bridge smoke when rag-service and bridge are available, (4) narrative/doc grep gates for RAG-not-in-common and capacity consistency markers agreed in tasks.

#### Scenario: Maintainer runs V5 acceptance

- **WHEN** a maintainer runs the documented V5 acceptance commands
- **THEN** each gate reports pass/fail without requiring undocumented manual folklore

### Requirement: Evidence references visible on judgment outputs (stretch, non-batch-mandatory)

**Stretch only** (interview bonus; NOT part of original Batch A–G mandatory set; NOT Batch-D completion): When stretch work is enabled, judgment outputs exposed to Demo/review UI or API MUST include visible evidence references (`doc_id` / evidence ref list) when citations exist.

#### Scenario: Reviewer inspects approved package with citations

- **WHEN** a judgment package includes RAG-backed citations and stretch work is enabled
- **THEN** API or Demo UI surfaces the evidence reference identifiers to the reviewer

### Requirement: Call-log sink shape (stretch, Batch-D minimum slice)

**Stretch only** (Batch-D minimum slice; does NOT complete ENG-02/07 or COMP-01/02): MCP and/or LLM call logs MUST be able to write through a pluggable sink interface (file or DB shape), even if the default remains in-memory for Demo.

#### Scenario: Sink is registered in test

- **WHEN** a test registers a file/temp sink for LLM or MCP call logs
- **THEN** a successful invoke records an entry via that sink

### Requirement: Prompt and vendor endpoints are not hard-coded in transport

Business system prompts for real LLM judgment MUST be loaded from the prompt/config path (not inlined as large hard-coded strings inside the HTTP transport module). Vendor base URLs and keys MUST come from configuration.

#### Scenario: Engineer opens real LLM transport module

- **WHEN** an engineer inspects the real LLM transport after this change
- **THEN** it does not contain the primary business system prompt body as a hard-coded multi-paragraph string, and vendor endpoints are configuration-driven
