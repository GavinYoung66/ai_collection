# Spec Delta: complaint-service/data-layer

## Purpose

为 complaint-service 建立数据层：ORM 模型（ComplaintSession / ComplaintAgentLog / Complaint 扩展）、Alembic 迁移脚本、Redis Key 模式、证据工具函数。复用 M0 common 的 Base/PublicFieldsMixin/Redis 连接，不重复造轮子。

## ADDED Requirements

### Requirement: ComplaintSession ORM 模型

系统 SHALL 在 `app/models/complaint_session.py` 定义 `ComplaintSession`，继承 `common.db.base.Base` + `PublicFieldsMixin`，字段对齐 PRD 9.1 DDL：
- `session_id: str`（UNIQUE NOT NULL）
- `trace_id: str`（NOT NULL）
- `complaint_id: UUID`（FK → complaints.id）
- `case_ids: JSONB`（默认 `[]`）
- `session_ids: JSONB`（默认 `[]`）
- `user_id: str`（可空）
- `complaint_text: str`（NOT NULL）
- `loop_count: int`（CHECK 0~3，默认 0）
- `max_loops: int`（默认 3）
- `case_evidence / cross_case_evidence / historical_complaint_evidence: JSONB`
- `reflection_report / fact_timeline / compliance_opinion / user_demand / verdict_draft / judgment_package: JSONB`
- `exception_info: JSONB`（默认 `{}`）
- `human_review: JSONB`
- `task_status: str`（CHECK 10 值：pending/evidence_gathering/reflection_retry/judging/draft_generated/pending_review/pending_senior_review/resolved/closed/archived，默认 pending）
- `total_tokens_consumed / max_token_threshold: int`
- `mode: str`（CHECK single_agent/multi_agent，默认 multi_agent）

#### Scenario: ORM 可 import 且字段对齐

- **WHEN** `from app.models.complaint_session import ComplaintSession`
- **THEN** 类含上述全部字段，`task_status` CHECK 含 10 个状态值

#### Scenario: 本表无 verdict 列

- **WHEN** 检查 ComplaintSession 表/ORM 列集合
- **THEN** 不含独立 `verdict` 列；判责四值约束落在 `Complaint.verdict`（及后续 `verdict_draft` JSON 内容），不在本表重复定义

### Requirement: ComplaintAgentLog ORM 模型

系统 SHALL 在 `app/models/complaint_agent_log.py` 定义 `ComplaintAgentLog`，append-only（无 is_delete）：
- `session_id: str`（FK → complaint_sessions.session_id）
- `trace_id: str`
- `agent_role: str`（CHECK 10 值：orchestrator/evidence_case/evidence_call_qc/reflection/fact/compliance/user_demand/verdict/generate_package/sample_backflow）
- `step_name: str`
- `input_summary: str`
- `output_json: JSONB`
- `mcp_calls: JSONB`（默认 `[]`）
- `llm_call_log_ids: JSONB`（默认 `[]`）
- `tokens_consumed: int`
- `latency_ms: int`
- `status: str`（CHECK 4 值：success/failed/degraded/skipped）
- `error_msg: str`
- `create_time`（来自 PublicFieldsMixin 或单独声明，无 update_time/is_delete）

#### Scenario: append-only 无 is_delete

- **WHEN** 检查 ComplaintAgentLog 字段
- **THEN** 不含 `is_delete` 和 `update_time` 字段

#### Scenario: agent_role 与 status CHECK 齐全

- **WHEN** 检查 CHECK 约束
- **THEN** agent_role 含 10 值，status 含 success/failed/degraded/skipped

### Requirement: Complaint ORM 映射与扩展

系统 SHALL 在 `app/models/complaint.py` 定义 `Complaint` ORM，映射已有 `complaints` 表（0001_init_tables.py 创建），并包含 PRD 9.2 扩展字段：
- 已有字段：id, case_id, session_id, complaint_text, verdict, evidence, suggestion, status, create_time, update_time, is_delete
- 新增字段：`verdict_reasoning: str`、`response_draft: str`、`regulation_refs: JSONB`（默认 `[]`）、`confidence: float`（CHECK 0~1）、`internal_improvement: str`、`conflict_highlight: bool`（默认 False）
- `verdict` CHECK 同步为四值：agent_violation / user_misunderstanding / compliant_but_poor_experience / pending（去掉 normal）

#### Scenario: Complaint ORM 可 import

- **WHEN** `from app.models.complaint import Complaint`
- **THEN** 类含上述全部字段（已有 + 扩展）

#### Scenario: verdict 枚举不含 normal

- **WHEN** 检查 Complaint.verdict 的 CHECK 约束
- **THEN** 仅含四值枚举，不含 normal

### Requirement: Alembic 迁移脚本

系统 SHALL 在 `migrations/versions/0010_create_complaint_service_tables.py` 提供迁移：
- `CREATE TABLE complaint_sessions`（对齐 PRD 9.1，含全部 CHECK 约束）
- `CREATE TABLE complaint_agent_logs`（对齐 PRD 9.1，append-only）
- `ALTER TABLE complaints ADD COLUMN IF NOT EXISTS` 6 个扩展字段
- `ALTER TABLE complaints` 修改 verdict CHECK 约束为四值（去掉 normal）
- 索引：complaint_sessions 的 session_id/complaint_id/task_status/trace_id/active 部分索引；complaint_agent_logs 的 session_id/agent_role/create_time/trace_id

#### Scenario: 迁移可 upgrade

- **WHEN** `alembic upgrade head`
- **THEN** complaint_sessions、complaint_agent_logs 表创建成功，complaints 扩展字段存在

#### Scenario: ALTER 幂等

- **WHEN** 迁移脚本对 complaints 扩展列使用 `ADD COLUMN IF NOT EXISTS`（对齐 `0009` 的 `op.execute` 风格）
- **THEN** 重复执行该 ALTER SQL 不因列已存在而失败

### Requirement: Redis Key 模式

系统 SHALL 在 `app/utils/redis_keys.py` 定义 9 个 Key 模式，DB6，前缀 `csmp:`，并提供与 PRD 9.3 一致的 TTL 常量（秒；永不过期为 `None`），复用 common Redis 连接（本批可仅导出 Key/TTL，不强制新建读写封装）：
1. `csmp:session:{session_id}` — STRING(JSON)，TTL 24h（`TTL_SESSION=86400`）
2. `csmp:ticket:{id}:pkg` — STRING(JSON)，TTL 24h（`TTL_TICKET_PKG=86400`）
3. `csmp:verdict:{id}:latest` — STRING(JSON)，TTL 24h（`TTL_VERDICT_LATEST=86400`）
4. `csmp:rslv:{id}:latest` — STRING(JSON)，TTL 24h（`TTL_RSLV_LATEST=86400`）
5. `csmp:hotspots:{start}:{end}` — STRING(JSON)，TTL 6h（`TTL_HOTSPOTS=21600`）
6. `csmp:gather:lock:{id}` — STRING(SET NX EX)，TTL 120s（`TTL_GATHER_LOCK=120`）
7. `csmp:llm_degraded` — STRING("1")，永不过期（`TTL_LLM_DEGRADED=None`）
8. `csmp:task:timeout:{session_id}` — STRING，TTL 30min（`TTL_TASK_TIMEOUT=1800`）
9. `csmp:mode:active` — STRING，永不过期（`TTL_MODE_ACTIVE=None`）

#### Scenario: 9 个 Key 均可构造

- **WHEN** 调用 redis_keys 模块的各 Key 构造函数
- **THEN** 返回符合上述模式的字符串，前缀均为 `csmp:`

#### Scenario: TTL 常量对齐 PRD 9.3

- **WHEN** 读取 redis_keys 中的 TTL_* 常量
- **THEN** 与上表秒数一致（永不过期项为 `None`）

#### Scenario: 复用 common Redis 连接

- **WHEN** 检查 redis_keys.py 的 import / 文档约定
- **THEN** 不新建独立连接池；连接复用 common Redis 配置

### Requirement: 证据工具函数

系统 SHALL 在 `app/utils/evidence_helper.py` 提供：
- `_collect_all_evidence(state) -> list[EvidenceItem]`：展平 case_evidence（按案件分组）+ cross_case_evidence + historical_complaint_evidence 为统一证据列表
- 6 条冲突解决规则辅助函数（与 design 一一对应）：
  1. `sort_by_priority(items) -> list`：按 design 中 source→rank 映射排序（结构化 > 通话ASR > 质检 > 历史工单 > 历史投诉 > 用户自述/其它）
  2. `filter_by_confidence(items, *, for_judgment=True) -> list`：判责路径剔除 `confidence_level=low`（low 仅呈现）
  3. `mark_medium_auxiliary(items) -> list`：独立函数，给 medium 打 `auxiliary=True`，不作核心依据
  4. `detect_core_conflict(items) -> list`：检测金额/天数等核心事实冲突，标记 `conflict_highlight`
  5. `apply_corruption_degradation(items) -> list`：**对齐 PRD 7.3**——`is_corrupted=True` 默认降为 **medium**；`core_content_missing=True`（或 content 空）再降为 **low**
  6. `mark_conflict_pending(items) -> list`：冲突证据标记 `pending_for_package=True`，不下放产出物生成 Agent

#### Scenario: _collect_all_evidence 展平正确

- **WHEN** 传入含多案件 case_evidence + cross_case + historical 的 state
- **THEN** 返回统一 EvidenceItem 列表，含所有来源证据

#### Scenario: 6 条规则函数可单测

- **WHEN** 运行 `tests/unit/test_evidence_helper.py`
- **THEN** 6 条规则各至少 1 个用例通过（含独立的 `mark_medium_auxiliary` 用例）

#### Scenario: ASR 残缺降级（PRD 7.3）

- **WHEN** 证据 item 的 `is_corrupted=True` 且未标记核心内容缺失
- **THEN** `apply_corruption_degradation` 后其 `confidence_level` 为 **medium**

- **WHEN** 证据 item 的 `is_corrupted=True` 且 `core_content_missing=True`（或 content 为空）
- **THEN** `apply_corruption_degradation` 后其 `confidence_level` 为 **low**
