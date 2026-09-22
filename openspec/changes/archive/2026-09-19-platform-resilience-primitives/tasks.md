## 1. Scaffold resilience package

- [x] 1.1 Create `common/resilience/__init__.py` and verify `python -c "import common.resilience"` succeeds
- [x] 1.2 Create `common/mcp/__init__.py` (if not exists) and verify `python -c "import common.mcp"` succeeds

## 2. Implement atomic parts (each with unit tests)

- [x] 2.1 Implement `common/resilience/rate_limit.py` `allow(key, limit, window=60) -> bool` (Redis INCR+EXPIRE, Redis-down silent allow) and verify unit test: under-limit→True, over-limit→False, Redis-down→True
- [x] 2.2 Implement `common/resilience/concurrency.py` `Semaphore(max_inflight)` with `async acquire()->bool`/`release()` (False when full, non-blocking) and verify unit test: slot available→True, full→False, release→slot frees
- [x] 2.3 Implement `common/resilience/queue.py` `BoundedQueue(max_len)` with `async push(item)->bool`/`async pop(timeout)->Optional` (False when full, no silent drop) and verify unit test: under-cap→True, full→False, pop returns item or None
- [x] 2.4 Implement `common/resilience/idempotency.py` `acquire(key, ttl=1800)->bool` (Redis SETNX+TTL, Redis-down conservative False) and verify unit test: first→True, duplicate→False, Redis-down→False
- [x] 2.5 Implement `common/resilience/breaker.py` `CircuitBreaker(key, fail_max=5, reset_timeout=30)` state machine (closed→open→half-open→closed/open, half-open 1 probe) and verify unit test: fail_max→open, half-open 1 probe, probe success→closed, probe fail→open
- [x] 2.6 Implement `common/resilience/hooks.py` `async on_event(name, payload)` (name=`{svc}.{component}.{action}`, default structlog, no publisher required) and verify unit test: event logged, no publisher→no raise
- [x] 2.7 Implement `common/resilience/timeout.py` `@dataclass Timeout(connect=5.0, read=60.0, total=120.0)` and verify unit test: dataclass fields and defaults correct

## 3. Implement LLM reliability entry

- [x] 3.1 Add `invoke_llm(model, messages, *, fallback_chain=None, timeout=Timeout(), breaker_key=None) -> dict` to `common/llm/reliability.py` (orchestration: key resolve → rate check → breaker check → bounded retry business-only, no retry on timeout → fallback swap → degrade; auto-derive `{svc}:llm_degraded:{model}` when breaker_key None) and verify unit test: primary fail→fallback succeeds, timeout→no retry, all fail→degrade flag set
- [x] 3.2 Switch `common/llm/provider.py` `chat`/`chat_json` to route through `invoke_llm` (or per-key breaker from `common.resilience.breaker`) instead of global `@llm_breaker`, and verify no global `csmp:llm_degraded=1` key is set per model

## 4. Implement MCP reliability entry

- [x] 4.1 Implement `common/mcp/reliability.py` `call_tool(server, tool, payload, *, timeout=Timeout(total=30.0), breaker_key=None) -> dict` (error grading: timeout→no retry+mark degraded, permission deny→no retry+terminate, business-retryable→retry 3x; per-server breaker key `{svc}:mcp_degraded:{server}`) and verify unit test: timeout→no retry+degraded, permission→no retry+terminate, business→retry 3x, breaker open→reject

## 5. Migrate rag-service rate limit

- [x] 5.1 Rewrite `services/rag-service/app/services/rate_limit.py` to delegate to `common.resilience.rate_limit.allow` (keep thresholds 20/60/200, X-User-Id→IP fallback, Redis-down silent allow, B0006/429 contract) and verify unit test: behavior identical to pre-migration (under-limit allow, over-limit 429+B0006, Redis-down allow, user-dimension keying)

## 6. Register error codes

- [x] 6.1 Register resilience error codes A5015 (Token 超限→转人审) / A5016 (任务超时→转人审) / A5017 (LLM 熔断→转人审·规则草稿) in `common/errors/codes.py` (extend A5 域末尾; A5009-A5011 already occupied); verify `codes.py` enum and ERROR_MESSAGES updated consistently and `python -c "from common.errors.codes import ErrorCode; print(ErrorCode.A5015.value, ErrorCode.A5016.value, ErrorCode.A5017.value)"` prints `A5015 A5016 A5017`

## 7. Acceptance verification

- [x] 7.1 Verify all `common/resilience/` parts + `common/llm/reliability.py` + `common/mcp/reliability.py` import cleanly: `python -c "import common.resilience.rate_limit, common.resilience.concurrency, common.resilience.queue, common.resilience.idempotency, common.resilience.breaker, common.resilience.hooks, common.resilience.timeout; import common.llm.reliability, common.mcp.reliability"`
- [x] 7.2 Run full unit test suite for resilience parts and verify all green: rate-limit hit→False/B0006, breaker open→reject, breaker half-open→1 probe→closed, fallback swap→primary fail+backup success, queue full→False no drop, idempotency→duplicate False, MCP timeout→no retry+degraded, MCP permission→no retry+terminate
- [x] 7.3 Verify rag-service rate limit migration behavior unchanged: thresholds 20/60/200, user dimension (X-User-Id→IP), Redis-down silent allow (run rag rate_limit unit tests, pre/post behavior diff = none)
- [x] 7.4 Verify breaker key isolation: open `csmp:mcp_degraded:rag_kb` and confirm `qc:llm_degraded:*` calls unaffected (no single key disables whole platform)
