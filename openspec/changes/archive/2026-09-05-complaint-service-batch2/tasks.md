# Tasks: complaint-service-batch2

## 执行顺序

**2.1 + 2.2 先行 → 2.3 可并行 → 2.4 依赖 2.1+2.2+2.3 → 2.5 / 2.6 独立可并行**

验证命令中的 `PYTHONPATH` 一律用相对路径：`PYTHONPATH=../..:$PYTHONPATH`（在 `services/complaint-service/` 下执行时指向仓库根）。

---

## 2.1 ComplaintSession ORM

- [x] 创建 `app/models/complaint_session.py`
  - 继承 `common.db.base.Base` + `PublicFieldsMixin`
  - 字段对齐 PRD 9.1 DDL（session_id/trace_id/complaint_id/case_ids/session_ids/user_id/complaint_text/loop_count/max_loops/case_evidence/cross_case_evidence/historical_complaint_evidence/reflection_report/fact_timeline/compliance_opinion/user_demand/verdict_draft/judgment_package/exception_info/human_review/task_status/total_tokens_consumed/max_token_threshold/mode）
  - **本表无独立 `verdict` 列**（四值约束在 Complaint）
  - `task_status` CHECK 含 10 值：pending/evidence_gathering/reflection_retry/judging/draft_generated/pending_review/pending_senior_review/resolved/closed/archived
  - `loop_count` CHECK 0~3
  - `mode` CHECK single_agent/multi_agent
  - JSONB 字段默认值对齐 DDL（case_ids/session_ids 默认 `[]`，exception_info 默认 `{}`）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.models.complaint_session import ComplaintSession
  cols = {c.name for c in ComplaintSession.__table__.columns}
  required = {'session_id','trace_id','complaint_id','case_ids','session_ids','user_id','complaint_text','loop_count','max_loops','case_evidence','cross_case_evidence','historical_complaint_evidence','reflection_report','fact_timeline','compliance_opinion','user_demand','verdict_draft','judgment_package','exception_info','human_review','task_status','total_tokens_consumed','max_token_threshold','mode','id','create_time','update_time','is_delete'}
  missing = required - cols
  assert not missing, f'missing: {missing}'
  assert 'verdict' not in cols
  assert ComplaintSession.__tablename__ == 'complaint_sessions'
  print('2.1 ComplaintSession OK, cols=', len(cols))
  "
  ```

## 2.2 ComplaintAgentLog ORM

- [x] 创建 `app/models/complaint_agent_log.py`
  - 继承 `Base`（append-only，**不**继承 PublicFieldsMixin 的 update_time/is_delete；仅声明 id + create_time）
  - 字段：session_id/trace_id/agent_role/step_name/input_summary/output_json/mcp_calls/llm_call_log_ids/tokens_consumed/latency_ms/status/error_msg/create_time
  - `agent_role` CHECK 含 10 值
  - `status` CHECK 含 success/failed/degraded/skipped
  - 无 `is_delete`、无 `update_time`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.models.complaint_agent_log import ComplaintAgentLog
  cols = {c.name for c in ComplaintAgentLog.__table__.columns}
  assert 'is_delete' not in cols, 'append-only 不应有 is_delete'
  assert 'update_time' not in cols, 'append-only 不应有 update_time'
  assert ComplaintAgentLog.__tablename__ == 'complaint_agent_logs'
  print('2.2 ComplaintAgentLog OK (append-only)')
  "
  ```

## 2.3 Complaint ORM 映射与扩展

- [x] 创建 `app/models/complaint.py`
  - 继承 `Base` + `PublicFieldsMixin`，`__tablename__ = "complaints"`
  - 已有字段：case_id/session_id/complaint_text/verdict/evidence/suggestion/status
  - 扩展字段（PRD 9.2）：verdict_reasoning/response_draft/regulation_refs(JSONB)/confidence(FLOAT)/internal_improvement/conflict_highlight(BOOL)
  - `verdict` CHECK 四值：agent_violation/user_misunderstanding/compliant_but_poor_experience/pending（**不含 normal**）
  - `confidence` CHECK 0~1
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.models.complaint import Complaint
  cols = {c.name for c in Complaint.__table__.columns}
  for f in ['verdict_reasoning','response_draft','regulation_refs','confidence','internal_improvement','conflict_highlight']:
      assert f in cols, f'missing {f}'
  assert Complaint.__tablename__ == 'complaints'
  print('2.3 Complaint OK, extended cols present')
  "
  ```

## 2.4 Alembic 迁移脚本

- [x] 创建 `migrations/versions/0010_create_complaint_service_tables.py`
  - `revision = "0010"`，`down_revision = "0009"`
  - `upgrade()`：CREATE complaint_sessions + complaint_agent_logs；**complaints 扩展列用 `op.execute(... ADD COLUMN IF NOT EXISTS ...)`（对齐 0009）**；修改 verdict CHECK 为四值（`DROP CONSTRAINT IF EXISTS` + ADD）；创建索引（session_id/complaint_id/task_status/trace_id/active 部分索引 + agent_logs 的 4 索引）
  - `downgrade()`：DROP 两表 + 移除扩展字段（可选）
- [x] 验证（在仓库根执行；迁移语法检查，不连真实 PG）：

  ```bash
  # 在仓库根 ai_collection/ 下
  python3 -c "
  import importlib.util
  from pathlib import Path
  path = Path('migrations/versions/0010_create_complaint_service_tables.py')
  text = path.read_text()
  assert 'ADD COLUMN IF NOT EXISTS' in text
  assert 'DROP CONSTRAINT IF EXISTS' in text
  spec = importlib.util.spec_from_file_location('m', path)
  m = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(m)
  assert m.revision == '0010'
  assert m.down_revision == '0009'
  assert hasattr(m, 'upgrade') and hasattr(m, 'downgrade')
  print('2.4 migration script OK: revision=0010, IF NOT EXISTS present')
  "
  ```

  真实 PG 升级验证（需 PG 可用，开发环境）：
  ```bash
  alembic upgrade head  # complaint_sessions / complaint_agent_logs 建表 + complaints ALTER
  alembic downgrade -1  # 回滚
  ```

## 2.5 Redis Key 模式

- [x] 创建 `app/utils/redis_keys.py`
  - 9 个 Key 构造函数/常量，前缀 `csmp:`，DB=6 逻辑标注
  - TTL 常量：`TTL_SESSION/TICKET_PKG/VERDICT_LATEST/RSLV_LATEST=86400`，`TTL_HOTSPOTS=21600`，`TTL_GATHER_LOCK=120`，`TTL_TASK_TIMEOUT=1800`，`TTL_LLM_DEGRADED/TTL_MODE_ACTIVE=None`
  - 复用 common Redis 连接约定（不新建连接池）
  - Key 清单：session / ticket:pkg / verdict:latest / rslv:latest / hotspots / gather:lock / llm_degraded / task:timeout / mode:active
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.utils import redis_keys as rk
  assert rk.session_key('s1') == 'csmp:session:s1'
  assert rk.ticket_pkg_key('t1') == 'csmp:ticket:t1:pkg'
  assert rk.verdict_latest_key('c1') == 'csmp:verdict:c1:latest'
  assert rk.rslv_latest_key('c1') == 'csmp:rslv:c1:latest'
  assert rk.hotspots_key('20260101','20260131') == 'csmp:hotspots:20260101:20260131'
  assert rk.gather_lock_key('c1') == 'csmp:gather:lock:c1'
  assert rk.LLM_DEGRADED_KEY == 'csmp:llm_degraded'
  assert rk.task_timeout_key('s1') == 'csmp:task:timeout:s1'
  assert rk.MODE_ACTIVE_KEY == 'csmp:mode:active'
  assert rk.TTL_SESSION == 86400 and rk.TTL_HOTSPOTS == 21600
  assert rk.TTL_GATHER_LOCK == 120 and rk.TTL_TASK_TIMEOUT == 1800
  assert rk.TTL_LLM_DEGRADED is None and rk.TTL_MODE_ACTIVE is None
  print('2.5 redis_keys OK: 9 keys + TTL constants')
  "
  ```

## 2.6 证据工具函数

- [x] 创建 `app/utils/evidence_helper.py`
  - `_collect_all_evidence(state) -> list[dict]`：展平 case_evidence + cross_case_evidence + historical_complaint_evidence
  - 6 条规则函数（独立、可单测）：
    1. `sort_by_priority`（source→rank 见 design）
    2. `filter_by_confidence`（判责路径剔除 low）
    3. `mark_medium_auxiliary`（medium → `auxiliary=True`）
    4. `detect_core_conflict`
    5. `apply_corruption_degradation`（**PRD 7.3**：corrupted → medium；core_content_missing/空 content → low）
    6. `mark_conflict_pending`
- [x] 创建 `tests/unit/test_evidence_helper.py`，每条规则至少 1 个用例（含 medium 辅助 + corrupted→medium + core_missing→low）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_evidence_helper.py -v
  ```

  快速断言：
  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.utils.evidence_helper import (
      _collect_all_evidence, sort_by_priority, filter_by_confidence,
      mark_medium_auxiliary, detect_core_conflict,
      apply_corruption_degradation, mark_conflict_pending,
  )
  state = {'case_evidence': {'c1': {'call_evidence': [{'evidence_id':'e1','source':'call','content':'a','confidence':1.0,'confidence_level':'high','is_corrupted':False}]}}, 'cross_case_evidence': [], 'historical_complaint_evidence': []}
  items = _collect_all_evidence(state)
  assert len(items) == 1 and items[0]['case_id'] == 'c1'
  degraded = apply_corruption_degradation([{'evidence_id':'e2','source':'call','content':'x','is_corrupted':True,'confidence_level':'high'}])
  assert degraded[0]['confidence_level'] == 'medium'
  degraded2 = apply_corruption_degradation([{'evidence_id':'e3','source':'call','content':'','is_corrupted':True,'core_content_missing':True,'confidence_level':'high'}])
  assert degraded2[0]['confidence_level'] == 'low'
  print('2.6 evidence_helper OK')
  "
  ```

---

## 全部完成标准

- [x] complaint_sessions + complaint_agent_logs 表可通过 Alembic 迁移创建（或迁移脚本语法 + 字段对齐验证通过）
- [x] complaints 表 6 个扩展字段 ALTER 使用 `ADD COLUMN IF NOT EXISTS`
- [x] verdict CHECK 枚举为四值（不含 normal）
- [x] ComplaintSession / ComplaintAgentLog / Complaint ORM 均可 import 且字段对齐；ComplaintSession **无** verdict 列
- [x] 9 个 csmp: Redis Key 模式构造正确，TTL 常量对齐 PRD 9.3
- [x] `tests/unit/test_evidence_helper.py` 全部用例通过（6 条规则各≥1 用例；ASR 降级跟 PRD 7.3）
- [x] 未实现 MCP/LLM/LangGraph/业务 API（仅数据层）
- [x] 未改 common/
