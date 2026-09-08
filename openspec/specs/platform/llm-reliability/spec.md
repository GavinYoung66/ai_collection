# platform/llm-reliability

## Purpose

Defines shared LLM call reliability above transport so first-party services compose retry and degrade behavior instead of scattering duplicate loops and incompatible breaker semantics.

## Requirements

### Requirement: Shared reliability helpers above transport

The platform MUST provide shared reliability helpers in `common` for external LLM HTTP calls that compose with `common.llm.transport`. Helpers MUST support bounded retry and a Redis-backed (or equivalently shared) degrade flag convention. Service-local providers MUST use these helpers for retry/degrade when calling external LLM HTTP APIs, and MUST NOT introduce a third raw HTTP client path for the same concern.

#### Scenario: Complaint and RAG both hit transient LLM HTTP failure

- **WHEN** complaint-service and rag-service invoke external LLM HTTP APIs under failure
- **THEN** both apply shared retry/degrade conventions from `common` while retaining service-local orchestration (routing, schema, citations)

### Requirement: Degrade key naming is service-scoped

Shared reliability conventions MUST allow service-scoped degrade keys (e.g. distinct prefixes for rag vs complaint) so one service’s breaker does not silently disable another.

#### Scenario: RAG opens LLM degrade flag

- **WHEN** rag-service marks its LLM path degraded
- **THEN** complaint-service LLM path is not required to be degraded solely because of the rag key

### Requirement: Placeholder secrets fail closed on real path

When a service is configured for real LLM calls (mock disabled), placeholder API keys such as `change_me` MUST be treated as unset. The service MUST fail fast at startup or refuse real invocations with a clear configuration error, rather than calling vendors with placeholder credentials.

#### Scenario: Real LLM enabled with placeholder key

- **WHEN** mock LLM is disabled and the configured API key is a known placeholder
- **THEN** the service does not perform a successful vendor call with that placeholder

### Requirement: Legacy chat entry vs preferred call path

`common` MAY keep thin `chat` / `chat_json` wrappers for compatibility, but normative guidance MUST treat shared transport helpers plus reliability composition as the preferred path for new call sites. Services MUST NOT add a third raw HTTP/SDK channel for the same LLM HTTP concern.

#### Scenario: New call site is added in a deep service

- **WHEN** a developer adds a new external LLM HTTP call in rag-service or complaint-service
- **THEN** the implementation uses shared transport (and reliability helpers) rather than a new ad-hoc httpx/SDK client

### Requirement: Redis degrade is multi-instance authority

When both process-local circuit breakers and Redis (or equivalent shared) degrade flags exist, the shared degrade flag MUST be the authority for multi-instance degrade decisions. Process-local breakers MUST NOT silently disagree in a way that leaves one instance calling vendors while others are degraded for the same service-scoped key.

#### Scenario: One instance trips shared degrade

- **WHEN** a service-scoped Redis degrade flag is set for the LLM path
- **THEN** other instances of that service honor the degraded state for that path
