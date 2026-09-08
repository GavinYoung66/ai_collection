# Design: complaint-service-batch9

## Context

批次7（API+安全）、批次8（业务服务）已归档。批次9 交付灰度降级灾备（模块9）+ 事件联动（模块11）。

**关键现状**：
- `common.errors.codes`：A5013=样本回流，A5014=模式切换
- 批次7 mode 路由需委托 mode_service
- EventPublisher 已在 **`app/events/publisher.py`**（禁止另建 `app/service/event_publisher.py`）
- 单工单锁：`complaint_lock_key` + **`TTL_COMPLAINT_LOCK=1800`**

## Goals / Non-Goals

**Goals:** 灰度切换、11 降级可测、5 维限流、4 频道+扩展 topic 可断言、停止致电、灾备恢复

**Non-Goals:** 批次10 CI/压测、批次11 Docker、真 PG 主备、真对象存储、真 MQ、重写整图

## Decisions

### D1: 错误码——以 common 语义为准（纠正 step8 字面）

| 场景 | step8 字面 | **本批（common）** | 说明 |
|---|---|---|---|
| LLM 判责失败→RuleBased | A5003 | **A5004** | AI 判责失败 |
| 产出物失败→模板 | A5003 | **A5010** | 处置方案生成失败 |
| RAG 失败→规则兜底 | A5002 | **A5005** | 合规知识检索失败 |
| 业务工单 MCP 失败 | A5004 | **A5006** | DP/业务数据拉取失败 |
| 通话质检 MCP 失败 | A5004 | **A5008** | 质检记录拉取失败 |
| 数据中台回写失败 | A5005 | **A5013** | 样本回流失败 |
| MCP 权限拒绝 | A5006 | **无业务码** | 终止+审计；**不占用** A5006 |
| 反思超限 | A5007 | **A5004** | 归入 AI 判责失败族 |
| 步骤超限 | A5008 | **A5002** | 工单状态/流程非法 |
| Token 超限 | A5009 | **A5004** | 同 AI 判责失败族 |
| 全局超时 | A5010 | **A5010** | 一致 |
| 模式切换失败 | A5013 | **A5014** | 纠正与回流冲突 |

权威：`common.errors.codes`；禁止实现时再跟 step8 字面各写一套。

### D2: mode_service 为真相源

- PUT /complaints/mode → mode_service；双签（ops+tech）缺一拒绝
- 写 Redis：`csmp:mode:active` / `gray_percent` / `gray_sessions`
- 失败回滚 → **A5014**；成功 → `complaint.mode.switched`
- single_agent：单次 Mock LLM 全量包，不进 `build_complaint_graph`

### D3: 降级统一入口

- `degrade_llm` / `degrade_mcp` / `degrade_flow` 各 `handle*`
- 写 `exception_info`（含 code）
- 流程降级：`task_status=pending_review` + publish **`complaint.degraded`**（同一 EventPublisher）

### D4: 灾备恢复

- checkpointer 失败 → pending_review（零丢失）
- Redis fallback：`csmp:session:{id}:fallback` TTL **72h**，标记 model_degraded
- PG 不可用：新工单 **A5010**；已接入读 fallback

### D5: 限流 5 维

- `config/rate_limiter.yaml`（缺文件用默认值，不崩）
- Semaphore(20) + ZSET `csmp:queue:pending`（max 500，超时 10min）
- MCP/LLM 令牌桶按 YAML
- 单工单锁：`complaint_lock_key` + **TTL_COMPLAINT_LOCK**

### D6: EventPublisher——单真相源

- **只扩展** `app/events/publisher.py`
- PRD 4 频道：judged / closed / insight.weekly / mode.switched
- **扩展 topic（可断言，勿静默丢）**：`complaint.degraded`、`case.risk.upgraded`
- payload 强制 `trace_id`

### D7: 停止致电联动

- agent_violation → `stop_call_flag=True` + `case.risk.upgraded`
- user_misunderstanding / compliant_but_poor_experience → `stop_call_flag=False`

### D8: scheduler

- stub + 可调用 jobs：cleanup_archived_90d / cleanup_stale_24h / redis_rdb_snapshot / weekly_backup / quarterly_drill

### D9: Redis keys 增补（`redis_keys.py`）

| Helper / 常量 | Key | TTL |
|---|---|---|
| （已有）`complaint_lock_key` | `csmp:complaint:lock:{id}` | **TTL_COMPLAINT_LOCK=1800** |
| `mode_gray_sessions_key` | `csmp:mode:gray_sessions` | 永不过期或随模式 |
| `session_fallback_key(id)` | `csmp:session:{id}:fallback` | **TTL_SESSION_FALLBACK=259200**（72h） |
| `queue_pending_key` | `csmp:queue:pending` | —（ZSET） |
| （已有）mode active/gray | `csmp:mode:active` 等 | — |

### D10: FakeRedis / Mock 边界

| 依赖 | 策略 |
|---|---|
| Redis | 可注入 FakeRedis；模式/限流/fallback/队列 |
| LLM / MCP | Mock；single_agent / 降级可测 |
| Pub-Sub | EventPublisher 内存 + 可选 mock redis.publish；禁真 MQ |
| 时钟 | MockClock（超时/排队） |
| 单测 | 禁止真网 |

## Risks / Trade-offs

- **[step8 字面码]** → 已用 D1 对照表纠正，归档说明即可
- **[single_agent]** → 单次 LLM，不重写图
- **[任务零丢失]** → 降级/恢复必转 pending_review 或继续

## Migration Plan

1. redis_keys 增补 + 9.1 mode + 路由委托
2. 9.5 rate_limiter + YAML（与 9.1 并行）
3. 9.2a/b/c 降级
4. 9.3 recovery + 9.4 scheduler
5. **扩展** `app/events/publisher.py` + 11.2 停止致电
6. `tests/unit/test_batch9.py`
