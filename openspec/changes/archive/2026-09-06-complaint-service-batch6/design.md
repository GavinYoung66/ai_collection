# Design: complaint-service-batch6

## Context

批次5 已交付 State Schema + 9 个 LangGraph 上半节点。本批闭环：判责草稿（4.9）、产出物（4.10）、人工断点（4.11）、研判扇出（4.13）、整图编译连边（4.12）。落实 PRD v1.6 FATAL-1/2、MODERATE-4/5/6。

## Goals / Non-Goals

**Goals:**
- `build_complaint_graph()` 编译通过
- 全链路 Mock：evidence_gathering → resolved（含 interrupt；强制由 `tests/unit/test_graph_compile.py` 覆盖）
- 两个扇出边正确：4.13→fact/compliance/user_demand；4.14→evidence_case/evidence_call_qc
- 生产 checkpointer：asyncpg 连接池 min=5/max=20 + AsyncPostgresSaver；本地 stub

**Non-Goals:**
- 不重写批次5 节点（仅注册/连边）
- 不实现批次7 API、模块10、批次8
- 不接生产 PG/Redis
- 90 天归档 / 24h 挂起仅钩子，不实现定时任务
- 禁止仓库根 `complaint-service/` 旁路树

## Decisions

### D1: 图拓扑与条件边

```
START → orchestrator → [evidence_case ‖ evidence_call_qc] → reflection
reflection → should_retry_or_proceed:
  to_manual   → human_review
  to_re_gather → evidence_re_gather → [evidence_case ‖ evidence_call_qc] → reflection
  to_judgment → judgment_fanout → [fact ‖ compliance ‖ user_demand] → verdict
verdict → should_proceed_after_verdict:
  to_review  → human_review          # MODERATE-6 / pending_review / 无 verdict_draft
  to_package → generate_package → human_review
human_review → should_proceed_after_review:
  to_re_gather     → evidence_re_gather
  to_manual        → human_review（再次 interrupt，pending_review）
  to_senior_review → human_review（再次 interrupt，pending_senior_review）
  to_backflow      → sample_backflow → END
```

- 理由：FATAL-2 / MODERATE-4；**verdict 后不得无条件硬进 generate_package**（避免 None 防御后仍编造产出）
- `to_manual` / `to_senior_review` **定稿**：回到 `human_review` 再 interrupt（非 END）

### D2: human_review 用 interrupt

`interrupt({...})` 暂停；`Command(resume=review_action)` 恢复。恢复后更新 task_status，并维护 **State.reject_count**（reject_rejudge 时 +1）。

### D3: FATAL-1

human_review **无硬边**到 sample_backflow；统一 `should_proceed_after_review`。reject_rejudge → evidence_re_gather。

### D4: checkpointer = 池 + Saver（禁止伪参数）

生产路径（语义对齐 step8，禁止裸连接）：

```python
pool = await asyncpg.create_pool(dsn=..., min_size=5, max_size=20)
checkpointer = AsyncPostgresSaver(pool)  # 或官方等价「传入已建池」构造
# 禁止：AsyncPostgresSaver.from_conn_string(..., min_size=5, max_size=20)  # 非真实 API
```

- 本地/单测：`MemorySaver`（或 AsyncSqliteSaver）stub，不连真 PG
- `get_checkpointer()`：PG 失败 → Redis fallback **接口**（本批 stub 回 MemorySaver，不假装接真 Redis）
- 池参数以 **asyncpg.create_pool** 为准，不依赖 Saver 构造函数是否暴露 min_size

### D5: State 增强

- `VerdictDraft.per_case_verdicts`
- `ReviewAction` 六值；`HumanReviewResult.review_action`
- **`ComplaintSessionState.reject_count: int = 0`**（权威计数，边函数读此字段；HumanReviewResult 可镜像，不得作为唯一存储）
- 节点 DI：一律 `app/api/deps.get_mcp_client()` / `get_llm_provider()`（对齐批次5）

### D6: 90 天 / 24h 钩子

`archive_expired_sessions()` / `handle_suspended_sessions()` 仅签名 + TODO，不实现调度。

### D7: should_proceed_after_verdict

```text
若 task_status == pending_review
   或 exception_info.error_type == llm_degraded
   或 verdict_draft is None
→ "to_review"
否则 → "to_package"
```

`generate_package_node` 仍建议防御性短路（双保险）：已 pending_review / 无 verdict_draft → 不调 MCP/LLM，原样返回或空增量。

## Risks / Trade-offs

- **[langgraph 未安装]** → 实施前 pip install
- **[AsyncPostgresSaver 构造签名]** → 以安装后官方文档为准，但**连接池必须由 asyncpg 创建**；不得用伪造 kwargs 糊弄验收
- **[interrupt API]** → 以实际 langgraph 版本为准

## Migration Plan

1. 增强 state.py（含 reject_count 根字段）
2. 4.9 / 4.10 / 4.11 / 4.13（可并行）
3. 4.12 edges（含 after_verdict）+ builder + checkpointer
4. 单测：节点 + 条件边 + `test_graph_compile.py` 全链路 Mock（interrupt→resolved）

## Open Questions

- （已关闭）to_manual / to_senior_review 路由 → **D1 定稿回 human_review 再 interrupt**
- （已关闭）连接池参数挂载点 → **D4 定稿 asyncpg.create_pool**
- AsyncPostgresSaver 与 pool 的精确构造函数名以安装后 API 为准（不阻塞规划）
