# Design: complaint-service-batch5

## Context

批次2（数据层/证据助手）、批次3（MCP Client + LLM Provider + ModelRouter）、批次4（PromptOps loader + A/B）已归档。`app/graph/nodes/` 仅有空 `__init__.py`，`app/models/state.py` 不存在（`complaint_session.py` 是 ORM 表模型，非 LangGraph State）。本批建立 LangGraph 编排上半：State Schema + 9 个节点函数，节点可独立调用，默认走 Mock，不编译整图（批次6）。

## Goals / Non-Goals

**Goals:**
- 每个本批节点可独立调用：输入 State → 输出 State 增量；Pydantic Schema 校验通过
- `merge_case_evidence` reducer 单元测试通过（并行写入无竞态）
- VerdictType 四值无 normal；TaskStatus 10 值齐全
- 节点默认走 Mock MCP / Mock LLM

**Non-Goals:**
- 4.9 verdict / 4.10 generate_package / 4.11 human_review+sample_backflow
- 4.12 edges.py + builder.py + AsyncPostgresSaver（整图编译批次6）
- 4.13 judgment_fanout（批次6）
- 业务 API、灰度降级、L4 护栏

## Decisions

### D1: 节点函数统一 async

所有节点：`async def xxx_node(state: ComplaintSessionState) -> dict`，返回 State 增量。

理由：批次3 `MCPClient.call` / `LLMProvider.invoke` 均为 async；节点内直接 await，避免在节点里 `asyncio.run`。

验证：tasks 冒烟一律 `asyncio.run(xxx_node(s))`。

备选：sync 节点外包一层 → 拒绝（与 Provider/MCP 栈不一致）。

### D2: State Schema 与 ORM 解耦但字段对齐

`app/models/state.py` 用 Pydantic v2 定义 LangGraph State，字段对齐 `complaint_session.py` ORM 列名。PRD 中 `session_id` / `trace_id` / `complaint_id` / `complaint_text` 等为核心标识；**实现时对冒烟友好**：非核心字段给默认值；核心标识在单测/`_minimal_state()` 辅助中显式传入（tasks 统一用该辅助，避免半残构造）。

- 备选：复用 ORM → 拒绝

### D3: merge_case_evidence 对齐 PRD 6.1（字段级覆盖）

权威语义（PRD FATAL-3）：

```python
def merge_case_evidence(left, right):
    merged = dict(left)
    for case_id, bundle in right.items():
        if case_id in merged:
            existing = merged[case_id]
            # 右侧字段「有值」则覆盖左侧同名字段（整字段替换，非列表拼去重）
            if bundle.case_info: existing.case_info = bundle.case_info
            if bundle.call_evidence: existing.call_evidence = bundle.call_evidence
            if bundle.history_tickets: existing.history_tickets = bundle.history_tickets
            if bundle.history_complaints: existing.history_complaints = bundle.history_complaints
            if bundle.qc_alerts: existing.qc_alerts = bundle.qc_alerts
        else:
            merged[case_id] = bundle
    return merged
```

- 并行场景：evidence_case 写 case_info/history_*，evidence_call_qc 写 call_evidence/qc_alerts → 字段互补保留
- **禁止**本批改成「列表合并去重」另套语义（与 PRD 冲突）
- 单测构造真实 `CaseEvidenceBundle`，不用含糊裸 dict（除非内部先 model_validate）

### D4: Mock / DI 定稿——经 deps 工厂

节点通过 `app/api/deps.py` 的 `get_mcp_client()` / `get_llm_provider()` 获取实例（`USE_MOCK_MCP` / `USE_MOCK_LLM`）。**不在节点内直接 `if USE_MOCK` 分支构造。**

单测可 monkeypatch deps 或注入 Mock。

### D5: 复用批次2 `_collect_all_evidence`

权威实现：`app/utils/evidence_helper._collect_all_evidence(state: dict) -> list[dict]`。

节点侧：`_collect_all_evidence(state.model_dump())`（或薄封装接受 ComplaintSessionState）。**禁止**在 `app/graph/` 再造第二套展平逻辑。

ASR/残缺降级优先复用 `apply_corruption_degradation`（`confidence_level` 字段，对齐 data-layer / PRD 7.3）。

### D6: evidence_re_gather 出边接口约定（批次6 衔接）

本批 `evidence_re_gather_node` 仅返回 `{"task_status": "evidence_gathering", "exception_info": None}`。批次6 builder 配边：`evidence_re_gather → evidence_case`、`→ evidence_call_qc`。节点不感知出边。

### D7: MCP 异常导入

从 **`app.mcp.errors`** 导入 `MCPTimeoutError` / `MCPError`（非 `app.mcp.client`）。

## Risks / Trade-offs

- **[State 与 ORM 字段漂移]** → 命名对齐 ORM；批次6 再校验序列化
- **[Mock LLM Schema]** → 用批次0 fixtures / 最小合法 JSON
- **[PRD「有值」判定]** → 空 list / None 视为无值不覆盖；与 PRD `if bundle.xxx:` 一致

## Migration Plan

1. 新建 `app/models/state.py`
2. 新建 9 个 async 节点文件
3. 单测：reducer + `test_graph_nodes*.py` Mock 冒烟（含 4.7 MCP 降级）
4. 回滚：删除新增文件即可

## Open Questions

- （已关闭）deps vs 节点内构造 → **D4 定稿经 deps**
- （已关闭）reducer 列表合并 vs PRD 覆盖 → **D3 定稿 PRD 字段级覆盖**
