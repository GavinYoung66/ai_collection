## Purpose

为 complaint-service 提供 LangGraph 编排上半节点：调度 orchestrator、证据采集（案件/通话质检）、重新取证扇出、反思、事实还原、合规判定、用户诉求。每个节点为 **async**，输入 State、输出 State 增量，经 deps 注入 Mock MCP/LLM，可独立调用与测试；为批次6 整图编译奠定基础。

## ADDED Requirements

### Requirement: 节点统一 async 与依赖注入

系统 SHALL 为每个本批节点提供 `async def xxx_node(state: ComplaintSessionState) -> dict`。
- MCP / LLM 经 `app/api/deps.py` 的 `get_mcp_client()` / `get_llm_provider()` 获取
- MCP 异常类型从 `app.mcp.errors` 导入（`MCPTimeoutError` / `MCPError`）
- 证据展平复用 `app.utils.evidence_helper._collect_all_evidence`（对 State 使用 `model_dump()`），**禁止**另造第二套展平函数
- 证据置信度字段名为 **`confidence_level`**（对齐批次2 / data-layer）；ASR 乱码与录音残缺降级优先复用 `apply_corruption_degradation`

#### Scenario: 节点可被 asyncio.run 调用

- **WHEN** 调用 `asyncio.run(xxx_node(minimal_state))`
- **THEN** 返回 dict 增量且不抛未处理异常（Mock 路径）

### Requirement: orchestrator 调度节点

系统 SHALL 在 `app/graph/nodes/orchestrator.py` 提供 `async def orchestrator_node(state) -> dict`：
- 弱中心化、纯代码条件分支，**不调用 LLM 做业务推理**
- 初始化 `task_status=evidence_gathering`
- 异常检查或 Token 超限 → `task_status=pending_review`
- 可 MCP 读取工单基础信息
- **禁止**事实还原、合规判责、修改子 Agent 输出

#### Scenario: 初始化任务状态

- **WHEN** orchestrator 处理新会话
- **THEN** 增量中 task_status == "evidence_gathering"

#### Scenario: 异常转人工

- **WHEN** state 含 exception_info 或 token 超阈值
- **THEN** task_status == "pending_review"

### Requirement: evidence_case 证据-案件节点

系统 SHALL 在 `app/graph/nodes/evidence_case.py` 提供 `async def evidence_case_node(state) -> dict`：
- 遍历 `state.case_ids`，MCP 拉取案件/历史工单/历史投诉
- 写入**新** `CaseEvidenceBundle` 对象，由 reducer 合并到 `case_evidence`
- MCP 超时/错误 → 写入 `exception_info`，不做业务判责

#### Scenario: 多案件证据写入

- **WHEN** state.case_ids = ["c1", "c2"] 且 MCP 返回两案数据
- **THEN** state 增量含 case_evidence["c1"] 与 case_evidence["c2"]

#### Scenario: MCP 异常写入 exception_info

- **WHEN** MCP 调用抛 MCPTimeoutError/MCPError
- **THEN** state 增量含 exception_info，不产出业务判责

### Requirement: evidence_call_qc 证据-通话质检节点

系统 SHALL 在 `app/graph/nodes/evidence_call_qc.py` 提供 `async def evidence_call_qc_node(state) -> dict`：
- MCP 拉取 ASR 文本、质检违规片段、告警元数据
- 写入**新** `CaseEvidenceBundle`（call_evidence / qc_alerts），由 reducer 合并
- ASR 乱码 `is_corrupted=true` → **`confidence_level=medium`**（对齐 PRD 7.3 / data-layer）
- 录音残缺或核心内容缺失 → **`confidence_level=low`**
- **禁止**合规定性

#### Scenario: ASR 乱码降置信度

- **WHEN** MCP 返回 ASR 标记为乱码且非核心缺失
- **THEN** 对应证据 `confidence_level` == "medium"

#### Scenario: 录音残缺降置信度

- **WHEN** MCP 返回录音残缺或核心内容缺失
- **THEN** 对应证据 `confidence_level` == "low"

### Requirement: evidence_re_gather 重新取证扇出节点

系统 SHALL 在 `app/graph/nodes/evidence_re_gather.py` 提供 `async def evidence_re_gather_node(state) -> dict`：
- 设置 `task_status=evidence_gathering`
- 清除上一轮 `exception_info` 标记
- 本批仅实现节点函数；两条出边（→evidence_case、→evidence_call_qc）由批次6 builder 配置

#### Scenario: 清除异常并重置状态

- **WHEN** 节点执行
- **THEN** task_status == "evidence_gathering" 且 exception_info 被清空

### Requirement: reflection 反思节点

系统 SHALL 在 `app/graph/nodes/reflection.py` 提供 `async def reflection_node(state) -> dict`：
- `load_prompt("complaint_reflection")` + `await llm_provider.invoke` → ReflectionReport
- 输入复用 `_collect_all_evidence(state.model_dump())` 展平证据
- `loop_count += 1`
- **不自行发起取证循环**，流转权交还调度

#### Scenario: 产出反思报告并递增循环计数

- **WHEN** reflection 节点执行（Mock LLM 返回有效 JSON）
- **THEN** state 增量含 reflection_report 且 loop_count 比输入 +1

### Requirement: fact 事实还原节点

系统 SHALL 在 `app/graph/nodes/fact.py` 提供 `async def fact_node(state) -> dict`：
- `load_prompt("complaint_fact_timeline")` + `await llm_provider.invoke` → FactTimeline
- 输入展平证据 + case_evidence
- **禁止**违规判定、不输出责任结论

#### Scenario: 产出事实时序

- **WHEN** fact 节点执行（Mock LLM 返回有效 JSON）
- **THEN** state 增量含 fact_timeline，且不含 verdict/责任结论字段

### Requirement: compliance 合规判定节点

系统 SHALL 在 `app/graph/nodes/compliance.py` 提供 `async def compliance_node(state) -> dict`：
- 先 MCP `search_regulations` 检索法规（try/except 捕获 `app.mcp.errors.MCPTimeoutError` / `MCPError` → rag_result=[] + exception_info）
- 再 `load_prompt("complaint_compliance")` + `await llm_provider.invoke` → ComplianceOpinion（含 regulation_refs）
- **禁止**输出最终定责

#### Scenario: MCP 法规检索降级

- **WHEN** MCP search_regulations 抛异常
- **THEN** rag_result=[]，exception_info 记录，仍产出 ComplianceOpinion（不引用法规）

#### Scenario: 产出合规意见

- **WHEN** MCP 返回法规且 Mock LLM 返回有效 JSON
- **THEN** state 增量含 compliance_opinion，含 regulation_refs

### Requirement: user_demand 用户诉求节点

系统 SHALL 在 `app/graph/nodes/user_demand.py` 提供 `async def user_demand_node(state) -> dict`：
- `load_prompt("complaint_user_demand")` + `await llm_provider.invoke` → UserDemandAnalysis
- 输入投诉文本 + 展平证据
- 输出 demand_type / emotion_level / secondary_complaint_risk
- **禁止**责任判定

#### Scenario: 产出用户诉求分析

- **WHEN** user_demand 节点执行（Mock LLM 返回有效 JSON）
- **THEN** state 增量含 user_demand，含 demand_type/emotion_level/secondary_complaint_risk
