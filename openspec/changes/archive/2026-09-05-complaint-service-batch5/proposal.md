# Proposal: complaint-service-batch5

## Why

批次2（数据层/证据助手）、批次3（MCP+LLM Provider+Router）、批次4（PromptOps 加载器+版本/A-B）已归档。LangGraph 编排层尚未建立——缺少统一 State Schema、节点函数与扇出入口，导致后续批次6（图编译+checkpointer）和批次7（业务 API）无从挂载。本批交付 LangGraph 编排上半：State Schema（含 FATAL-3 reducer）+ 9 个节点（调度/证据×2/扇出/反思/事实/合规/用户诉求），使每个节点可独立调用并产出 State 增量，为批次6 整图编译打好基础。

## What Changes

**4.1 State Schema + reducer（新增）**
- `app/models/state.py`：TaskStatus(10值)、VerdictType(4值无 normal)、ConfidenceLevel(3值)、EvidenceItem、CaseEvidenceBundle、ReflectionReport、FactTimeline、ComplianceOpinion、UserDemandAnalysis、VerdictDraft、JudgmentPackage、ExceptionInfo、HumanReviewResult、ComplaintSessionState
- `merge_case_evidence` 自定义 reducer + `case_evidence: Annotated[dict[str, CaseEvidenceBundle], merge_case_evidence]`（FATAL-3）
- reducer 语义对齐 **PRD 6.1**：同 case_id 按字段级覆盖（右侧字段非空则覆盖左侧同名字段），**非**列表合并去重
- 所有字段含 Field 描述；冒烟/单测可用的合理默认值（或构造辅助），避免缺必填无法实例化

**4.2～4.8 + 4.14 节点（新增）**
- 统一签名：`async def xxx_node(state: ComplaintSessionState) -> dict`（因 MCP `call` / LLM `invoke` 均为 async）
- 经 `app/api/deps.py` 注入 MCP/LLM；默认 Mock
- 复用批次2 `app/utils/evidence_helper._collect_all_evidence`（State → `model_dump()` 再展平，勿另造第二套）
- MCP 异常从 `app.mcp.errors` 导入（`MCPTimeoutError` / `MCPError`）
- 证据置信度字段名：`confidence_level`（对齐批次2 / data-layer）；ASR 乱码→medium，录音残缺/核心缺失→low

**4.2 orchestrator**：弱中心化、纯代码分支，不调 LLM 业务推理；异常/Token 超限 → pending_review；初始化 evidence_gathering

**4.3 evidence_case**：遍历 case_ids；写**新** CaseEvidenceBundle；MCP 错 → exception_info

**4.4 evidence_call_qc**：ASR/质检/告警；`confidence_level` 降级；写新 Bundle

**4.14 evidence_re_gather**：task_status=evidence_gathering；清 exception_info；**仅节点**，出边批次6

**4.5 reflection**：`load_prompt("complaint_reflection")` + invoke → ReflectionReport；loop_count += 1

**4.6 fact**：`load_prompt("complaint_fact_timeline")` → FactTimeline

**4.7 compliance**：MCP `search_regulations` try/except → 再 invoke → ComplianceOpinion

**4.8 user_demand**：`load_prompt("complaint_user_demand")` → UserDemandAnalysis

## Non-Goals

- 不实现 4.9 verdict / 4.10 generate_package / 4.11 human_review+sample_backflow
- 不实现 4.12 edges.py + builder.py + AsyncPostgresSaver（整图编译留给批次6）
- 不实现 4.13 judgment_fanout（批次6）
- 不实现业务 API、灰度降级、L4 护栏
- 本批不编译完整 Graph

## Capabilities

### New Capabilities
- `complaint-service/state-schema`: LangGraph State Schema + merge_case_evidence reducer（4.1）
- `complaint-service/graph-nodes`: LangGraph 编排节点（4.2~4.8 + 4.14，9 个节点）

## Impact

- **新增代码**：`app/models/state.py`、`app/graph/nodes/{orchestrator,evidence_case,evidence_call_qc,evidence_re_gather,reflection,fact,compliance,user_demand}.py`、`tests/unit/test_state_reducer.py`、`tests/unit/test_graph_nodes*.py`
- **依赖**：批次2（数据层/证据助手）、批次3（MCP/LLM/Router）、批次4（PromptOps）
- **路径**：一律 `services/complaint-service/`
