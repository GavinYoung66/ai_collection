## MODIFIED Requirements

### Requirement: evidence_case 证据-案件节点

系统 SHALL 在 `app/graph/nodes/evidence_case.py` 提供 `async def evidence_case_node(state) -> dict`：
- 遍历 `state.case_ids`，MCP 拉取案件/历史工单/历史投诉
- 对单个 case，案件信息、历史工单、历史投诉三个相互独立的 MCP 查询 SHALL 并发发起（并发度受 MCP per-server QPS 约束），不得无依赖地串行 await
- 写入**新** `CaseEvidenceBundle` 对象，由 reducer 合并到 `case_evidence`
- 任一查询 MCP 超时/错误 → 写入 `exception_info`，不做业务判责；并发编排 SHALL NOT 放大或吞掉单个查询的异常（降级契约与串行时一致）

#### Scenario: 多案件证据写入

- **WHEN** state.case_ids = ["c1", "c2"] 且 MCP 返回两案数据
- **THEN** state 增量含 case_evidence["c1"] 与 case_evidence["c2"]

#### Scenario: 单 case 独立查询并发执行

- **WHEN** 节点采集单个 case 的案件信息、历史工单、历史投诉
- **THEN** 三个查询并发发起，采集总耗时接近单次最慢查询而非三者之和，返回的 CaseEvidenceBundle 字段与串行时一致

#### Scenario: MCP 异常写入 exception_info

- **WHEN** 任一并发 MCP 调用抛 MCPTimeoutError/MCPError
- **THEN** state 增量含 exception_info，不产出业务判责，其余查询的异常不被掩盖为成功

### Requirement: evidence_call_qc 证据-通话质检节点

系统 SHALL 在 `app/graph/nodes/evidence_call_qc.py` 提供 `async def evidence_call_qc_node(state) -> dict`：
- MCP 拉取 ASR 文本、质检违规片段、告警元数据
- 对单个 case，ASR 与质检违规两个相互独立的 MCP 查询 SHALL 并发发起（并发度受 MCP per-server QPS 约束），不得无依赖地串行 await
- 写入**新** `CaseEvidenceBundle`（call_evidence / qc_alerts），由 reducer 合并
- ASR 乱码 `is_corrupted=true` → **`confidence_level=medium`**（对齐 PRD 7.3 / data-layer）
- 录音残缺或核心内容缺失 → **`confidence_level=low`**
- 任一查询 MCP 超时/错误 → 写入 `exception_info`，降级契约与串行时一致
- **禁止**合规定性

#### Scenario: ASR 乱码降置信度

- **WHEN** MCP 返回 ASR 标记为乱码且非核心缺失
- **THEN** 对应证据 `confidence_level` == "medium"

#### Scenario: 录音残缺降置信度

- **WHEN** MCP 返回录音残缺或核心内容缺失
- **THEN** 对应证据 `confidence_level` == "low"

#### Scenario: ASR 与质检查询并发执行

- **WHEN** 节点采集单个 case 的通话 ASR 与质检违规
- **THEN** 两个查询并发发起，总耗时接近单次较慢查询，装配出的 call_evidence / qc_alerts 与串行时一致
