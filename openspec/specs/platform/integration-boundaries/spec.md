## Purpose

Defines cross-service integration style, configuration ownership, and shared LLM call-channel boundaries so seven AI subsystems stay decoupled without conflicting HTTP-vs-MCP or duplicate provider narratives.

## Requirements

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

### Requirement: Complaint lifecycle notify vs Agent write-back

Complaint lifecycle async signals (`complaint.created`, `complaint.result.ready`, and existing complaint **business** topics on the bus) MUST use the RocketMQ event bus with ID-only envelopes. Agent graph write-back of snapshot complaint columns MUST continue via MCP tools `write_session` / `write_human_review_truth` that HTTP PUT the data-platform snapshot API. `complaint.result.ready` MUST NOT replace `write_session` as the data-plane write. Resilience hooks MUST NOT be required to ride the business Topic.

#### Scenario: result.ready is notification after write_session

- **WHEN** sample_backflow successfully completes write_session
- **THEN** complaint-service publishes `complaint.result.ready` with whitelist IDs only, and the snapshot complaint columns were already written via MCP→HTTP

#### Scenario: Closed-loop path is event-driven, not DP HTTP orchestration

- **WHEN** the min-loop / batch closed loop creates and judges complaints
- **THEN** data-platform or scripts publish `complaint.created` and MUST NOT rely on HTTP create→judge→review as the primary orchestration path

### Requirement: Dual entry for complaint intake

The platform SHALL distinguish two intake paths: (1) EventBus `complaint.created` for closed-loop/simulation orchestration MUST create the ticket and start `judge(run_graph=true)`; (2) HTTP `POST /api/v1/complaints` for real business systems MUST create the ticket only and MUST NOT auto-start judge—callers use existing judge APIs. Both paths MUST share create idempotency on `case_id`.

#### Scenario: Event path auto-judges

- **WHEN** complaint-service consumes `complaint.created`
- **THEN** it creates (idempotent) and starts judge with `run_graph=true`

#### Scenario: HTTP path does not auto-judge

- **WHEN** a business system calls `POST /api/v1/complaints` successfully
- **THEN** a ticket exists and judge has NOT been started solely by that create call

### Requirement: Architecture docs match runtime integration

Canonical architecture documents and `openspec/specs/01_arch_constraint.md` MUST describe the layered integration style above. The step3 cross-module call matrix MUST NOT list complaint→rag / complaint→qc as direct Agent-path HTTP when the runtime Agent path is MCP-based.

#### Scenario: Reader checks complaint→rag edge

- **WHEN** an engineer reads the module call matrix for complaint-service evidence retrieval
- **THEN** the documented path identifies MCP (`rag_kb` / `call_qc` as applicable) with optional note that MCP Servers may HTTP-call downstream services

### Requirement: Mock-MCP complaint stack does not require rag-service container

While `USE_MOCK_MCP` is enabled for local/dev complaint demos, starting `complaint-service` MUST NOT hard-depend on `rag-service` (and its vector/search dependency chain) solely for judgment demos.

#### Scenario: Dev compose up complaint only

- **WHEN** an operator runs compose to start complaint-service with Mock MCP
- **THEN** postgres and redis (as declared for complaint) may start as dependencies, and rag-service MUST NOT be required via `depends_on` for the service to become healthy

### Requirement: Platform settings exclude product-specific RAG flags

Shared platform settings under `common` MUST contain only cross-service platform concerns (e.g. datastore hosts, shared secrets placeholders, logging/trace defaults). RAG product flags (`KB_*` and equivalents) MUST be owned by rag-service settings. Complaint-specific flags MUST remain on complaint settings.

#### Scenario: Complaint process loads settings

- **WHEN** complaint-service loads its configuration at startup
- **THEN** it MUST NOT require RAG-only `KB_*` product switches as part of the shared platform settings contract for correct operation

### Requirement: Shared LLM transport and reliability channel

All first-party services that call external LLM HTTP APIs MUST execute those HTTP calls through shared `common.llm.transport` helpers (`post_chat_completions` for OpenAI-compatible chat APIs; `post_json` or equivalent for vendor-native LLM endpoints). The transport layer MUST own request execution, timeout, and HTTP error wrapping. Key resolution, retry, and circuit-breaking/degradation MUST be composed by service-local providers (or shared wrappers such as `common.llm.provider` / `circuit_breaker`) above transport — they are NOT required to live inside the transport module itself. Service-local providers MAY own prompts, schemas, agent-role routing, and domain orchestration. Normative architecture text MUST describe this as “unified HTTP call channel + layered reliability”, not “a single god Provider class for all products”.

#### Scenario: Complaint and RAG both call DeepSeek-compatible APIs

- **WHEN** complaint-service and rag-service invoke a chat-completions compatible LLM endpoint
- **THEN** both MUST go through the shared transport channel for the HTTP call, while retaining their own orchestration/provider wrappers

#### Scenario: Vendor-native LLM HTTP (non chat-completions)

- **WHEN** a service calls a vendor-native LLM HTTP API that is not OpenAI chat-completions shaped (e.g. DashScope text-generation)
- **THEN** it MUST still use a shared common HTTP helper (e.g. `post_json`) rather than scattering raw httpx/SDK calls in business nodes

#### Scenario: Reliability stays above transport

- **WHEN** a provider needs API key resolution, retries, or circuit breaking around an LLM call
- **THEN** it MAY compose those concerns above `common.llm.transport` (including `common.llm.circuit_breaker` / service Redis degrade flags); transport itself is not required to implement key vaults or breakers

#### Scenario: Agent structured output stays service-local

- **WHEN** a complaint graph node requests a Pydantic-validated structured judgment object
- **THEN** schema validation and `agent_role` model routing MAY remain in complaint-service LLM layer above the shared transport

### Requirement: Real MCP path is implementable without agent HTTP bypass

When Mock MCP is disabled and real MCP connectivity is configured, complaint-service Agent nodes MUST continue to obtain regulation/template evidence only via MCP tool calls through the independent MCP gateway. The MCP gateway layer MAY HTTP-call `rag-service` internally. Graph/Agent node code MUST still NOT call `rag-service` HTTP routes directly. Agent node code MUST NOT embed in-process bridges that directly HTTP-call downstream services.

#### Scenario: Real path judgment needs regulations

- **WHEN** `USE_MOCK_MCP` is false, MCP gateway is configured, and compliance node requests regulations
- **THEN** evidence is retrieved through MCP gateway and not via direct Agent-to-rag HTTP or in-process bridges

### Requirement: Reliability and Guardrails sit above transport in the platform story

Architecture and integration docs MUST describe LLM concerns in layers: transport executes HTTP; reliability (retry/degrade) and Guardrails compose above transport; service providers own domain orchestration. Thin legacy `chat`/`chat_json` wrappers MAY remain for compatibility but MUST NOT be described as a second independent HTTP stack. Shared Redis (or equivalent) degrade flags MUST be described as the multi-instance authority when present.

#### Scenario: Reader checks LLM layering text

- **WHEN** an engineer reads platform integration boundary docs after this change
- **THEN** transport, reliability, Guardrails, and service providers are described as layered responsibilities rather than a single god Provider or dual raw HTTP stacks

### Requirement: MCP gateway standalone container

MCP gateway MUST be deployed as an independent Docker container. Business service processes MUST NOT embed MCP gateway logic or in-process MCP bridges. MCP gateway MUST own tool routing, reliability (circuit breaker / retry / timeout / rate limit), audit, and PII sanitization. Agent processes MAY retain thin timeout and PII secondary check as defense-in-depth.

#### Scenario: Gateway runs as independent container

- **WHEN** docker-compose up starts the platform
- **THEN** mcp-gateway runs as a separate container, not embedded in complaint-service or any other business service

#### Scenario: Business service does not embed MCP bridges

- **WHEN** complaint-service processes a judgment request with USE_MOCK_MCP=false
- **THEN** it calls the MCP gateway via MCP protocol, SHALL NOT have in-process bridge code that directly HTTP-calls downstream services
