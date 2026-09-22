## ADDED Requirements

### Requirement: LLM 回答走 invoke_llm 与 fallback_chain

当 `KB_LLM_ANSWER_ENABLED=true` 且请求显式 `answer=true` 且检索未兜底时，LLM 回答 MUST 经共享入口 `invoke_llm(model, messages, fallback_chain=..., timeout=...)` 调用外部模型。MUST NOT 再对单个 Provider 套私有 3 次重试 + 全局熔断器。主模型与 `fallback_chain` SHALL 由 `LLM_PROVIDER` 派生：

- `dashscope`：主模型 `qwen-plus`（或当前 `LLM_MODEL`），备用 `qwen-turbo`；
- `deepseek`：单元素链 `deepseek-chat`，无备用；
- `local`：单元素链为 `LLM_MODEL`，无备用。

超时 MUST 为 `Timeout(total=LLM_TIMEOUT)`，沿用现网默认 10s，超时尝试 MUST NOT 在同一模型上重试。业务错有界重试 MUST 仅由 `invoke_llm` 承担。主模型失败后 MUST 尝试链中下一模型；整链失败、熔断 open 或模型维限流 MUST 返回 `answer=null` + `degraded=true`，召回片段（含 BM25/关键词）仍出结果，HTTP 200，MUST NOT 500。

#### Scenario: 主模型超时走备用模型

- **WHEN** 开关开启且 `answer=true`，`LLM_PROVIDER=dashscope`，主模型超过 `LLM_TIMEOUT` 未返回，且备用 `qwen-turbo` 可用
- **THEN** 响应 `answer` 为备用模型结果，HTTP 200，不是 500；主模型熔断/降级标志独立于备用模型

#### Scenario: 整链失败仍出检索结果

- **WHEN** 主模型与 fallback_chain 全部超时或不可用
- **THEN** 响应 HTTP 200、`answer=null`、`degraded=true`、`citations=[]`，召回片段 results 仍返回（关键词/BM25 命中不丢），不是 500

### Requirement: LLM 熔断 key 按模型分片

LLM 熔断与降级标志 MUST 使用 `rag:llm_degraded:{model}`。一个模型熔断 open MUST NOT 阻断链中其他模型。系统 MUST NOT 再写入或查询全局单键 `rag:llm:degraded` 或无模型后缀的 `rag:llm_degraded`。

#### Scenario: 主模型熔断不阻断备用模型

- **WHEN** 熔断器在 `rag:llm_degraded:{primary}` 下 open，且备用模型熔断器 closed
- **THEN** 入口跳过主模型并尝试备用模型，其 key `rag:llm_degraded:{backup}` 独立不受影响

#### Scenario: 全局 rag:llm:degraded 不再使用

- **WHEN** LLM 回答失败触发熔断
- **THEN** 写入的是 `rag:llm_degraded:{model}`，全局键 `rag:llm:degraded` MUST NOT 被 SET

### Requirement: LLMRateLimitError 跳过回答仍出检索

当 `invoke_llm` 因模型维限流拒绝时，检索路径 MUST 跳过 LLM 回答，MUST 返回 HTTP 200 且 `answer=null` + `degraded=true`，召回片段仍出。MUST NOT 返回 HTTP 500。本变更 MUST NOT 修改 common 的 60/min 阈值。

#### Scenario: 模型维限流不是 500

- **WHEN** 主模型窗口内超过 invoke_llm 模型维限流
- **THEN** 响应 HTTP 200、`answer=null`、`degraded=true`，results 仍含检索命中，不是 500

## MODIFIED Requirements

### Requirement: LLM 失败降级（不阻塞、不 500）

系统 MUST 对 LLM 回答调用实施超时/熔断/不可用降级，降级 MUST NOT 抛错给终端：

- 超时：`LLM_TIMEOUT`（默认 10s）内未返回 → 中止该模型尝试，不在同一模型上重试；有备用则换 `fallback_chain` 下一模型，无备用或整链失败则返回纯检索结果；
- 熔断：按模型连续失败后 open（key `rag:llm_degraded:{model}`，TTL 60s 量级）→ 窗口内短路该模型；备用模型独立；
- 不可用：Provider 未配置/网络异常/整链失败 → 跳过回答；
- 上述任一降级：响应 `answer=null` + `degraded=true`，召回片段 results 正常返回，HTTP 200；
- 降级/熔断事件 MUST 记录结构化日志（含 trace_id），供监控告警；
- 系统 MUST NOT 使用全局键 `rag:llm:degraded`。

#### Scenario: LLM 超时降级为纯检索

- **WHEN** 开关开启且请求 answer=true，检索成功返回结果，但 LLM 调用超过 `LLM_TIMEOUT`（默认 10s）未返回且无可用备用（或备用也失败）
- **THEN** 系统中止 LLM 调用，响应 `answer=null`、`degraded=true`、`citations=[]`，召回片段正常返回，接口不报错

#### Scenario: LLM 连续失败熔断后短路

- **WHEN** 某模型连续失败使 `rag:llm_degraded:{model}` 置位（TTL 60s 量级），期间再有 answer=true 请求且链上无其他可用模型
- **THEN** 系统在熔断窗口内短路该模型调用，直接返回纯检索结果 `answer=null` + `degraded=true`，不发起对该模型的实际调用

#### Scenario: 停用 LLM 服务检索仍正常

- **WHEN** 后端 LLM 服务整体不可用，开关开启且请求 answer=true
- **THEN** 检索链路不受影响，正常返回 V4 检索结果，`answer=null` + `degraded=true`，响应标记降级
