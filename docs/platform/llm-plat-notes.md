# Platform LLM notes（PLAT-01 / PLAT-02）

- **`common.llm.provider.chat` / `chat_json`**：遗留薄封装；新代码优先 `common.llm.transport` + `common.llm.reliability`（或服务内 RealLLMTransport）。
- **多实例降级**：以 Redis degrade 标志（服务前缀 key，如 `rag:llm:degraded`）为权威；进程内 pybreaker 仅为辅助。
