# Proposal: complaint-service-batch8

## Why

批次7 已交付 API 接口层（12 路由）+ 安全护栏，且 `complaint_service` 为内存 stub。批次8 交付业务服务层（模块8，8.1～8.6），替换/收敛内存 stub 为真实状态机驱动的服务，打通「工单接收→session 创建→Agent 执行→人工复核→回调」全流程，并补齐统计/回流/监控能力，为批次9 灰度降级与批次10 压测奠定业务基础。

## What Changes

**0. Redis keys 增补**（`app/utils/redis_keys.py`）
- 增补 `complaint_lock_key`（TTL **1800s / 30min**）、`callback_dlq_key`、`backflow_retry_key`；复用已有 `hotspots_key`（TTL 6h）
- PREFIX=`csmp:`，DB=6

**8.1 session_service**（`app/service/session_service.py`）
- 幂等真相：`complaint_id → session_id` **持久映射**（内存 store / 对齐 ORM）
- 创建竞态：`SETNX csmp:complaint:lock:{id}`，**TTL=1800s（30min）**（对齐 step8 9.5 同 key）
- 创建初始状态 **`pending`**；`judge` 再流转（如 evidence_gathering）
- 10 状态流转；全局任务超时 30min → pending_review
- 每日对账与 8.4 共用 `reconcile.py::daily_reconcile()`

**8.2a review_service** + **8.2b review_sla_service**
- 6 动作、reject_count≤2、驳回原因枚举、状态映射、agent_logs、human_review stub resume
- SLA 3 级、权限 4 级、ai_human_diff、明文双人授权（复用 audit_service）
- verdict 四值无 normal

**8.3 stats_service**
- 7 指标 + by_verdict 四值；一致率两周 <80% 告警；热点缓存 TTL 6h
- **接线**：`app/api/routes/stats.py` 改调 stats_service（禁止路由自算第二套）

**8.4 callback_service** / **8.5 backflow_service** / **8.6 metrics_service**
- 回调：session_id+version 幂等；3 次退避 10/30/60s；DLQ；对账共用 reconcile
- 回流：write_session + write_human_review_truth；失败暂存 `csmp:backflow:retry`；告警码 **A5013**
- 指标：自建薄采集器（common.monitor 未就绪）；MockClock；三类指标 + 告警阈值

**API 接线（批次7）**
- `complaint_service.py` → **薄门面**，委托 session / review / callback 等；路由签名不变
- `stats.py` → stats_service；创建工单初始 `pending`（相对批次7 stub 的 evidence_gathering，单测需同步）

## Capabilities

### New Capabilities
- `complaint-service/session-service`
- `complaint-service/review-service`
- `complaint-service/stats-service`
- `complaint-service/callback-service`
- `complaint-service/backflow-service`
- `complaint-service/metrics-service`

## Impact

- **新增**：6 个 service + `reconcile.py` + `tests/unit/test_batch8_services.py`
- **修改**：`complaint_service.py`（门面）；`stats.py`（接 stats_service）；`redis_keys.py`（本批 key）
- **依赖**：批次6 graph/State；批次7 API/安全；`common.errors.codes` A5001～A5014（**不新增**错误码）
- **路径**：一律 `services/complaint-service/`
- **Mock**：可注入 FakeRedis / Mock HTTP / Mock MCP；单测禁止真网

## Non-Goals

- 批次9 灰度降级矩阵 / 限流全量 / 真 Pub-Sub 全频道
- 批次10 压测 CI
- 真实外部业务系统对接（回调/回流用 Mock）
- `common.monitor.metrics` 真实实现（本批自建薄采集器，接口对齐）
- 不新增 A5015+ 错误码
