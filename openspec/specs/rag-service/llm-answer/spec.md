# RAG LLM Answer Specification

## Purpose

在保持 V1~V4 检索主链路不变的前提下，以开关门控方式为 RAG 检索接入在线大模型，检索后生成**带溯源引用的回答**供前端展示；开关默认关闭，关闭时零 LLM 调用、行为与 V4 完全一致，LLM 失败一律降级为纯检索结果。

## Requirements

### Requirement: 开关门控（KB_LLM_ANSWER_ENABLED）

系统 SHALL 提供开关 `KB_LLM_ANSWER_ENABLED`（默认 false）控制检索接入在线大模型回答：

- 开关关闭（默认）：检索响应与 V4 完全一致，**不产生任何 LLM 调用**（零成本回退）；
- 开关开启且请求显式 `answer=true` 且检索未触发双闸门兜底（返回有效 results）：检索后组装上下文（top_k chunk 文本 + 溯源），调用 LLM Provider 生成回答；
- 开关开启但请求未传 `answer`（缺省 false）：不调用 LLM，行为与 V4 一致；
- 触发双闸门兜底（results=[]）时 MUST NOT 调用 LLM（无可溯源内容）。

#### Scenario: 开关关闭零 LLM 调用

- **WHEN** `KB_LLM_ANSWER_ENABLED=false`（默认），用户检索（无论请求是否传 answer=true）
- **THEN** 检索链路不产生任何 LLM 调用，响应与 V4 完全一致，`answer=null`

#### Scenario: 开关开启且请求开启生成回答

- **WHEN** `KB_LLM_ANSWER_ENABLED=true`，用户检索 query="逾期协商需要哪些材料" 并传 answer=true，检索返回有效结果
- **THEN** 系统组装召回上下文调用 LLM Provider，响应 `answer` 字段包含带溯源引用的回答（引用命中文档 doc_id）

#### Scenario: 双闸门兜底时不调用 LLM

- **WHEN** `KB_LLM_ANSWER_ENABLED=true` 且请求 answer=true，但检索触发双闸门兜底（results=[]）
- **THEN** 系统不调用 LLM，响应按既有兜底契约返回，`answer=null`

### Requirement: LLM Provider 抽象

系统 SHALL 提供 LLM Provider 抽象，支持在线/本地可切换（为后续本地部署预留），切换 MUST NOT 影响对外请求/响应契约：

- 默认 Provider：在线 DashScope（`LLM_PROVIDER=dashscope`）；
- 预留 Provider：本地 OpenAI 兼容接口（`LLM_PROVIDER=local`，供本地部署切换），配套配置 `LLM_LOCAL_BASE_URL` / `LLM_LOCAL_API_KEY` / `LLM_MODEL`，未配置基址时按"Provider 不可用"降级路径处理；
- Provider 统一输出契约：回答文本 + 溯源引用结构（引用的 doc_id 列表）；调用参数（上下文、query、超时 `LLM_TIMEOUT` 默认 10s）由统一封装管理；
- 并发上限：LLM 回答调用受 `LLM_MAX_CONCURRENCY`（默认 4）信号量约束，**超限请求排队等待，不降级**（批 11 用户裁决，与实现 D8 一致），不得无界占用 worker。

#### Scenario: 切换 Provider 对外契约不变

- **WHEN** 运维将 `LLM_PROVIDER` 从 dashscope 切换为 local（并配置 `LLM_LOCAL_BASE_URL`）
- **THEN** `POST /kb/search` 请求/响应契约无任何变化，回答生成链路内部切换到对应 Provider，回答与溯源引用格式一致

#### Scenario: 本地 Provider 未配置基址时按不可用降级

- **WHEN** `LLM_PROVIDER=local` 且未配置 `LLM_LOCAL_BASE_URL`，开关开启且请求 answer=true
- **THEN** 系统按 Provider 不可用降级，返回纯检索结果 `answer=null` + `degraded=true`，不抛错、不 500

#### Scenario: Provider 未配置密钥时按不可用降级

- **WHEN** `KB_LLM_ANSWER_ENABLED=true` 且请求 answer=true，但当前 Provider（如 dashscope）未配置密钥
- **THEN** 系统不调用 LLM，返回纯检索结果 `answer=null` + `degraded=true`，不抛错、不 500

### Requirement: LLM 回答生成与溯源引用

系统 SHALL 在生成回答时携带溯源引用：回答文本 MUST 基于召回片段组装（top_k chunk 文本 + 对应 doc_id），回答中的关键结论引用 MUST 可追溯到命中文档；回答不得脱离召回片段编造内容。溯源引用以响应字段 `citations`（命中文档 doc_id 列表，可空数组）随检索响应返回，供回答可溯源审计（关键结论回查命中文档）。页码标注为可选增强：chunk 级无页码数据源（页码仅 kb_doc_assets 资产级），Prompt 页码标注仅在片段携带 page 时生成，不得虚构页码。回答生成 MUST 遵守全局大模型护栏（超时、熔断、异常降级、空结果兜底，禁止裸跑）。

#### Scenario: 回答含可溯源引用

- **WHEN** 开关开启且请求 answer=true，检索命中 regulation 文档 doc_id=X 的"申请材料"片段
- **THEN** 回答文本基于该片段生成（内容与召回片段语义一致，不凭空编造金额/时间/政策），响应 `citations` 字段包含命中文档 doc_id=X，关键结论可回查命中文档

### Requirement: LLM 回答合规红线（脱敏 + 双校验 + Prompt 配置化）

系统 MUST 在调用 LLM 生成回答时遵守全局护栏硬约束（03_db_vector_spec【强制8】脱敏、04_llm_guardrail【强制1】双校验、05_prompt_spec【强制2/6】配置化），任一违反即视为不合规：

- **脱敏**：检索片段中的客户手机号/身份证号/金额明细等敏感信息 MUST 在注入 LLM 前脱敏为占位符（如 `[手机号]`、`[金额]`，规则对齐既有脱敏工具），回答、引用与片段展示均使用脱敏后文本；
- **双校验**：LLM 调用 MUST 执行前置合规拦截（敏感词/红线关键词过滤通过后才可发起调用）+ 后置内容校验（回答输出复核，命中红线关键词则丢弃回答、按 LLM 失败降级处理），对齐 04_llm_guardrail【强制1】；
- **Prompt 配置化**：回答生成 Prompt MUST 由 `conf/llm_answer_prompt.yaml` 统一配置管理，业务变量（召回 chunk 文本、页码、query）MUST 以参数注入方式替换模板占位符，**禁止业务代码硬编码/拼接 Prompt 明文**，对齐 05_prompt_spec【强制2/6】；
- 脱敏/校验失败的处理路径 MUST 与 LLM 失败降级一致（`answer=null` + `degraded=true`，不抛错、不 500）。

#### Scenario: 敏感信息脱敏后注入 LLM

- **WHEN** 检索片段含客户手机号"138xxxx1234"与金额"¥5,000"，开关开启且请求 answer=true
- **THEN** 注入 LLM 的上下文为脱敏后文本（如"[手机号]、[金额]"），回答与引用不出现明文敏感信息，kb_search_logs 标记脱敏事件

#### Scenario: 后置内容校验拦截违规回答

- **WHEN** LLM 返回的回答命中红线关键词，开关开启且请求 answer=true
- **THEN** 系统丢弃该回答，返回 `answer=null` + `degraded=true` 纯检索结果，不抛错、不 500，并落结构化日志（含 trace_id）

#### Scenario: 前置合规拦截拦截违规上下文

- **WHEN** 检索片段命中敏感词/红线关键词（如威胁/诱导话术），开关开启且请求 answer=true
- **THEN** 系统在发起 LLM 调用前拦截，**不发起任何 LLM 调用**（`kb_search_logs.llm_called` 记 '0'，与实际发起调用的超时/失败区分），返回 `answer=null` + `degraded=true` 纯检索结果，召回片段正常返回，并落结构化日志（含 trace_id）——与"后置内容校验"构成前置拦截 + 后置校验双机制，任一命中均按 LLM 失败降级处理

#### Scenario: Prompt 由配置文件加载且业务变量参数注入

- **WHEN** 审计回答生成链路
- **THEN** Prompt 模板来源为 `conf/llm_answer_prompt.yaml`，chunk 文本/页码/query 通过参数注入占位符，业务代码中无硬编码 Prompt 明文

### Requirement: LLM 失败降级（不阻塞、不 500）

系统 MUST 对 LLM 回答调用实施超时/熔断/不可用降级，降级 MUST NOT 抛错给终端：

- 超时：`LLM_TIMEOUT`（默认 10s）内未返回 → 中止调用，返回纯检索结果；
- 熔断：连续失败按既有 3 连失败熔断规则（复用 `rag:llm:degraded` 熔断标识，TTL 60s）→ 窗口内短路 LLM 调用；
- 不可用：Provider 未配置/网络异常 → 跳过回答；
- 上述任一降级：响应 `answer=null` + `degraded=true`，召回片段 results 正常返回，HTTP 200；
- 降级/熔断事件 MUST 记录结构化日志（含 trace_id），供监控告警。

#### Scenario: LLM 超时降级为纯检索

- **WHEN** 开关开启且请求 answer=true，检索成功返回结果，但 LLM 调用超过 `LLM_TIMEOUT`（默认 10s）未返回
- **THEN** 系统中止 LLM 调用，响应 `answer=null`、`degraded=true`、`citations=[]`，召回片段正常返回，接口不报错

#### Scenario: LLM 连续失败熔断后短路

- **WHEN** LLM 连续 3 次调用失败（熔断标识 `rag:llm:degraded` 置位，TTL 60s），期间再有 answer=true 请求
- **THEN** 系统在熔断窗口内短路 LLM 调用，直接返回纯检索结果 `answer=null` + `degraded=true`，不发起实际调用

#### Scenario: 停用 LLM 服务检索仍正常

- **WHEN** 后端 LLM 服务整体不可用，开关开启且请求 answer=true
- **THEN** 检索链路不受影响，正常返回 V4 检索结果，`answer=null` + `degraded=true`，响应标记降级

### Requirement: §1.4 边界显式放开（受控例外）

系统 MUST 遵守 PRD §1.4 架构边界，并以本增量规范作为显式受控例外：RAG-Service 仅当 `KB_LLM_ANSWER_ENABLED=true` 且请求显式开启时才向外调用 LLM 生成回答；其余任何场景 MUST NOT 发起 LLM 调用（含 Query 改写等多轮链路既有开关继续独立控制）；`include_answer` 类历史生成式应答字段不回归；回答仅作为检索结果的附加展示字段，不替代检索片段，不生成面向坐席/终端用户的业务话术（话术生成仍归 call-service）。

#### Scenario: 边界审计通过

- **WHEN** 审计 V5 的 LLM 调用点
- **THEN** LLM 调用仅存在于 `KB_LLM_ANSWER_ENABLED=true` 且请求 answer=true 且未触发兜底的路径，其余路径零 LLM 调用；对外新增字段仅 `answer`（可空）、`citations`（可空数组）与 `results[].assets`（可空数组），不恢复 `include_answer` 字段

### Requirement: 回答日志与可观测

系统 SHALL 在生成/降级 LLM 回答时落检索日志标记：`kb_search_logs` 记录本次请求是否发起 LLM 调用、是否降级（降级/兜底响应仍遵守既有"不写查询缓存"契约）；回答生成不写入查询缓存（LLM 回答为动态内容，缓存仅覆盖检索片段）。请求显式 `answer=true` 时 MUST **跳过查询缓存读取**（避免缓存响应缺 answer 造成语义悬空），直接走真实检索；缓存写入行为与 V4 一致（`answer=true` 请求不写缓存，降级/兜底响应不写缓存）。

#### Scenario: 回答请求落检索日志

- **WHEN** 开关开启且请求 answer=true 完成一次检索（含 LLM 回答成功或降级）
- **THEN** kb_search_logs 对应记录标记 LLM 调用与降级状态，检索日志 15 字段契约不破坏

#### Scenario: answer=true 跳过缓存读取走真实检索

- **WHEN** 同一 query 的 V4 检索结果已在查询缓存中，用户以 answer=true 再次发起检索（开关开启）
- **THEN** 系统不返回缓存响应，跳过缓存读取走真实检索并生成回答，响应 `answer` 有值 + 召回片段完整；缓存语义与 V4 一致（不写入 answer 相关差异）

#### Scenario: 回答生成不写查询缓存

- **WHEN** 开关开启且请求 answer=true 的检索请求返回（含 LLM 回答）
- **THEN** 查询缓存仅缓存检索片段相关响应（与 V4 一致），LLM 回答相关差异不进入缓存，缓存命中行为不因 answer 参数改变既有契约
