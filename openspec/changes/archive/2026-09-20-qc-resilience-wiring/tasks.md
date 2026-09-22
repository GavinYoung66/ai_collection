## 1. 启动与配置

- [x] 1.1 在 `app/core/config.py` 增加 `QC_MAX_INFLIGHT`（默认 3）、`QC_LLM_FALLBACK_CHAIN`（默认 `["qwen-turbo"]`）、`QC_INFLIGHT_WAIT_TIMEOUT`（默认 0=无限等）；保持 `QC_LLM_MODEL`/`QC_LLM_TIMEOUT=5`；更新 `tests/test_config.py` 断言默认值
- [x] 1.2 在 `app/main.py` lifespan 调用 `set_default_service("qc")`；单测或启动探测断言服务名前缀用于熔断/限流 key

## 2. raw 限流

- [x] 2.1 将 `raw_rate_limit` 改为 `await rate_limit.allow(f"qc:ratelimit:raw:{user}", limit=10, window=1, context={"trace_id": ...})`，`False` 抛 `BizException(B0006)`；用户维仍 `X-User-Id` → IP → `anon`
- [x] 2.2 更新 raw 限流单测：1s 内第 11 次 → B0006；mock Redis 不可用 → 放行且不抛 B0006；超限事件 payload 含 `trace_id`。验证 `PYTHONPATH=services/qc-service python -m pytest services/qc-service/tests/test_replay_api.py -k rate -q`

## 3. LLM 入口与熔断分片

- [x] 3.1 改 `llm_reviewer.py` 走 `invoke_llm(..., fallback_chain=..., timeout=Timeout(total=QC_LLM_TIMEOUT), transport=json_object 适配器)`；删除外层 `call_with_retry` 与 `QcLlmCircuit`；捕获整链失败 / 熔断 / `LLMRateLimitError` → 跳过 LLM hits + A2011 + `system.alert.raised`
- [x] 3.2 删除 `app/services/llm_circuit.py` 引用；`QcJobStore` 去掉全局 `qc:llm_degraded`；health `llm_degraded` 改为配置链全部 open（仅 primary open 且 backup 可用 → false）
- [x] 3.3 单测：主模型失败 → fallback 成功且仍 json_object；整链超时 → HTTP 200 `code=A2011` 且 data 含 AC+规则分；`LLMRateLimitError` → 同 A2011 不是 500；写入 `qc:llm_degraded:{model}` 且不写全局键；primary open 不阻断 backup。验证 `pytest services/qc-service/tests/test_llm_reviewer.py services/qc-service/tests/test_health.py services/qc-service/tests/test_job_store.py -q`

## 4. RAG 检索降级

- [x] 4.1 在 `rag_client.py` 外包 breaker key `qc:mcp_degraded:rag_kb`；超时/熔断/失败返回空，不抛到 API；不改 `dp_client.py`
- [x] 4.2 单测：检索失败/熔断 open → 回退关键词仍出分，非 500、非 PARTIAL。验证 `pytest services/qc-service/tests/test_rag_client.py services/qc-service/tests/test_kb_search.py services/qc-service/tests/test_offline_qc.py -q`

## 5. 离线并发闸

- [x] 5.1 基于 P0 `Semaphore` 实现 `acquire_wait`（满则等待）；`max_inflight=QC_MAX_INFLIGHT`；acquire 后 `try/finally: release()`；包住实际跑检
- [x] 5.2 单测：并发 > N 时在途不超过 N、后来者等槽后完成、不丢单；取消/异常后槽位释放。验证 `test_offline_qc.py` 或新闸门用例通过

## 6. hooks 适配器

- [x] 6.1 实现适配器记录 `qc.rate_limit.hit` / `qc.circuit_breaker.open` / `qc.degrade.triggered`，MUST NOT 调用六频道 `_publish`；lifespan `hooks.set_publisher(...)`；shutdown `hooks.set_publisher(None)`；LLM 熔断仍走 `system.alert.raised`
- [x] 6.2 单测：限流命中可观察到带 `trace_id` 的 `qc.rate_limit.hit`；熔断仍 `event=qc:llm_degraded`；六频道名单不变；shutdown 后 publisher 为 None。验证 `pytest services/qc-service/tests/test_events_publish.py -q`

## 7. 回归

- [x] 7.1 跑 qc-service 全量单测并修复本变更引入的红灯（含 `QcLlmCircuit` / 全局 `qc:llm_degraded` / Redis 挂 B0006 旧断言）。验证：`PYTHONPATH=services/qc-service python -m pytest services/qc-service/tests -q`
- [x] 7.2 规划校验通过。验证：`openspec validate qc-resilience-wiring --strict`

## 8. 验收

- [x] 8.1 raw 1s 内第 11 次 → B0006
- [x] 8.2 LLM 故意超时 → HTTP 200 `code=A2011`，data 含 AC+规则分，不是 500
- [x] 8.3 主模型不可用 → fallback 成功
- [x] 8.4 熔断键为 `qc:llm_degraded:{model}`，无全局 `qc:llm_degraded`
- [x] 8.5 离线并发 > `max_inflight` → 排队，在途 ≤ N，不丢单
- [x] 8.6 RAG 检索失败 → 回退关键词仍出分，非 500、非 PARTIAL
- [x] 8.7 qc-service 全量 pytest 不因本变更新增红灯
- [x] 8.8 `LLMRateLimitError` → A2011 仍出分，不是 500
- [x] 8.9 primary open 且 backup 可用 → `health.llm_degraded=false`；整链 open → `true`
- [x] 8.10 `openspec validate qc-resilience-wiring --strict` 通过
