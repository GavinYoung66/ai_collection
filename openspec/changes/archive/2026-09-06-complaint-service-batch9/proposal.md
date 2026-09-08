# Proposal: complaint-service-batch9

## Why

批次8 已交付业务服务层（session/review/callback/backflow/metrics）。批次9 交付**灰度降级灾备（模块9）+ 事件联动（模块11）**，为系统提供生产级可靠性：双模灰度切换、11 个降级场景兜底、灾备恢复、5 维限流、4 频道 Pub-Sub、停止致电联动。对齐 PRD REQ-CJ-024/031/046/047/052/054，满足 SLA≥99.5%、MTTR≤15min、任务零丢失。

## What Changes

### 模块9 灰度降级灾备

**9.1 mode_service**（`app/service/mode_service.py`）
- single_agent / multi_agent 双模；gray_percent（0~100）、gray_sessions 列表
- 一致性达标（AI-人工≥85%、互一致≥80%）后自动全量切换
- 切换失败回滚 **A5014**；灰度切换需双签（运营+技术负责人）
- single_agent：跳过多 Agent 图，单次 LLM 出全量包（Mock Provider 可测，不重写整图）
- **接线**：`PUT /complaints/mode` 委托 mode_service（批次7 路由薄、service 真）

**9.2a～9.2c 降级**（11 场景；错误码以 `common.errors.codes` 语义为准，纠正 step8 字面——见 design D1）
- degrade_llm：A5004 / A5010 / A5005
- degrade_mcp：A5006 / A5008 / A5013；权限拒绝→终止+审计（**不占用** A5006）
- degrade_flow：A5004 / A5002 / A5004 / A5010 + 发布 `complaint.degraded`

**9.3 recovery_service** / **9.4 scheduler** / **9.5 rate_limiter**
- fallback `csmp:session:{id}:fallback` TTL 72h；积压清理；PG 不可用 A5010
- apscheduler stub + 可调用 job
- 5 维限流 + `config/rate_limiter.yaml`；单工单锁复用 `TTL_COMPLAINT_LOCK=1800`

### 模块11 事件联动

**11.1** **扩展**既有 `app/events/publisher.py`（**禁止**新建 `app/service/event_publisher.py`）
- 4 频道：complaint.judged / closed / insight.weekly / mode.switched
- 扩展 topic：`complaint.degraded`、`case.risk.upgraded`（同一 EventPublisher，可断言）
- Payload 含 trace_id；Mock publish

**11.2** 停止致电联动（`review_service` / `complaint_service`）
- agent_violation → stop_call_flag + case.risk.upgraded
- 误解/体验差 → 恢复可外呼

## Capabilities

### New Capabilities
- `complaint-service/mode`
- `complaint-service/degrade`
- `complaint-service/recovery`
- `complaint-service/rate-limit`
- `complaint-service/events`

## Impact

- **新增**：mode_service / degrade_* / recovery_service / scheduler / rate_limiter + `config/rate_limiter.yaml` + `tests/unit/test_batch9.py`
- **修改**：`app/api/routes/mode.py`；`app/events/publisher.py`（扩展频道）；`review_service` / `complaint_service`（停止致电）；`redis_keys.py`（gray_sessions / fallback / queue）
- **禁止**：新建第二套 EventPublisher
- **依赖**：批次7（A5014、mode 路由）；批次8（session/review/lock/stop_call）
- **路径**：一律 `services/complaint-service/`

## Non-Goals

- 批次10 CI/压测/E2E 全量
- 批次11 Docker
- 真 PG 主备切换实操
- 真对象存储备份上传（9.4 stub）
- 真 MQ 集群（Mock publish）
- 重写 LangGraph 整图（single_agent 单次 LLM）

## 错误码约定

- 模式切换失败 = **A5014**（纠正 step8 字面 A5013）
- A5013 = 判责样本回流失败
- 降级 11 场景码见 design D1（以 common 语义为准）
