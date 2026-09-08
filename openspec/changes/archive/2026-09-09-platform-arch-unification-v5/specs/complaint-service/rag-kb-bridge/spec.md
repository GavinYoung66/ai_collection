## Purpose

Defines the bridge from complaint MCP `rag_kb` tools to rag-service HTTP search so real judgment can use live retrieval without graph nodes calling rag HTTP directly.

## ADDED Requirements

### Requirement: rag_kb search tools bridge to rag HTTP search

For real MCP mode, `rag_kb` tools used by judgment (`search_regulations` and `search_response_templates` at minimum) MUST be backed by a bridge that invokes rag-service knowledge-base search HTTP API and returns tool-shaped results consumable by existing graph nodes.

#### Scenario: Compliance requests regulations on real path

- **WHEN** real MCP/bridge is configured and compliance invokes `rag_kb.search_regulations`
- **THEN** the bridge performs an HTTP search against rag-service and returns regulation-oriented hits (or an explicit empty/degraded tool result on retrieval failure)

#### Scenario: Package generation requests templates on real path

- **WHEN** real MCP/bridge is configured and generate-package invokes `rag_kb.search_response_templates`
- **THEN** the bridge performs an HTTP search against rag-service and returns template-oriented hits usable for citation/`doc_id` checks

### Requirement: Bridge failures degrade safely

When the bridge cannot reach rag-service or search fails, MCP tool handling MUST surface a controllable error or empty result according to existing node degrade rules. It MUST NOT crash the whole process without recording failure context for the session/trace.

#### Scenario: rag-service unreachable

- **WHEN** the bridge HTTP call to rag-service times out or fails
- **THEN** the tool path returns a failed/empty outcome that graph nodes can degrade on, with traceable error information

### Requirement: Reserved unused rag_kb tools are explicit

If `search_complaint_handling_guidelines` (or successor) remains unused by graph nodes, docs and/or mock/real contracts MUST mark it as reserved-unused for this milestone, OR a minimal node MUST call it. Silent dual-hanging (contract present, never called, never labeled) is not allowed.

#### Scenario: Engineer checks guidelines tool status

- **WHEN** an engineer reviews `rag_kb` tool inventory after this change
- **THEN** each published tool is either wired to a caller or explicitly labeled reserved-unused for the milestone
