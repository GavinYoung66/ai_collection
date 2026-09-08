# Tasks: complaint-service-batch6

## 执行顺序

**4.9 / 4.10 / 4.11 / 4.13 可并行** → **4.12（edges + builder + checkpointer）最后**

环境：conda `ai_collection`，`PYTHONPATH=../..:$PYTHONPATH`，在 `services/complaint-service/` 下执行。
前置：`pip install "langgraph>=0.0.40" langgraph-checkpoint-postgres`。
约定：节点 `async`；DI 经 `app/api/deps.py`；路径禁止仓库根旁路树。

---

## State 增强（state-schema MODIFIED）

- [x] 修改 `app/models/state.py`
  - `VerdictDraft` 新增 `per_case_verdicts: dict[str, VerdictType] = Field(default_factory=dict)`
  - 新增 `ReviewAction` 枚举（6 值）
  - `HumanReviewResult.review_action: Optional[ReviewAction]`
  - **`ComplaintSessionState.reject_count: int = 0`**（权威计数）
  - `ExceptionInfo.error_type` 支持 `llm_degraded`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.models.state import VerdictDraft, HumanReviewResult, ReviewAction, VerdictType, ComplaintSessionState
  vd = VerdictDraft(verdict=VerdictType.pending, confidence=0.5, per_case_verdicts={'c1': VerdictType.agent_violation})
  assert vd.per_case_verdicts['c1'] == VerdictType.agent_violation
  assert len(list(ReviewAction)) == 6
  assert 'normal' not in {v.value for v in VerdictType}
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x', reject_count=0)
  assert s.reject_count == 0
  hr = HumanReviewResult(review_action=ReviewAction.approve)
  print('state enhance OK')
  "
  ```

## 4.9 verdict 综合判责节点

- [x] 创建 `app/graph/nodes/verdict.py`：`async def verdict_node(state) -> dict`
  - 经 `get_llm_provider()`；MODERATE-6 None 防御
  - `load_prompt("complaint_verdict")` + await invoke → VerdictDraft
  - confidence < 0.7 → conflict_highlight=True
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState, FactTimeline, ComplianceOpinion, UserDemandAnalysis
  from app.graph.nodes.verdict import verdict_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  out = asyncio.run(verdict_node(s))
  assert out['exception_info']['error_type'] == 'llm_degraded'
  assert out['task_status'] == 'pending_review'
  print('4.9 None-defense OK')
  s2 = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      fact_timeline=FactTimeline(summary='s'), compliance_opinion=ComplianceOpinion(overall_compliance='compliant'),
      user_demand=UserDemandAnalysis(demand_type='refund', emotion_level='calm', secondary_complaint_risk='low'))
  out2 = asyncio.run(verdict_node(s2))
  assert 'verdict_draft' in out2
  print('4.9 verdict OK')
  "
  ```

## 4.10 generate_package 产出物节点

- [x] 创建 `app/graph/nodes/generate_package.py`：`async def generate_package_node(state) -> dict`
  - 经 deps；无 verdict_draft / pending_review → 短路不调 MCP/LLM
  - MODERATE-5：MCP `search_response_templates` try/except
  - `load_prompt("complaint_generate_package")` → JudgmentPackage
- [x] 验证（happy path；**MCP 降级由 test_nodes_batch6 覆盖**）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState, VerdictDraft, VerdictType
  from app.graph.nodes.generate_package import generate_package_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      verdict_draft=VerdictDraft(verdict=VerdictType.pending, confidence=0.8))
  out = asyncio.run(generate_package_node(s))
  assert 'judgment_package' in out
  print('4.10 generate_package OK')
  "
  ```

## 4.11 human_review + sample_backflow

- [x] 创建 `app/graph/nodes/human_review.py`：`async def human_review_node(state) -> dict`
  - interrupt；approve/reject_rejudge/escalate 映射；**reject_rejudge → reject_count += 1（State 根）**
  - FATAL-1：无硬边到 sample_backflow
- [x] 创建 `app/graph/nodes/sample_backflow.py`：经 `get_mcp_client()`；失败重试 3 次不阻塞
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from unittest.mock import AsyncMock, patch
  from app.mcp.errors import MCPError
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.sample_backflow import sample_backflow_node
  async def fail(*a, **k): raise MCPError('s','t','boom')
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  with patch('app.graph.nodes.sample_backflow.get_mcp_client') as gm:
      gm.return_value.call = AsyncMock(side_effect=fail)
      out = asyncio.run(sample_backflow_node(s))
  print('4.11 sample_backflow retry OK')
  "
  ```

## 4.13 judgment_fanout 研判扇出节点

- [x] 创建 `app/graph/nodes/judgment_fanout.py`：`async def judgment_fanout_node(state) -> dict`
  - FATAL-2：仅 `{'task_status': 'judging'}`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from app.models.state import ComplaintSessionState
  from app.graph.nodes.judgment_fanout import judgment_fanout_node
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x')
  out = asyncio.run(judgment_fanout_node(s))
  assert out == {'task_status': 'judging'}
  print('4.13 judgment_fanout OK')
  "
  ```

## 4.12 edges.py + builder.py + checkpointer

- [x] 创建 `app/graph/edges.py`
  - `should_retry_or_proceed` / `should_proceed_after_verdict` / `should_proceed_after_review`
  - after_review 读 **`state.reject_count`**
- [x] 创建 `app/graph/builder.py`：`build_complaint_graph(checkpointer=None)`
  - 注册批次5 九节点 + 本批五节点
  - 连边含 verdict→should_proceed_after_verdict；4.13/4.14 扇出；human_review 条件边（无硬边到 backflow）
  - to_manual / to_senior_review → 回到 human_review
- [x] 创建 `app/graph/checkpointer.py`（或 builder 内工厂）
  - 生产：`asyncpg.create_pool(..., min_size=5, max_size=20)` + `AsyncPostgresSaver(pool)`（禁止伪 from_conn_string kwargs）
  - 本地/测试：MemorySaver stub；90 天/24h 钩子仅声明
- [x] 验证（条件边）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.graph.edges import should_retry_or_proceed, should_proceed_after_review, should_proceed_after_verdict
  from app.models.state import (
      ComplaintSessionState, ReflectionReport, HumanReviewResult, ReviewAction,
      ExceptionInfo, TaskStatus, VerdictDraft, VerdictType,
  )
  s = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      exception_info=ExceptionInfo(error_type='mcp', message='e'))
  assert should_retry_or_proceed(s) == 'to_manual'
  s2 = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      loop_count=0, max_loops=3, reflection_report=ReflectionReport(evidence_sufficient=False))
  assert should_retry_or_proceed(s2) == 'to_re_gather'
  s3 = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      reflection_report=ReflectionReport(evidence_sufficient=True))
  assert should_retry_or_proceed(s3) == 'to_judgment'
  # after_verdict
  s_v = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      task_status=TaskStatus.pending_review,
      exception_info=ExceptionInfo(error_type='llm_degraded', message='none'))
  assert should_proceed_after_verdict(s_v) == 'to_review'
  s_ok = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      verdict_draft=VerdictDraft(verdict=VerdictType.pending, confidence=0.9))
  assert should_proceed_after_verdict(s_ok) == 'to_package'
  # after_review
  s4 = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      reject_count=1, human_review=HumanReviewResult(review_action=ReviewAction.reject_rejudge))
  assert should_proceed_after_review(s4) == 'to_re_gather'
  s5 = ComplaintSessionState(session_id='s1', trace_id='t1', complaint_id='cmp1', complaint_text='x',
      human_review=HumanReviewResult(review_action=ReviewAction.escalate))
  assert should_proceed_after_review(s5) == 'to_senior_review'
  print('4.12 edges OK')
  "
  ```
- [x] 验证（图编译）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.graph.builder import build_complaint_graph
  g = build_complaint_graph()
  assert g is not None
  print('4.12 graph compile OK')
  "
  ```

## 单测

- [x] `tests/unit/test_edges.py`：三条条件边全分支（含 after_verdict）
- [x] `tests/unit/test_nodes_batch6.py`：verdict None 防御 / **generate_package MCP 降级** / 短路 / judgment_fanout
- [x] `tests/unit/test_graph_compile.py`：**强制**全链路 Mock（interrupt resume → resolved）+ 编译
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest \
    tests/unit/test_edges.py tests/unit/test_nodes_batch6.py tests/unit/test_graph_compile.py -v
  ```

---

## 全部完成标准

- [x] `build_complaint_graph()` 编译通过
- [x] `test_graph_compile.py`：evidence_gathering → resolved（interrupt approve）
- [x] 扇出边 4.13/4.14 正确；verdict 后 `should_proceed_after_verdict` 存在
- [x] checkpointer：asyncpg 池 min=5/max=20 + Saver；本地 MemorySaver stub
- [x] FATAL-1/2、MODERATE-4/5/6 落实；reject_count 在 State 根
- [x] VerdictType 无 normal；节点经 deps；路径仅 services/complaint-service/
- [x] 未实现批次7 API / 模块10 / 批次8；90 天/24h 仅钩子
