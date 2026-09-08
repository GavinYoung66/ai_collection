## Purpose

Provides a minimal shared Guardrails surface so complaint and RAG do not each re-implement basic pre/post safety checks while still allowing domain rule registration per service.

## ADDED Requirements

### Requirement: Shared pre-check hook

The platform MUST expose a shared pre-check capability that can run sensitive-word (or equivalent) screening on text destined for LLM prompts. Services MAY register additional domain dictionaries. On blocking hits, callers MUST receive a structured deny/degrade signal and MUST NOT send the blocked raw text to the external LLM.

#### Scenario: Prompt contains blocked sensitive token

- **WHEN** a service runs shared pre-check on prompt text that matches a blocking entry
- **THEN** external LLM invocation is skipped or replaced by a documented degrade path

### Requirement: Shared post-check hook

The platform MUST expose a shared post-check capability for LLM outputs that supports at least: redline keyword screening and optional structured-field validation hooks. Domain-specific rules (e.g. allowed `doc_id` sets for complaint packages) MAY remain service-registered callbacks invoked by the shared hook.

#### Scenario: Output fails redline screening

- **WHEN** shared post-check detects a redline violation in model output
- **THEN** the caller receives a failure signal and MUST NOT treat the output as approved downstream content

### Requirement: Services compose shared Guardrails

First-party deep services that already perform answer/judgment safety checks (rag answer path and complaint L4-equivalent path) MUST compose the shared Guardrails hooks for the overlapping concerns, instead of maintaining a fully separate duplicate implementation of the same basic checks.

#### Scenario: Engineer audits safety call sites

- **WHEN** an engineer inspects rag answer generation and complaint package post-check paths after this change
- **THEN** both call into the shared Guardrails surface for the overlapping pre/post checks
