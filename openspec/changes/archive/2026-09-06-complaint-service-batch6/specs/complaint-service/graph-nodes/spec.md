## ADDED Requirements

### Requirement: verdict 综合判责节点

系统 SHALL 在 `app/graph/nodes/verdict.py` 提供 `async def verdict_node(state) -> dict`：
- **MODERATE-6**：开头检查 `fact_timeline` / `compliance_opinion` / `user_demand`；任一为 None → `exception_info(error_type=llm_degraded)` + `task_status=pending_review`，直接返回（不调 LLM）
- `load_prompt("complaint_verdict")` + `await llm_provider.invoke` → VerdictDraft（含 `per_case_verdicts`）
- `confidence < 0.7` → 自动 `conflict_highlight=True`
- verdict 枚举四值无 normal；**仅草稿、无最终定责权限**

#### Scenario: None 输入防御

- **WHEN** fact_timeline / compliance_opinion / user_demand 任一为 None
- **THEN** 增量含 exception_info(error_type=llm_degraded) 且 task_status=pending_review，不调用 LLM

#### Scenario: 低置信度标记冲突

- **WHEN** LLM 返回 VerdictDraft 且 confidence < 0.7
- **THEN** conflict_highlight == True

#### Scenario: 产出判责草稿

- **WHEN** 三方输入齐全且 Mock LLM 返回有效 JSON
- **THEN** 增量含 verdict_draft，verdict ∈ 四值且不含 normal，含 per_case_verdicts

### Requirement: generate_package 产出物生成节点

系统 SHALL 在 `app/graph/nodes/generate_package.py` 提供 `async def generate_package_node(state) -> dict`：
- **防御性短路**：若 `task_status=pending_review` 或无 `verdict_draft` → **不调** MCP/LLM，返回空增量或透传（与 `should_proceed_after_verdict` 双保险）
- **MODERATE-5**：先 MCP `search_response_templates` try/except（`MCPTimeoutError` / `MCPError` → rag_result=[] + exception_info，降级模板填充）
- `load_prompt("complaint_generate_package")` + `await llm_provider.invoke` → JudgmentPackage
- 输出含 evidence_chain / judgment_report / response_draft / internal_improvement / rag_source_ids
- **禁止**编造事实、编造法规条款

#### Scenario: MCP 模板检索降级

- **WHEN** MCP search_response_templates 抛异常
- **THEN** rag_result=[]，exception_info 记录，仍产出 JudgmentPackage（降级模板填充）

#### Scenario: 产出研判数据包

- **WHEN** MCP 返回模板且 Mock LLM 返回有效 JSON
- **THEN** 增量含 judgment_package，含 rag_source_ids

#### Scenario: 无草稿时短路

- **WHEN** verdict_draft 为 None 或 task_status=pending_review
- **THEN** 不调用 MCP/LLM

### Requirement: human_review 人工断点节点

系统 SHALL 在 `app/graph/nodes/human_review.py` 提供 `async def human_review_node(state) -> dict`：
- 使用 `langgraph.types.interrupt` 等待人工提交 review_action
- 根据 review_action 更新 task_status：approve→resolved；reject_rejudge→evidence_gathering；escalate→pending_senior_review
- **reject_rejudge 时 `state.reject_count += 1`**（写回 State 根字段）
- **FATAL-1**：human_review **无硬边** 指向 sample_backflow；由 `should_proceed_after_review` 条件边路由

#### Scenario: interrupt 等待人工

- **WHEN** 图执行到 human_review
- **THEN** 触发 interrupt 暂停，等待人工输入 review_action

#### Scenario: approve 转 resolved

- **WHEN** 人工提交 review_action=approve
- **THEN** task_status == "resolved"

#### Scenario: reject_rejudge 转 evidence_gathering 并计数

- **WHEN** 人工提交 review_action=reject_rejudge
- **THEN** task_status == "evidence_gathering" 且 state.reject_count 比进入前 +1

### Requirement: sample_backflow 样本回流节点

系统 SHALL 在 `app/graph/nodes/sample_backflow.py` 提供 `async def sample_backflow_node(state) -> dict`：
- 经 deps 调用 MCP `write_session` + `write_human_review_truth`
- 失败自动重试 3 次；仍失败告警（日志）但不阻塞
- 入边仅来自 `should_proceed_after_review` 的 to_backflow（非 human_review 硬边）

#### Scenario: 回写成功

- **WHEN** MCP 成功
- **THEN** 流程继续，不产生阻塞异常

#### Scenario: 回写失败重试后告警不阻塞

- **WHEN** MCP 连续失败 3 次
- **THEN** 记录告警日志，流程不中断

### Requirement: judgment_fanout 研判扇出节点

系统 SHALL 在 `app/graph/nodes/judgment_fanout.py` 提供 `async def judgment_fanout_node(state) -> dict`：
- **FATAL-2**：仅设置 `task_status=judging`，**不做业务推理**
- 出边由 4.12 builder 配置三条并行边：→fact / →compliance / →user_demand

#### Scenario: 仅设置 judging 状态

- **WHEN** judgment_fanout 执行
- **THEN** 增量仅含 task_status="judging"，无其他业务输出
