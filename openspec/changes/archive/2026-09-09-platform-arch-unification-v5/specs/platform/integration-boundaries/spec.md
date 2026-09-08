## ADDED Requirements

### Requirement: Real MCP path is implementable without agent HTTP bypass

When Mock MCP is disabled and real MCP connectivity is configured, complaint-service Agent nodes MUST continue to obtain regulation/template evidence only via MCP tool calls (`rag_kb` or successors). The MCP gateway/server layer MAY HTTP-call `rag-service` internally. Graph/Agent node code MUST still NOT call `rag-service` HTTP routes directly.

#### Scenario: Real path judgment needs regulations

- **WHEN** `USE_MOCK_MCP` is false, real MCP/bridge is configured, and compliance node requests regulations
- **THEN** evidence is retrieved through MCP `rag_kb` tools and not via direct Agent-to-rag HTTP

### Requirement: Reliability and Guardrails sit above transport in the platform story

Architecture and integration docs MUST describe LLM concerns in layers: transport executes HTTP; reliability (retry/degrade) and Guardrails compose above transport; service providers own domain orchestration. Thin legacy `chat`/`chat_json` wrappers MAY remain for compatibility but MUST NOT be described as a second independent HTTP stack. Shared Redis (or equivalent) degrade flags MUST be described as the multi-instance authority when present.

#### Scenario: Reader checks LLM layering text

- **WHEN** an engineer reads platform integration boundary docs after this change
- **THEN** transport, reliability, Guardrails, and service providers are described as layered responsibilities rather than a single god Provider or dual raw HTTP stacks
