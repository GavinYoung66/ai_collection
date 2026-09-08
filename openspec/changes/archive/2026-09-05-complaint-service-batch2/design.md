# Design: complaint-service-batch2

## 1. 现有数据层现状分析

### 1.1 common.db.base

```python
class Base(DeclarativeBase): pass
class PublicFieldsMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    update_time = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_delete = Column(CHAR(1), nullable=False, default="0")
```

### 1.2 现有 complaints 表（0001_init_tables.py）

```sql
CREATE TABLE complaints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    session_id VARCHAR(100) REFERENCES call_records(session_id) ON DELETE CASCADE,
    complaint_text TEXT NOT NULL,
    verdict VARCHAR(30) CHECK (verdict IN ('agent_violation','user_misunderstanding','normal','pending')),
    evidence JSONB,
    suggestion TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','investigating','resolved','closed')),
    create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
);
```

**关键缺口**：
- 仓库内**无现有 Complaint ORM 模型**（`class Complaint` 全局搜索无结果）
- `verdict` CHECK 含 `normal`，需改为四值枚举（去掉 normal）
- 缺少 PRD 9.2 的 6 个扩展字段

**设计决策**：在 `app/models/complaint.py` 新建 Complaint ORM，映射已有 `complaints` 表（`__tablename__ = "complaints"`），不重复建表。扩展字段通过 0010 迁移 ALTER 添加。

### 1.3 migrations 命名约定

现有版本：`0001_init_tables.py` ~ `0009_kb_documents_ingest_observability.py`  
下一个版本号：**`0010_create_complaint_service_tables.py`**

## 2. ORM 模型设计

### 2.1 ComplaintSession

```python
# app/models/complaint_session.py
from sqlalchemy import Column, String, Integer, Text, CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from common.db.base import Base, PublicFieldsMixin

class ComplaintSession(Base, PublicFieldsMixin):
    __tablename__ = "complaint_sessions"
    session_id = Column(String(100), unique=True, nullable=False)
    trace_id = Column(String(100), nullable=False)
    complaint_id = Column(UUID(as_uuid=True), ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False)
    case_ids = Column(JSONB, nullable=False, default=list)
    session_ids = Column(JSONB, nullable=False, default=list)
    user_id = Column(String(100))
    complaint_text = Column(Text, nullable=False)
    loop_count = Column(Integer, nullable=False, default=0)
    max_loops = Column(Integer, nullable=False, default=3)
    case_evidence = Column(JSONB, default=dict)
    cross_case_evidence = Column(JSONB, default=list)
    historical_complaint_evidence = Column(JSONB, default=list)
    reflection_report = Column(JSONB)
    fact_timeline = Column(JSONB)
    compliance_opinion = Column(JSONB)
    user_demand = Column(JSONB)
    verdict_draft = Column(JSONB)
    judgment_package = Column(JSONB)
    exception_info = Column(JSONB, default=dict)
    human_review = Column(JSONB)
    task_status = Column(String(30), nullable=False, default="pending")
    total_tokens_consumed = Column(Integer, default=0)
    max_token_threshold = Column(Integer, default=50000)
    mode = Column(String(20), nullable=False, default="multi_agent")
    __table_args__ = (
        CheckConstraint("loop_count BETWEEN 0 AND 3"),
        CheckConstraint("task_status IN ('pending','evidence_gathering','reflection_retry','judging','draft_generated','pending_review','pending_senior_review','resolved','closed','archived')"),
        CheckConstraint("mode IN ('single_agent','multi_agent')"),
    )
```

### 2.2 ComplaintAgentLog（append-only）

```python
# app/models/complaint_agent_log.py
from sqlalchemy import Column, String, Integer, Text, CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from common.db.base import Base
from common.db.base import PublicFieldsMixin  # 仅取 create_time

class ComplaintAgentLog(Base):
    __tablename__ = "complaint_agent_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(100), ForeignKey("complaint_sessions.session_id", ondelete="CASCADE"), nullable=False)
    trace_id = Column(String(100), nullable=False)
    agent_role = Column(String(50), nullable=False)
    step_name = Column(String(100), nullable=False)
    input_summary = Column(Text)
    output_json = Column(JSONB)
    mcp_calls = Column(JSONB, default=list)
    llm_call_log_ids = Column(JSONB, default=list)
    tokens_consumed = Column(Integer, default=0)
    latency_ms = Column(Integer)
    status = Column(String(20), nullable=False)
    error_msg = Column(Text)
    create_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 注意：append-only，无 update_time / is_delete
    __table_args__ = (
        CheckConstraint("agent_role IN ('orchestrator','evidence_case','evidence_call_qc','reflection','fact','compliance','user_demand','verdict','generate_package','sample_backflow')"),
        CheckConstraint("status IN ('success','failed','degraded','skipped')"),
    )
```

### 2.3 Complaint（映射已有表 + 扩展字段）

```python
# app/models/complaint.py
from sqlalchemy import Column, String, Text, Boolean, Float, CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from common.db.base import Base, PublicFieldsMixin

class Complaint(Base, PublicFieldsMixin):
    __tablename__ = "complaints"
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(100), ForeignKey("call_records.session_id", ondelete="CASCADE"))
    complaint_text = Column(Text, nullable=False)
    verdict = Column(String(30))
    evidence = Column(JSONB)
    suggestion = Column(Text)
    status = Column(String(20), nullable=False, default="pending")
    # PRD 9.2 扩展字段
    verdict_reasoning = Column(Text)
    response_draft = Column(Text)
    regulation_refs = Column(JSONB, default=list)
    confidence = Column(Float)
    internal_improvement = Column(Text)
    conflict_highlight = Column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("status IN ('pending','investigating','resolved','closed')"),
        CheckConstraint("verdict IN ('agent_violation','user_misunderstanding','compliant_but_poor_experience','pending')"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"),
    )
```

## 3. Alembic 迁移设计（0010_create_complaint_service_tables.py）

对齐仓库现有 `0009` 风格：对 `complaints` 扩展列使用 **`op.execute("ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...")`**，禁止仅用不可幂等的 `op.add_column`。

```python
def upgrade():
    # complaint_sessions
    op.create_table("complaint_sessions", ...)
    op.create_index("idx_complaint_sessions_session_id", ...)
    op.create_index("idx_complaint_sessions_complaint_id", ...)
    op.create_index("idx_complaint_sessions_task_status", ...)
    op.create_index("idx_complaint_sessions_trace_id", ...)
    op.create_index("idx_complaint_sessions_active", "complaint_sessions", ["task_status"],
                    postgresql_where="task_status NOT IN ('archived','closed') AND is_delete = '0'")
    # complaint_agent_logs
    op.create_table("complaint_agent_logs", ...)
    op.create_index(...)  # 4 个索引
    # complaints ALTER（IF NOT EXISTS，对齐 0009）
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS verdict_reasoning TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS response_draft TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS regulation_refs JSONB DEFAULT '[]'::jsonb;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS confidence FLOAT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS internal_improvement TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS conflict_highlight BOOLEAN DEFAULT FALSE;")
    # confidence CHECK（可空或 0~1）
    op.execute(
        "ALTER TABLE complaints DROP CONSTRAINT IF EXISTS complaints_confidence_check;"
    )
    op.execute(
        "ALTER TABLE complaints ADD CONSTRAINT complaints_confidence_check "
        "CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));"
    )
    # verdict CHECK 改为四值（先 DROP 旧约束，再 ADD 新约束）
    op.execute("ALTER TABLE complaints DROP CONSTRAINT IF EXISTS complaints_verdict_check;")
    op.execute(
        "ALTER TABLE complaints ADD CONSTRAINT complaints_verdict_check "
        "CHECK (verdict IN ('agent_violation','user_misunderstanding',"
        "'compliant_but_poor_experience','pending'));"
    )
```

**verdict CHECK 迁移注意**：PostgreSQL 自动生成的约束名可能是 `complaints_verdict_check`；一律用 `DROP CONSTRAINT IF EXISTS`，避免环境名不一致失败。

## 4. Redis Key 设计（app/utils/redis_keys.py）

```python
PREFIX = "csmp:"
DB = 6  # 投诉研判专用 DB（逻辑标注；实际选 DB 由 common Redis 客户端配置决定）

# TTL 常量（秒），对齐 PRD 9.3；永不过期用 None
TTL_SESSION = 24 * 3600
TTL_TICKET_PKG = 24 * 3600
TTL_VERDICT_LATEST = 24 * 3600
TTL_RSLV_LATEST = 24 * 3600
TTL_HOTSPOTS = 6 * 3600
TTL_GATHER_LOCK = 120
TTL_TASK_TIMEOUT = 30 * 60
TTL_LLM_DEGRADED = None   # 永不过期
TTL_MODE_ACTIVE = None    # 永不过期

def session_key(session_id: str) -> str: ...          # csmp:session:{session_id}
def ticket_pkg_key(ticket_id: str) -> str: ...         # csmp:ticket:{id}:pkg
def verdict_latest_key(complaint_id: str) -> str: ...  # csmp:verdict:{id}:latest
def rslv_latest_key(complaint_id: str) -> str: ...     # csmp:rslv:{id}:latest
def hotspots_key(start: str, end: str) -> str: ...     # csmp:hotspots:{start}:{end}
def gather_lock_key(complaint_id: str) -> str: ...     # csmp:gather:lock:{id}
LLM_DEGRADED_KEY = f"{PREFIX}llm_degraded"             # csmp:llm_degraded
def task_timeout_key(session_id: str) -> str: ...      # csmp:task:timeout:{session_id}
MODE_ACTIVE_KEY = f"{PREFIX}mode:active"               # csmp:mode:active
```

**复用 common Redis**：通过 `common.config.settings` 的 Redis 配置获取连接，不在本服务新建连接池。本批 `redis_keys.py` 只提供 Key/TTL 常量与构造函数；读写封装可后续批次补。

## 5. 证据工具函数设计（app/utils/evidence_helper.py）

### 5.1 _collect_all_evidence

```python
def _collect_all_evidence(state: dict) -> list[dict]:
    """展平 case_evidence + cross_case_evidence + historical_complaint_evidence。"""
    items = []
    # case_evidence: {case_id: CaseEvidenceBundle}
    for case_id, bundle in (state.get("case_evidence") or {}).items():
        for field in ["call_evidence", "history_tickets", "history_complaints", "qc_alerts"]:
            for ev in bundle.get(field, []):
                ev.setdefault("case_id", case_id)
                items.append(ev)
    # cross_case_evidence: list[EvidenceItem]
    items.extend(state.get("cross_case_evidence") or [])
    # historical_complaint_evidence: list[EvidenceItem]
    items.extend(state.get("historical_complaint_evidence") or [])
    return items
```

### 5.2 EvidenceItem.source → 优先级映射（对齐 PRD 6.1 + 7.3）

PRD `EvidenceItem.source` 取值含 `case` / `call` / `qc` / `rag` / `history_complaint` 等；step8 优先级用业务语义六档。实现时用下表（数字越小优先级越高）：

| 优先级 rank | 业务语义 | 典型 `source` 值（可扩展同义词） |
|------------|----------|----------------------------------|
| 0 | 结构化数据 | `case`、`structured`、`case_info` |
| 1 | 通话 ASR | `call`、`asr`、`call_asr` |
| 2 | 质检告警 | `qc`、`qc_alert` |
| 3 | 历史工单 | `history_ticket`、`ticket` |
| 4 | 历史投诉 | `history_complaint` |
| 5 | 用户自述 / 其它 | `user`、`user_statement`、`rag`、未知 source |

`sort_by_priority` 按 rank 升序稳定排序；同 rank 保持原相对顺序。

### 5.3 冲突 6 条规则（函数一一对应）

| # | 函数 | 说明 |
|---|------|------|
| 1 | `sort_by_priority(items)` | 按上表 rank 排序 |
| 2 | `filter_by_confidence(items, *, for_judgment=True)` | `for_judgment=True` 时剔除 `confidence_level=low`（low 仅呈现不参与判责）；展示链路可 `for_judgment=False` 保留全部 |
| 3 | `mark_medium_auxiliary(items)` | **独立函数**：给 `confidence_level=medium` 打 `auxiliary=True`，不作核心判责依据 |
| 4 | `detect_core_conflict(items)` | 金额/逾期天数等核心事实矛盾 → 相关 item `conflict_highlight=True`，返回冲突子集 |
| 5 | `apply_corruption_degradation(items)` | **对齐 PRD 7.3**：`is_corrupted=True` 默认降为 **medium**；若 `core_content_missing=True`（或 content 空/仅噪声占位）再降为 **low** |
| 6 | `mark_conflict_pending(items)` | 对已标记冲突的证据打 `pending_for_package=True`，不下放产出物生成 Agent |

**决策（相对 step8 字面「直接 low」）**：本批 **以 PRD 7.3 为准**——残缺先 medium，核心缺失才 low。step8 2.6 的「自动降级」解释为调用本函数，不解释为「一律 low」。

## 6. 复用 common 接口清单

| common 接口 | 用途 |
|-------------|------|
| `common.db.base.Base` | ORM 声明式基类 |
| `common.db.base.PublicFieldsMixin` | id/create_time/update_time/is_delete |
| `common.config.settings` | Redis 连接配置 |
| `common.db.session` | DB session（迁移不直接用，但 ORM import 时需 engine） |

## 7. 执行顺序与依赖

```
2.1 ComplaintSession ORM ─┐
                          ├─→ 2.4 Alembic 迁移（依赖 2.1+2.2+2.3 的字段定义）
2.2 ComplaintAgentLog ORM ┤
                          │
2.3 Complaint ORM 扩展 ───┘

2.5 Redis Keys ──────────────（独立，可并行）
2.6 证据工具函数 ────────────（独立，可并行）
```

- **2.1 + 2.2 先行**：ORM 模型定义是迁移脚本的依据
- **2.3 与 2.1/2.2 并行**：Complaint ORM 独立
- **2.4 依赖 2.1+2.2+2.3**：迁移脚本需对齐全部字段
- **2.5 / 2.6 完全独立**：可与 2.1~2.4 并行
