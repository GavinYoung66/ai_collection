# Tasks: complaint-service-batch5

## 执行顺序

**串行 + 分组**：4.1（State+reducer）先行 → 4.2/4.3/4.4/4.14（可并行） → 4.5 → 4.6/4.7/4.8（可并行）

验证命令统一在 `services/complaint-service/` 下执行：`PYTHONPATH=../..:$PYTHONPATH`，conda env `ai_collection`（Python≥3.11）。

节点均为 **`async def`**；冒烟验证一律 `asyncio.run(...)`。

两轮验收：
- **轮1**：4.1 + 4.2 + 4.3 + 4.14
- **轮2**：4.4 + 4.5 + 4.6 + 4.7 + 4.8

约定：
- DI：经 `app/api/deps.py`（`get_mcp_client` / `get_llm_provider`）
- MCP 异常：`from app.mcp.errors import MCPTimeoutError, MCPError`
- 展平：复用 `app.utils.evidence_helper._collect_all_evidence`（`state.model_dump()`）
- 置信度字段：`confidence_level`
- reducer：PRD 字段级覆盖（非列表拼去重）

---

## 4.1 State Schema + merge_case_evidence reducer

- [x] 创建 `app/models/state.py`
  - 枚举：`TaskStatus`（10 值）、`VerdictType`（4 值无 normal）、`ConfidenceLevel`（high/medium/low）
  - 模型：EvidenceItem、CaseEvidenceBundle、ReflectionReport、FactTimeline、ComplianceOpinion、UserDemandAnalysis、VerdictDraft、JudgmentPackage、ExceptionInfo、HumanReviewResult、ComplaintSessionState
  - 所有字段含 `Field(...)` 描述；提供测试友好默认值或 `_minimal_state()` 辅助
  - `case_evidence: Annotated[dict[str, CaseEvidenceBundle], merge_case_evidence]`（FATAL-3）
  - `merge_case_evidence`：**PRD 字段级覆盖**（右侧有值则整字段替换；含 history_complaints）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import (
      TaskStatus, VerdictType, ConfidenceLevel, CaseEvidenceBundle,
      ComplaintSessionState, merge_case_evidence,
  )
  assert len(list(TaskStatus)) == 10
  assert {v.value for v in VerdictType} == {
      'agent_violation','user_misunderstanding','compliant_but_poor_experience','pending'
  }
  assert 'normal' not in {v.value for v in VerdictType}
  assert {v.value for v in ConfidenceLevel} == {'high','medium','low'}
  # 最小 State 可构造（按实现补齐必填）
  s = ComplaintSessionState(
      session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
  )
  assert s.session_id == 's1'
  left = {'c1': CaseEvidenceBundle(case_info={'id': 'c1'}, call_evidence=[{'id': 'A'}])}
  right = {'c1': CaseEvidenceBundle(call_evidence=[{'id': 'B'}]), 'c2': CaseEvidenceBundle(case_info={'id': 'c2'})}
  m = merge_case_evidence(left, right)
  assert m['c1'].case_info == {'id': 'c1'}
  assert m['c1'].call_evidence == [{'id': 'B'}]  # 整字段覆盖，非 [A,B]
  assert 'c2' in m
  print('4.1 state + reducer OK')
  "
  ```

## 4.2 orchestrator 调度节点

- [x] 创建 `app/graph/nodes/orchestrator.py`：`async def orchestrator_node(state) -> dict`
  - 初始化 task_status=evidence_gathering
  - exception_info 非空 或 token 超阈值 → task_status=pending_review
  - 可 MCP 读工单基础信息（经 deps，Mock）
  - 禁止事实还原/合规判责/改子 Agent 输出
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.orchestrator import orchestrator_node
  def st(**kw):
      base=dict(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
      base.update(kw); return ComplaintSessionState(**base)
  out = asyncio.run(orchestrator_node(st()))
  assert out['task_status'] == 'evidence_gathering'
  out2 = asyncio.run(orchestrator_node(st(session_id='s2', exception_info={'err':'e'})))
  assert out2['task_status'] == 'pending_review'
  print('4.2 orchestrator OK')
  "
  ```

## 4.3 evidence_case 证据-案件节点

- [x] 创建 `app/graph/nodes/evidence_case.py`：`async def evidence_case_node(state) -> dict`
  - 遍历 case_ids，MCP 拉案件/历史工单/历史投诉（deps + Mock）
  - 写入**新** CaseEvidenceBundle
  - MCP 超时/错误 → exception_info（`app.mcp.errors`）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.evidence_case import evidence_case_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x', case_ids=['c1'])
  out = asyncio.run(evidence_case_node(s))
  assert 'case_evidence' in out and 'c1' in out['case_evidence']
  print('4.3 evidence_case OK')
  "
  ```

## 4.4 evidence_call_qc 证据-通话质检节点

- [x] 创建 `app/graph/nodes/evidence_call_qc.py`：`async def evidence_call_qc_node(state) -> dict`
  - MCP 拉 ASR/质检/告警（Mock）
  - `confidence_level`：is_corrupted→medium；残缺/核心缺失→low（可复用 `apply_corruption_degradation`）
  - 写入新 CaseEvidenceBundle
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.evidence_call_qc import evidence_call_qc_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x', case_ids=['c1'])
  out = asyncio.run(evidence_call_qc_node(s))
  assert 'case_evidence' in out and 'c1' in out['case_evidence']
  print('4.4 evidence_call_qc OK')
  "
  ```

## 4.14 evidence_re_gather 重新取证扇出节点

- [x] 创建 `app/graph/nodes/evidence_re_gather.py`：`async def evidence_re_gather_node(state) -> dict`
  - task_status=evidence_gathering；清除 exception_info
  - 出边由批次6 builder 配置（本批仅节点函数）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.evidence_re_gather import evidence_re_gather_node
  s = ComplaintSessionState(
      session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      exception_info={'err':'e'}, task_status='reflection_retry',
  )
  out = asyncio.run(evidence_re_gather_node(s))
  assert out['task_status'] == 'evidence_gathering'
  assert out.get('exception_info') is None
  print('4.14 evidence_re_gather OK')
  "
  ```

## 4.5 reflection 反思节点

- [x] 创建 `app/graph/nodes/reflection.py`：`async def reflection_node(state) -> dict`
  - 复用 `_collect_all_evidence(state.model_dump())`
  - `load_prompt("complaint_reflection")` + await invoke → ReflectionReport（Mock LLM）
  - loop_count += 1
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.reflection import reflection_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x', loop_count=0)
  out = asyncio.run(reflection_node(s))
  assert 'reflection_report' in out
  assert out['loop_count'] == 1
  print('4.5 reflection OK')
  "
  ```

## 4.6 fact 事实还原节点

- [x] 创建 `app/graph/nodes/fact.py`：`async def fact_node(state) -> dict`
  - `load_prompt("complaint_fact_timeline")` + Mock LLM → FactTimeline
  - 禁止违规判定/责任结论
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.fact import fact_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  out = asyncio.run(fact_node(s))
  assert 'fact_timeline' in out
  print('4.6 fact OK')
  "
  ```

## 4.7 compliance 合规判定节点

- [x] 创建 `app/graph/nodes/compliance.py`：`async def compliance_node(state) -> dict`
  - MCP `search_regulations` try/except（`app.mcp.errors` → rag_result=[] + exception_info）
  - `load_prompt("complaint_compliance")` + Mock LLM → ComplianceOpinion
  - 禁止最终定责
- [x] 验证（happy path + MCP 降级）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.compliance import compliance_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  out = asyncio.run(compliance_node(s))
  assert 'compliance_opinion' in out
  print('4.7 compliance happy OK')
  "
  ```

  MCP 降级路径用单测断言（monkeypatch MCP 抛 `MCPTimeoutError`，仍有 compliance_opinion + exception_info）。

## 4.8 user_demand 用户诉求节点

- [x] 创建 `app/graph/nodes/user_demand.py`：`async def user_demand_node(state) -> dict`
  - `load_prompt("complaint_user_demand")` + Mock LLM → UserDemandAnalysis
  - 禁止责任判定
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.user_demand import user_demand_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  out = asyncio.run(user_demand_node(s))
  assert 'user_demand' in out
  print('4.8 user_demand OK')
  "
  ```

## 单测

- [x] 创建 `tests/unit/test_state_reducer.py`：同 case_id 字段覆盖、互补字段保留、不同 case_id 保留（**非**列表拼去重）
- [x] 创建 `tests/unit/test_graph_nodes.py`（或 `test_graph_nodes_*.py`）：各节点 Mock 冒烟；含 **4.7 MCP 降级** Scenario
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest \
    tests/unit/test_state_reducer.py tests/unit/test_graph_nodes.py -v
  ```

  （若拆多文件，pytest 路径改为 `tests/unit/test_graph_nodes*.py`。）

---

## 全部完成标准

- [x] 9 个 **async** 节点可独立 `asyncio.run` 调用，输入 State → 输出 dict 增量
- [x] merge_case_evidence 单测通过（PRD 字段级覆盖；并行互补无丢失）
- [x] VerdictType 四值无 normal（按 `.value` 断言）；TaskStatus 10 值；ConfidenceLevel 3 值
- [x] 复用 evidence_helper；MCP 异常来自 `app.mcp.errors`；`confidence_level` 命名一致
- [x] 节点经 deps Mock；不编译完整 Graph
- [x] 未实现 4.9/4.10/4.11/4.12/4.13、业务 API、灰度降级、L4 护栏
- [x] 路径均在 services/complaint-service/ 下
