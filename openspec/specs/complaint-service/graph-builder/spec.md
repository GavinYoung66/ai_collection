# complaint-service/graph-builder

## Purpose

为 complaint-service 提供 LangGraph 整图编译与连边能力：条件边（反思重试 / 判责后路由 / 人工复核路由）、扇出并行边、interrupt 人工断点、池化 AsyncPostgresSaver。落实 FATAL-1/2、MODERATE-4，以及 verdict 后不得盲进 generate_package。

## Requirements

### Requirement: should_retry_or_proceed 条件边

系统 SHALL 在 `app/graph/edges.py` 提供 `should_retry_or_proceed(state) -> str`：
- 异常（exception_info 非空）→ `"to_manual"`
- 反思不足（reflection_report.evidence_sufficient == False）且 loop_count < max_loops → `"to_re_gather"`（→evidence_re_gather，MODERATE-4）
- 反思充分 → `"to_judgment"`（→judgment_fanout，FATAL-2）

#### Scenario: 异常转人工

- **WHEN** state.exception_info 非空
- **THEN** 返回 "to_manual"

#### Scenario: 反思不足且未超限转重取证

- **WHEN** evidence_sufficient=False 且 loop_count < max_loops
- **THEN** 返回 "to_re_gather"

#### Scenario: 反思充分转判责扇出

- **WHEN** evidence_sufficient=True
- **THEN** 返回 "to_judgment"

### Requirement: should_proceed_after_verdict 条件边

系统 SHALL 在 `app/graph/edges.py` 提供 `should_proceed_after_verdict(state) -> str`：
- `task_status == pending_review` **或** `exception_info.error_type == llm_degraded` **或** `verdict_draft is None` → `"to_review"`（→human_review）
- 否则 → `"to_package"`（→generate_package）

#### Scenario: None 防御后跳过产出物

- **WHEN** verdict 因 MODERATE-6 写入 pending_review / llm_degraded
- **THEN** 返回 "to_review"，不进入 generate_package

#### Scenario: 正常判责进产出物

- **WHEN** verdict_draft 存在且非 pending_review（因 None 防御）
- **THEN** 返回 "to_package"

### Requirement: should_proceed_after_review 条件边

系统 SHALL 在 `app/graph/edges.py` 提供 `should_proceed_after_review(state) -> str`：
- 读取 **`state.reject_count`（State 根字段）** 与 `state.human_review.review_action`
- reject_rejudge 且 reject_count ≤ 2 → `"to_re_gather"`（→evidence_re_gather）
- reject_rejudge 且 reject_count > 2 → `"to_manual"`（→human_review 再 interrupt）
- escalate → `"to_senior_review"`（→human_review 再 interrupt，pending_senior_review）
- 其余（approve / modify_* / add_evidence）→ `"to_backflow"`（→sample_backflow）

#### Scenario: 驳回重判未超限转重取证

- **WHEN** review_action=reject_rejudge 且 state.reject_count ≤ 2
- **THEN** 返回 "to_re_gather"

#### Scenario: 驳回超限转人工

- **WHEN** review_action=reject_rejudge 且 state.reject_count > 2
- **THEN** 返回 "to_manual"

#### Scenario: 升级转高级复核

- **WHEN** review_action=escalate
- **THEN** 返回 "to_senior_review"

#### Scenario: 其余转样本回流

- **WHEN** review_action ∈ {approve, modify_verdict, modify_response, add_evidence}
- **THEN** 返回 "to_backflow"

### Requirement: build_complaint_graph 整图编译

系统 SHALL 在 `app/graph/builder.py` 提供 `build_complaint_graph(checkpointer=None) -> CompiledStateGraph`：
- 注册全部节点：批次5 九节点 + 本批五节点（verdict / generate_package / human_review / sample_backflow / judgment_fanout）
- 连边：
  - START → orchestrator → [evidence_case ‖ evidence_call_qc] → reflection
  - reflection → should_retry_or_proceed
    - to_re_gather → evidence_re_gather（扇出 →evidence_case / →evidence_call_qc）
    - to_judgment → judgment_fanout（扇出 →fact / →compliance / →user_demand）
    - to_manual → human_review
  - fact & compliance & user_demand → verdict
  - verdict → should_proceed_after_verdict
    - to_review → human_review
    - to_package → generate_package → human_review
  - human_review → should_proceed_after_review
    - to_re_gather → evidence_re_gather
    - to_manual / to_senior_review → human_review（再次 interrupt）
    - to_backflow → sample_backflow → END
- human_review 使用 interrupt；**无硬边**到 sample_backflow（FATAL-1）
- 可选 checkpointer 参数

#### Scenario: 图编译通过

- **WHEN** 调用 build_complaint_graph()
- **THEN** 返回已编译的 StateGraph，无编译错误

#### Scenario: 全链路 Mock 可跑通

- **WHEN** 用 Mock MCP/LLM 从 evidence_gathering 驱动图（见 `tests/unit/test_graph_compile.py`）
- **THEN** 经 human_review interrupt 注入 approve 可达 resolved

### Requirement: 池化 AsyncPostgresSaver checkpointer

系统 SHALL 提供 checkpointer 工厂 `get_checkpointer()`：
- **生产**：先 `asyncpg.create_pool(dsn=..., min_size=5, max_size=20)`，再将 pool 交给 `AsyncPostgresSaver`（或官方等价「传入已建池」构造）
- **禁止**裸连接；**禁止**把 `min_size`/`max_size` 伪造成 `from_conn_string` 的 kwargs
- PG 连接从配置读取，不硬编码
- PG 故障 → Redis fallback **接口**（本批 stub 可回 MemorySaver，不假装接真 Redis）
- **本地验证**：MemorySaver（或 AsyncSqliteSaver）stub，不要求连真 PG

#### Scenario: 连接池配置

- **WHEN** 初始化生产 checkpointer
- **THEN** 使用 asyncpg 池（min_size=5, max_size=20），非单次裸连接

#### Scenario: PG 不可用时降级

- **WHEN** PG 连接失败
- **THEN** 走 fallback 接口（本批 stub），不崩溃

#### Scenario: stub 可持久化/恢复

- **WHEN** 本地使用 MemorySaver 跑图
- **THEN** State 可在同一进程内保存与恢复（或序列化冒烟）
