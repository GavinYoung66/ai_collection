## MODIFIED Requirements

### Requirement: Layered cross-service integration style

The platform SHALL use a layered integration style: Agent/workflow tool access MUST use MCP (or an equivalent tool facade); synchronous service management and CRUD APIs MUST use HTTP/JSON; asynchronous cross-service notifications MUST use the unified event bus defined in `platform/event-bus`（统一事件总线，含信封契约、适配器抽象、重试/DLQ）。MCP Servers MAY call downstream services over HTTP internally. Application code in one service MUST NOT import another service's internal packages. Event payloads MUST NOT carry business objects, PII, or large documents; subscribers SHALL retrieve full data through dedicated read-only HTTP endpoints after receiving the event notification.

#### Scenario: Complaint agent retrieves regulation context

- **WHEN** complaint-service graph nodes need regulation or guideline materials during judgment
- **THEN** the node invokes MCP tool(s) under the `rag_kb` (or successor) server name and MUST NOT call `rag-service` HTTP routes directly from Agent node code

#### Scenario: Future service follows the same rulebook

- **WHEN** a new subsystem under `services/` needs another subsystem's capability
- **THEN** designers MUST classify the need as Agent-tool (MCP), management API (HTTP), or async (event bus) and MUST NOT invent a fourth ad-hoc style without updating this capability

#### Scenario: Event carries only IDs, subscriber pulls data

- **WHEN** `qc.check.completed` is published
- **THEN** payload carries only `{qc_record_id, session_id, case_id, trace_id}`; data-platform subscriber SHALL NOT rely on embedded `score`/`risk`/`grade` but SHALL call `GET /api/v1/qc/records/{session_id}` to retrieve results
