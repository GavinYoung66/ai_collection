## MODIFIED Requirements

### Requirement: Layered cross-service integration style

The platform SHALL use a layered integration style: Agent/workflow tool access MUST use MCP (or an equivalent tool facade); synchronous service management and CRUD APIs MUST use HTTP/JSON; asynchronous cross-service **business** notifications MUST use the unified RocketMQ event bus defined in `platform/event-bus`（运行时仅 `EVENT_BUS=rocketmq`）。MCP Servers MAY call downstream services over HTTP internally. Application code in one service MUST NOT import another service's internal packages. Event payloads MUST NOT carry business objects, PII, or large documents; subscribers SHALL retrieve full data through dedicated read-only HTTP endpoints (or existing Redis version keys where already specified) after receiving the event notification. Services MUST NOT use Redis Pub/Sub as the cross-service **business** bus. Redis MAY still be used for rate limiting, idempotency locks, version keys, and non-bus lists such as `csmp:callback:dlq`. Resilience/observability hooks (`csmp.*`) MUST follow the dual-track rule in `platform/event-bus`（不上业务 Topic）。

#### Scenario: Complaint agent retrieves regulation context

- **WHEN** complaint-service graph nodes need regulation or guideline materials during judgment
- **THEN** the node invokes MCP tool(s) under the `rag_kb` (or successor) server name and MUST NOT call `rag-service` HTTP routes directly from Agent node code

#### Scenario: Future service follows the same rulebook

- **WHEN** a new subsystem under `services/` needs another subsystem’s capability
- **THEN** designers MUST classify the need as Agent-tool (MCP), management API (HTTP), or async (RocketMQ business event bus) and MUST NOT invent a fourth ad-hoc style without updating this capability

#### Scenario: Event carries only IDs, subscriber pulls data

- **WHEN** `qc.check.completed` is published
- **THEN** envelope `data` carries only whitelist locator fields; data-platform subscriber SHALL call `GET /api/v1/qc/records/{session_id}` to retrieve score/risk/grade

#### Scenario: No Redis Pub/Sub as cross-service business bus

- **WHEN** a designer chooses how to notify another service asynchronously for business lifecycle
- **THEN** the path MUST be the RocketMQ event bus; Redis Pub/Sub MUST NOT be used for that purpose

## ADDED Requirements

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
