## MODIFIED Requirements

### Requirement: Layered cross-service integration style

The platform SHALL use a layered integration style: Agent/workflow tool access MUST use MCP via the independent MCP gateway service; synchronous service management and CRUD APIs MUST use HTTP/JSON; asynchronous cross-service **business** notifications MUST use the unified RocketMQ event bus defined in `platform/event-bus`(运行时仅 `EVENT_BUS=rocketmq`)。MCP Servers MAY call downstream services over HTTP internally. Application code in one service MUST NOT import another service's internal packages. Event payloads MUST NOT carry business objects, PII, or large documents; subscribers SHALL retrieve full data through dedicated read-only HTTP endpoints after receiving the event notification. Services MUST NOT use Redis Pub/Sub as the cross-service **business** bus. Resilience/observability hooks(`csmp.*`)MUST follow the dual-track rule in `platform/event-bus`(不上业务 Topic)。

Agent 调用下游工具 SHALL 经独立 MCP 网关路由,SHALL NOT 在业务进程内嵌 MCP 桥直接 HTTP 调下游。MCP 网关 SHALL 作为独立容器部署,SHALL NOT 嵌入业务服务进程。

#### Scenario: Complaint agent retrieves regulation context

- **WHEN** complaint-service graph nodes need regulation or guideline materials during judgment
- **THEN** the node invokes MCP tool(s) via the independent MCP gateway, and MUST NOT call `rag-service` HTTP routes directly from Agent node code

#### Scenario: MCP gateway routes to downstream

- **WHEN** Agent calls MCP tool via gateway
- **THEN** gateway routes to appropriate downstream service(内嵌工具 HTTP 调下游 or MCP protocol to qc-service),Agent SHALL NOT know the downstream address

#### Scenario: Future service follows the same rulebook

- **WHEN** a new subsystem under `services/` needs another subsystem's capability
- **THEN** designers MUST classify the need as Agent-tool (MCP via gateway), management API (HTTP), or async (RocketMQ business event bus)

#### Scenario: Event carries only IDs, subscriber pulls data

- **WHEN** `qc.check.completed` is published
- **THEN** envelope `data` carries only whitelist locator fields; data-platform subscriber SHALL call `GET /api/v1/qc/records/{session_id}` to retrieve score/risk/grade

#### Scenario: No Redis Pub/Sub as cross-service business bus

- **WHEN** a designer chooses how to notify another service asynchronously for business lifecycle
- **THEN** the path MUST be the RocketMQ event bus; Redis Pub/Sub MUST NOT be used for that purpose

### Requirement: Real MCP path is implementable without agent HTTP bypass

When Mock MCP is disabled and real MCP connectivity is configured, complaint-service Agent nodes MUST continue to obtain regulation/template evidence only via MCP tool calls through the independent MCP gateway. The MCP gateway layer MAY HTTP-call `rag-service` internally. Graph/Agent node code MUST still NOT call `rag-service` HTTP routes directly. Agent node code MUST NOT embed in-process bridges that directly HTTP-call downstream services.

#### Scenario: Real path judgment needs regulations

- **WHEN** `USE_MOCK_MCP` is false, MCP gateway is configured, and compliance node requests regulations
- **THEN** evidence is retrieved through MCP gateway and not via direct Agent-to-rag HTTP or in-process bridges

## ADDED Requirements

### Requirement: MCP gateway standalone container

MCP gateway MUST be deployed as an independent Docker container. Business service processes MUST NOT embed MCP gateway logic or in-process MCP bridges. MCP gateway MUST own tool routing, reliability (circuit breaker / retry / timeout / rate limit), audit, and PII sanitization. Agent processes MAY retain thin timeout and PII secondary check as defense-in-depth.

#### Scenario: Gateway runs as independent container

- **WHEN** docker-compose up starts the platform
- **THEN** mcp-gateway runs as a separate container, not embedded in complaint-service or any other business service

#### Scenario: Business service does not embed MCP bridges

- **WHEN** complaint-service processes a judgment request with USE_MOCK_MCP=false
- **THEN** it calls the MCP gateway via MCP protocol, SHALL NOT have in-process bridge code that directly HTTP-calls downstream services
