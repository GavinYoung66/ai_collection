# Proposal: complaint-service-batch6

## Why

批次5 已交付 LangGraph 上半（State Schema + 9 个节点：orchestrator/evidence_case/evidence_call_qc/evidence_re_gather/reflection/fact/compliance/user_demand）。但流程尚未闭环：缺少判责草稿（4.9）、产出物生成（4.10）、人工复核断点（4.11）、研判扇出（4.13）以及整图编译连边（4.12）。本批交付 LangGraph 编排下半，使 `build_complaint_graph()` 可编译并跑通全链路 Mock（evidence_gathering → resolved，含 interrupt 人工断点可测路径），落实 PRD v1.6 的 FATAL-1/2 与 MODERATE-4/5/6 修复。

## What Changes

**4.9 verdict 综合判责节点（新增）**
- MODERATE-6：开头检查 fact_timeline / compliance_opinion / user_demand；任一 None → exception_info(type=llm_degraded) + task_status=pending_review，直接返回
- `load_prompt("complaint_verdict")` + LLM（经 `deps.get_llm_provider()`）→ VerdictDraft（含 per_case_verdicts）
- confidence < 0.7 → conflict_highlight=True；verdict 四值无 normal；仅草稿

**4.10 generate_package 产出物节点（新增）**
- MODERATE-5：MCP search_response_templates try/except（经 deps；MCPTimeoutError/MCPError → rag_result=[] + exception_info，降级模板填充）
- 若上游已 pending_review / 无 verdict_draft：短路跳过 LLM/MCP，不编造产出
- `load_prompt("complaint_generate_package")` → JudgmentPackage；禁止编造事实/法规

**4.11 human_review + sample_backflow（新增）**
- human_review：`langgraph.types.interrupt` 等待人工；review_action 映射 task_status
- FATAL-1：human_review **无硬边** 到 sample_backflow；由 `should_proceed_after_review` 条件边路由
- `reject_count` 落在 **ComplaintSessionState 根字段**（跨轮持久）；reject_rejudge 时 +1
- sample_backflow：MCP write_session + write_human_review_truth；失败重试 3 次，仍失败告警不阻塞

**4.13 judgment_fanout 研判扇出节点（新增）**
- FATAL-2：仅设 task_status=judging，无业务推理；出边由 4.12 配三条并行边

**4.12 edges.py + builder.py + checkpointer（新增，最后做）**
- `should_retry_or_proceed` / `should_proceed_after_review`（reject_count 读 State 根字段）
- **`should_proceed_after_verdict`**：pending_review 或 exception llm_degraded 或无 verdict_draft → `to_review`（→human_review）；否则 → `to_package`（→generate_package）
- checkpointer：`asyncpg.create_pool(min_size=5, max_size=20)` + `AsyncPostgresSaver(pool)`（禁止裸连接 / 禁止把 min_size 伪造成 from_conn_string 参数）
- 本地 stub：MemorySaver；PG 故障 Redis fallback 接口 stub
- 90 天归档 / 24h 挂起：仅钩子声明

**State 增强**
- VerdictDraft.per_case_verdicts；ReviewAction 枚举；HumanReviewResult.review_action
- ComplaintSessionState.reject_count: int = 0

## Non-Goals

- 不重写批次5 已交付的 4.1～4.8、4.14 节点（仅在 4.12 注册/连边）
- 不实现批次7 业务 API、模块10 安全护栏、批次8 业务服务层
- 不接生产 PG/Redis（checkpointer 本地 stub）
- 不实现 90 天归档 / 24h 挂起定时任务（仅钩子）
- 路径一律 `services/complaint-service/`；**禁止**仓库根旁路树

## Capabilities

### Modified Capabilities
- `complaint-service/state-schema`: VerdictDraft.per_case_verdicts + ReviewAction + State.reject_count
- `complaint-service/graph-nodes`: 新增 4.9/4.10/4.11/4.13 节点行为契约

### New Capabilities
- `complaint-service/graph-builder`: edges + builder + 池化 AsyncPostgresSaver

## Impact

- **新增代码**：`app/graph/nodes/{verdict,generate_package,human_review,sample_backflow,judgment_fanout}.py`、`app/graph/edges.py`、`app/graph/builder.py`、`app/graph/checkpointer.py`（或等价）、单测
- **修改**：`app/models/state.py`
- **依赖**：批次5 + 批次2/3/4；节点经 `app/api/deps.py` 注入 MCP/LLM
- **langgraph**：实施前 `pip install "langgraph>=0.0.40" langgraph-checkpoint-postgres`
