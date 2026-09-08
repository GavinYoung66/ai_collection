# complaint-service/mcp-real-path

## Purpose

Defines complaint-service behavior for Mock vs real MCP gateway modes, including fail-fast when real mode is selected without a working transport.

## Requirements

### Requirement: Standalone MCP transport is implemented for real mode

When complaint-service is configured for non-mock MCP with standalone (or equivalent remote) gateway mode, the MCP transport MUST be implemented. It MUST NOT raise an unimplemented stub error for supported tool calls used by judgment nodes (`rag_kb` regulation/template tools at minimum, once bridge is configured).

#### Scenario: Real mode tool call

- **WHEN** mock MCP is disabled, standalone/real gateway mode is enabled, and a supported `rag_kb` tool is invoked
- **THEN** the call is dispatched over the real transport path instead of failing with NotImplemented

### Requirement: Mock mode remains default-compatible

With mock MCP enabled, complaint judgment Demo paths that already work MUST continue to function without requiring `rag-service` or a remote MCP gateway.

#### Scenario: Default Demo with mock MCP

- **WHEN** an operator runs complaint-service with mock MCP enabled
- **THEN** MCP tool calls resolve via mock fixtures and the service can become healthy without rag-service

### Requirement: Real mode fails fast when dependencies are missing

When mock MCP is disabled, complaint-service MUST validate that required real-path configuration is present (gateway URL/mode and/or bridge endpoint as applicable). Missing or placeholder configuration MUST fail startup or refuse judgment MCP calls with an explicit configuration/dependency error, not a silent stub success.

#### Scenario: Mock disabled without gateway config

- **WHEN** `USE_MOCK_MCP=false` and required real MCP/bridge settings are missing or placeholders
- **THEN** the service fails fast or returns a clear dependency error on MCP use

### Requirement: Compose dependency semantics match mock vs real

Dev compose for mock MCP complaint demos MUST NOT require `rag-service` solely for health. Real-path deployments MAY depend on MCP gateway/bridge health; they MUST NOT reintroduce Agent-node direct coupling to rag containers as a substitute for MCP.

#### Scenario: Mock compose dependencies

- **WHEN** operators start complaint-service under mock MCP compose overlay
- **THEN** `rag-service` is not a hard `depends_on` requirement for complaint health
