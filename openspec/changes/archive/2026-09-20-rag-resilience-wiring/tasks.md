## 1. 启动与 hooks

- [x] 1.1 在 `app/main.py` `startup()` 调用 `set_default_service("rag")`，并把 rag Redis `configure_redis` 注入 common；单测或启动探测断言熔断/限流 key 使用 `rag:` 前缀。验证：`PYTHONPATH=services/rag-service python -m pytest services/rag-service/tests/test_main.py -q`
- [x] 1.2 实现 hooks 适配器（structlog / 可测 emitted），事件 `rag.rate_limit.hit` / `rag.circuit_breaker.open` / `rag.degrade.triggered`；startup `hooks.set_publisher(...)`；shutdown `hooks.set_publisher(None)`；MUST NOT 调投诉六频道。验证：新增或扩展单测断言 shutdown 后 publisher 为 None，限流命中 payload 含 `trace_id`

## 2. LLM 入口与 fallback

- [x] 2.1 将 `llm_providers/factory.py` 改为按 `LLM_PROVIDER` 返回 `(model, fallback_chain, transport)`：dashscope → 主 `LLM_MODEL`/`qwen-plus` + `["qwen-turbo"]`，deepseek → `["deepseek-chat"]` 无备用，local → 单元素 `LLM_MODEL`；3 个 provider 只保留 HTTP + JSON 解析。验证：factory 单测覆盖三条链
- [x] 2.2 改 `llm_service.py` 走 `invoke_llm(..., timeout=Timeout(total=LLM_TIMEOUT), transport=...)`；删除 `RedisDegradeTracker` 与外层 `wait_for` 双超时；捕获整链失败 / `LLMCircuitOpenError` / `LLMRateLimitError` → `answer=null` + `degraded=true`，检索 results 仍出。验证：`pytest services/rag-service/tests/test_llm_answer.py -q`
- [x] 2.3 单测：主模型超时 → fallback 成功；整链失败 → HTTP 200 空 answer + 关键词/BM25 仍出结果非 500；写入 `rag:llm_degraded:{model}` 且不写 `rag:llm:degraded`；primary open 不阻断 backup；`LLMRateLimitError` 同降级。验证：上述用例通过且 `LLM_TIMEOUT` 默认仍为 10

## 3. embed / rerank 熔断

- [x] 3.1 `embed_client.py` 外包 `CircuitBreaker` key `rag:mcp_degraded:embed`（fail_max=3，reset_timeout=60s）+ `Timeout`；open 返回空向量；删除 `rag:embed:degraded` 与自写 3 次退避；search 走 BM25；`batch_embed` 空结果不写 Milvus。验证：`pytest services/rag-service/tests/test_embed_es_only_degradation.py -q` 且新增「breaker open → BM25、非 500」用例
- [x] 3.2 `rerank_client.py` 外包 key `rag:mcp_degraded:rerank`；open 跳过精排用 RRF/原始分；删除 `rag:rerank:degraded`。验证：`pytest services/rag-service/tests/test_search_degradation.py services/rag-service/tests/test_redis_fault_tolerance.py -q` 且新增「breaker open → 原始分排序、非 500」用例

## 4. ocr / asr 熔断

- [x] 4.1 `ocr_client.py` 外包 key `rag:mcp_degraded:ocr`；open 短路 HTTP 并按解析失败（A3001/占位），不得当 A3002；删除 `rag:ocr:degraded` 与自写退避。验证：新增「OCR breaker open → 标记失败不 500、不卡入库」单测；空白图成功识别仍 A3002
- [x] 4.2 `asr_providers/dashscope_provider.py` 外包 key `rag:mcp_degraded:asr`；open 返回转写失败标记不 500；删除自写 3 次指数退避与 Redis key。验证：新增「ASR breaker open → 标记失败不阻塞入库」单测
- [x] 4.3 删除 `asr_client.py` 中重复的 `rag:asr:degraded` 探测/写入；更新 `test_asr_client.py` 不再断言客户端私有 SETEX。验证：`pytest services/rag-service/tests/test_asr_client.py services/rag-service/tests/test_ingest_degradation.py services/rag-service/tests/test_multimodal_parsers.py -q`

## 5. 限流核对与死代码

- [x] 5.1 `rate_limit.py` 确认 key 仍为 `rag:ratelimit:{bucket}:{user}`；`allow` 传 `context={"trace_id": ...}`。验证：`pytest services/rag-service/tests/test_rate_limit.py -q`
- [x] 5.2 grep rag-service 无 `RedisDegradeTracker`、无 `rag:llm:degraded` / `rag:embed:degraded` / `rag:rerank:degraded` / `rag:ocr:degraded` / `rag:asr:degraded`（允许 `rag:rewrite:degraded`）；无 embed/rerank/ocr/asr 对 `call_tool` 的调用。验证：`rg 'RedisDegradeTracker|rag:llm:degraded|rag:embed:degraded|rag:rerank:degraded|rag:ocr:degraded|rag:asr:degraded' services/rag-service` 仅命中改写或测试负向断言

## 6. 回归与规划校验

- [x] 6.1 跑 rag-service 全量单测并修复本变更引入的红灯（含旧全局键断言）。验证：`PYTHONPATH=services/rag-service python -m pytest services/rag-service/tests -q`
- [x] 6.2 规划校验通过。验证：`openspec validate rag-resilience-wiring --strict`

## 7. 验收

- [x] 7.1 LLM 主模型超时 → fallback 成功（dashscope）；整链失败 → 空 answer + 关键词/BM25 仍出结果，HTTP 200 非 500
- [x] 7.2 embed 熔断 open → 回退 BM25，不抛 500
- [x] 7.3 rerank 熔断 open → 跳过重排用原始分数，不抛 500
- [x] 7.4 OCR 熔断 open → 文档标记 OCR 失败但不阻塞/不 500
- [x] 7.5 ASR 熔断 open → 音频标记转写失败但不阻塞/不 500
- [x] 7.6 熔断 key 为 `rag:llm_degraded:{model}` 与 `rag:mcp_degraded:{embed|rerank|ocr|asr}`，无全局 `rag:llm:degraded` 等旧 key
- [x] 7.7 rag-service 全量 pytest 不因本变更新增红灯
- [x] 7.8 `openspec validate rag-resilience-wiring --strict` 通过
