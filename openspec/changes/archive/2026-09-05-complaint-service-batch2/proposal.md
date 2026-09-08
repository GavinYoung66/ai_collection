# Proposal: complaint-service-batch2

## 为什么（Why）

M3 投诉研判系统的后续批次（MCP/LLM/LangGraph/API/业务服务）都需要数据层支撑：
- `complaint_sessions` 存 LangGraph 全量编排数据（State 快照）
- `complaint_agent_logs` 存每步 Agent 中间输出（审计追溯）
- `complaints` 表扩展判责相关字段（verdict_reasoning/response_draft/regulation_refs 等）
- Redis DB6 缓存 State/取证包/判责/热点/锁/模式等 9 类 Key
- 证据工具函数为 LangGraph 节点提供证据展平与冲突解决能力

批次1 脚手架已就绪，但数据层缺失会阻塞批次3（MCP/LLM）和批次5（LangGraph）。

## 交付什么（What）

在 `services/complaint-service/` 下交付 6 类产物（2.1~2.2 先行，2.3~2.6 可并行）：

| ID | 产物 | 说明 |
|----|------|------|
| 2.1 | `app/models/complaint_session.py` | ComplaintSession ORM，对齐 PRD 9.1 DDL + 6.1 State |
| 2.2 | `app/models/complaint_agent_log.py` | ComplaintAgentLog ORM，append-only |
| 2.3 | `app/models/complaint.py` | Complaint ORM 映射已有表 + 扩展字段（见设计决策） |
| 2.4 | `migrations/versions/0010_create_complaint_service_tables.py` | Alembic 迁移（CREATE 两表 + complaints ALTER + 索引） |
| 2.5 | `app/utils/redis_keys.py` | 9 个 Redis Key 模式（DB6，csmp: 前缀） |
| 2.6 | `app/utils/evidence_helper.py` | `_collect_all_evidence()` + 冲突 6 条规则辅助函数 |

## 完成标准

- PG：complaint_sessions + complaint_agent_logs 建表成功；complaints ALTER 扩展字段成功
- Redis：9 个 csmp: Key 模式可构造/读写（DB6）
- ComplaintSession ORM 可 import + 字段对齐 PRD 9.1
- 证据工具 `_collect_all_evidence` + 冲突 6 条规则辅助函数可单测（`tests/unit/test_evidence_helper.py`）

## 非目标（What NOT）

- 不实现 MCP Client（**批次3 / 模块3**）
- 不实现 LLM Provider（**批次3 / 模块5**；勿与模块编号「5」混淆成批次5）
- 不实现 LangGraph 节点/边/State Schema（**批次5～6 / 模块4**）
- 不实现业务 API（**批次7 / 模块7**）
- 不改 `common/`（Base/PublicFieldsMixin/Redis 连接/settings 直接复用）
- 不新增第二套 complaints 表模型（只映射已有表，扩展字段走迁移）

## 路径硬约束

- 业务代码：`services/complaint-service/` 下
- 迁移脚本：仓库根 `migrations/versions/0010_*.py`（挂全局 Alembic，不另起）
- 禁止写到仓库根 `app/`、`tests/`
- 禁止改 `openspec/changes/archive/**`
