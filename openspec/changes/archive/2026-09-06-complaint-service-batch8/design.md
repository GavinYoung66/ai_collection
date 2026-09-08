# Design: complaint-service-batch8

## Context

批次6（graph/State）+ 批次7（API 12 路由 + 安全护栏）已归档。`app/service/complaint_service.py` 当前为内存 dict stub。批次8 交付业务服务层（8.1～8.6），将内存 stub 收敛为真实状态机驱动服务，路由层接口不变，内部委托各专业 service。

**关键现状**：`common.monitor.metrics` 尚未实现 → 8.6 自建薄采集器，接口对齐，后续可替换。

## Goals / Non-Goals

**Goals:**
- 工单接收→session→Agent→复核→回调 全流程 Mock 跑通
- 10 状态流转 + 30min 超时 + 每日对账
- 6 复核动作 + reject_count≤2 + SLA 3 级 + 权限 4 级 + ai_human_diff
- 统计 7 指标 + by_verdict 四值 + 一致率告警 + 热点缓存
- 回调幂等/3 次重试/DLQ；回流零丢失暂存；指标 3 类 + 告警阈值
- 批次7 API 单真相源接线（门面 + stats 路由）

**Non-Goals:**
- 批次9 灰度矩阵/限流全量/真 Pub-Sub
- 批次10 压测 CI
- 真实外部业务系统（回调/回流 Mock HTTP）
- `common.monitor.metrics` 真实实现
- 新增 A5015+ 错误码

## Decisions

### D1: 收敛 complaint_service + API 接线（禁止平行真相源）

- `complaint_service.py`：**薄门面**，持有/委托 `session_service` / `review_service` /（可选）callback·backflow；路由层签名不变
- `app/api/routes/stats.py`：改为调用 `stats_service`，禁止路由内自算第二套统计
- 真相源：session_service 状态机（本批内存 store 模拟 PG，接口对齐 ORM）
- 批次7 stub 逻辑迁入 session_service，**不保留两套 store**
- **初始状态**：创建工单 → `TaskStatus.pending`；`POST .../judge` 再转入 `evidence_gathering`（或后续研判态）。相对批次7 stub 曾用 `evidence_gathering`，相关单测需同步

### D2: 幂等、锁 TTL 与状态机（8.1）— P0

**幂等语义（两层，缺一不可）：**
1. **持久映射** `complaint_id → session_id`（store/ORM）：重复接入直接返回已有 session
2. **SETNX** `csmp:complaint:lock:{complaint_id}`：仅防创建竞态；**TTL=1800s（30min）**，对齐 step8 9.5 同 key，**禁止 30s**

- 10 状态枚举复用 `app.models.state.TaskStatus`
- 超时：Mock 时钟可注入；扫描 `judging` / `draft_generated` 超 30min → `pending_review`
- `redis_keys.py` 增补 `complaint_lock_key` + `TTL_COMPLAINT_LOCK = 1800`

### D3: 复核 6 动作与 reject_count（8.2a）

| 动作 | 目标状态 | reject_count |
|---|---|---|
| approve | resolved | 不变 |
| modify_verdict | judging | 不变 |
| modify_response | judging | 不变 |
| reject_rejudge | evidence_gathering | +1（已达 2 后再 reject → 强制 escalate） |
| add_evidence | evidence_gathering | 不变 |
| escalate | pending_senior_review | 不变 |

- 驳回原因：insufficient_evidence / wrong_verdict / bad_response / other
- 写 agent_logs（`prepare_agent_log` 脱敏）；联动 human_review_node（graph resume **或 stub**）

### D4: SLA 与权限（8.2b）

- SLA：普通 24h → 紧急 4h → 高风险 2h
- 权限：level1 / level2 / level3 / readonly（readonly 禁提交；level3 可覆盖 level2）
- ai_human_diff：verdict 一致率 + response_diff 行数
- 明文 PII：复用 `audit_service.access_plaintext`

### D5: 统计与热点缓存（8.3）

- 7 指标；by_verdict 四值；一致率两周 <80% → metrics 告警通道
- 缓存：`csmp:hotspots:{start}:{end}` TTL 6h（已有 `hotspots_key`）

### D6: 回调（8.4）

- 幂等键：session_id + version
- 重试 3 次：10s / 30s / 60s（Mock 时钟）
- DLQ：`csmp:callback:dlq`；`redis_keys.callback_dlq_key`
- 对账：共用 `daily_reconcile()`

### D7: 样本回流（8.5）

- write_session + write_human_review_truth（Mock MCP）
- 失败 3 次 → 告警（**A5013**）+ 暂存 `csmp:backflow:retry`；**不阻塞**主流程
- AI≠人工 → high_value

### D8: 指标采集（8.6）

- 自建 `MetricsCollector`；60s；`MockClock`
- 告警：一致率两周 <80%；MCP>5%；LLM 降级>10%；队列积压>500

### D9: 共享定时入口

- `app/service/reconcile.py::daily_reconcile()`：超时扫描（8.1）+ draft_generated 超 1h 对账补偿（8.4）
- **禁止** 8.1 / 8.4 各造一套 scheduler

### D10: 错误码映射（不新增码）

| 场景 | 错误码 |
|---|---|
| 工单/session 不存在 | A5001 |
| 状态不允许此操作 / 非法流转 | A5002 |
| 参数错误（缺驳回原因、verdict=normal 等） | **A5002**（或 fail 消息校验错误；**不新增 A5015**） |
| 样本回流失败（告警/入 retry 关联） | **A5013** |
| 模式切换（批次7已有，本批不改） | A5014 |

回调入 DLQ / 回流暂存：**主流程不抛致命中断**；以告警 + 队列为准。

### D11: FakeRedis / Mock 边界

| 依赖 | 本批策略 |
|---|---|
| Redis | 可注入客户端（内存 FakeRedis）；keys 一律走 `redis_keys` |
| 回调 HTTP | 可注入 `http_client`；单测禁止真网 |
| 回流 MCP | 可注入 `mcp_client`；失败路径用 AlwaysFail stub |
| PG | 内存 store 模拟；接口对齐 model，不接真 PG |
| 时钟 | 超时/SLA/退避/采集均支持 MockClock |

## Risks / Trade-offs

- **[complaint_service 收敛]** → 路由不变；批次7 单测需适配初始 `pending`
- **[common.monitor 缺失]** → 自建薄采集器 + TODO
- **[锁 TTL 30min]** → 与 step8 9.5 同 key；幂等靠持久映射，不靠锁 alone
- **[Mock 时钟]** → 测试可快进

## Migration Plan

1. redis_keys 增补 + 8.1 session + reconcile + 收敛 complaint_service
2. 8.2a + 8.2b
3. 8.3 stats_service + 接线 stats 路由
4. 8.4 + 8.5 并行
5. 8.6 metrics
6. `tests/unit/test_batch8_services.py` + 全流程 Mock
