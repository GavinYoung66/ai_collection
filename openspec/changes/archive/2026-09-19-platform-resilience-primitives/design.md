## Context

三服务（complaint-service / qc-service / rag-service）的韧性能力现状分散且口径不一：complaint 有完整私有降级三件套 + 5 维限流；rag 有独立固定窗口限流；qc 几乎空白。`common/llm/circuit_breaker.py` 是单个全局 pybreaker 实例（`_llm_breaker`），一个 key 干掉全平台 LLM。MCP 调用无熔断、无错误分级。无幂等键、无统一钩子。现有可复用基础：`common/llm/reliability.py`（已有 `RedisDegradeTracker` + `call_with_retry` + placeholder-secret 守门）、`common/utils/semaphore.py`（阻塞式 asyncio.Semaphore 封装，外呼用）、`common/utils/retry.py`、`common/errors/codes.py`（90 条错误码全集）。

约束（来自 project_memory + 既有 spec）：
- 零件 vs 策略分离：common 只放原子零件，领域降级策略不下沉
- 熔断 key 必须带服务前缀，禁止全局熔断
- API 统一响应 `{code,message,data,trace_id}`
- PII 脱敏、CoT 加密、LangSmith/Langfuse 单开关等硬约束不变

## Goals / Non-Goals

**Goals:**
- 7 个原子零件 + LLM/MCP 两个 reliability 编排入口可独立 import、独立单测
- 熔断 key 强制 `{svc}:llm_degraded:{model}` / `{svc}:mcp_degraded:{server}`，消灭全局单 key
- rag-service 限流迁到 common 零件，行为零变化
- 超时层级 MCP(30s) < LLM(120s) < 投诉全局(30min)

**Non-Goals（设计层补充，不重复 proposal 的 Non-Goals）:**
- 不替换 `common/utils/semaphore.py` 现有外呼用阻塞信号量（外呼语义需要等待，新 concurrency 是非阻塞语义，两者共存）
- 不删除 `common/llm/circuit_breaker.py` 的 pybreaker 依赖（P0 让 provider 不再单实例装饰，P1 视迁移情况清理）
- 不在本变更定义 `invoke_llm` 的 prompt/schema 路由（那是服务内 provider 职责）

## Decisions

### Decision 1: common/resilience/ 是 Python 库不是微服务
**选择**：零件放在 `common/resilience/` 作为可 import 的 Python 模块，不做成第 8 个 resilience-service。
**理由**：三服务同进程 import 即可复用，无需引入网络跳数、序列化、健康检查。零件是原子能力，组合权在服务。
**备选**：独立 resilience-sidecar。否决——多一次网络 RTT、多一个故障域、违背"零件"定位。

### Decision 2: 限流用固定窗口（Redis INCR+EXPIRE），不用滑动窗口
**选择**：P0 固定窗口，与 rag-service 现有限流口径一致，迁移零行为变化。
**理由**：滑动窗口（ZSET/计数桶）复杂度高，P0 目标是统一口径而非提升精度；rag 现有 20/60/200 固定窗口已在生产。
**备选**：滑动窗口。否决——引入精度变化会导致迁移后行为不"零变化"，违背验收标准 3。

### Decision 3: rate_limit Redis 挂放行，idempotency Redis 挂拒绝
**选择**：限流降级放行（可用性优先），幂等键降级拒绝（正确性优先）。
**理由**：限流本质是保护，挂了放行最坏是多放几请求（下游还有熔断兜底）；幂等键挂了放行会导致重复写入（资金/工单类不可逆）。两种降级方向相反，按业务后果定向选择，不强求统一。
**备选**：统一放行/统一拒绝。否决——一刀切要么牺牲正确性要么牺牲可用性。

### Decision 4: concurrency 用非阻塞 acquire（满返回 False），不复用现有阻塞 semaphore
**选择**：新 `common/resilience/concurrency.py` 的 `acquire()` 满时立即返回 False，不等待。
**理由**：Agent 编排场景需要"满即拒绝→走降级"，而不是"满即排队→堆积"。现有 `common/utils/semaphore.py` 是阻塞等待语义（外呼场景需要），两者语义不同，共存不替换。
**备选**：复用 `utils/semaphore.py` 加 try_acquire。否决——会让外呼阻塞语义和编排非阻塞语义混在一个类，调用方易误用。

### Decision 5: 熔断状态机自实现，不用 pybreaker
**选择**：`common/resilience/breaker.py` 自实现 closed→open→half-open→closed/open 状态机，不依赖 pybreaker。
**理由**：pybreaker 是进程内单实例（现有 `_llm_breaker` 全局问题根因），新零件需要按 key 实例化、half-open 只放 1 探针、可注入 Redis 多实例权威。自实现 60 行可控。
**备选**：继续用 pybreaker per-key 实例。否决——pybreaker 的 half-open 并发控制语义不透明，且引入额外依赖。

### Decision 6: MCP 错误分级——超时/权限拒绝不重试，业务错重试 3 次
**选择**：`call_tool` 对错误三级：超时→不重试+标 degraded；权限拒绝→不重试+终止；业务可重试→重试 3 次。
**理由**：超时重试会放大延迟（下游可能已经慢，重试更慢）；权限拒绝重试无意义（不会自愈）；只有瞬时业务错（如 500）重试有价值。与 LLM `invoke_llm` 的"超时不重试"对齐。
**备选**：统一重试 3 次。否决——超时重试放大延迟，违背 timeout 层级设计。

### Decision 7: hooks 默认 structlog，不依赖 publisher
**选择**：`on_event` 默认打 structlog，服务可注入自己的 publisher，但 publisher 不存在时必须正常工作。
**理由**：P0 不上 Prometheus/Grafana，但要给 P1 留统一计数点。默认 no-op/log 保证零件可独立单测、不耦合外部基础设施。
**备选**：直接接 Langfuse。否决——违背"外部 tracing 单开关"约束，且耦合太紧。

## Risks / Trade-offs

- **[Risk] 全局 pybreaker 迁移遗漏** → `common/llm/circuit_breaker.py` 的 `_llm_breaker` 被 `provider.py` 装饰使用；P0 让 `invoke_llm` 用新 per-key breaker，但 `provider.chat` 旧路径若不切，全局 breaker 仍在。Mitigation：tasks 中明确 `provider.py` 切换为调 `invoke_llm` 或至少 per-key breaker，并在验收中验证"投诉 rag_kb 熔断不影响 QC 的 LLM"。
- **[Risk] 固定窗口边界突刺** → 固定窗口在窗口切换瞬间可能放 2 倍流量。Mitigation：P0 接受（与现网一致），P1 可换滑动窗口（零件接口 `allow(key,limit,window)` 不变）。
- **[Risk] Redis 挂导致限流全放行** → 流量洪峰。Mitigation：限流降级时 hooks 发 `*.rate_limit.degraded` 事件，服务可据此事后告警；下游有熔断兜底。
- **[Risk] 错误码冲突（已解决）** → 原提的 A5009/A5010/A5011 在 `common/errors/codes.py` 已被占用（证据片段截取失败/处置方案生成失败/投诉沉淀统计失败）。用户已拍板续用 A5 域末尾 A5015/A5016/A5017（Token 超限/任务超时/LLM 熔断），不破坏既有前端契约。

## Migration Plan

1. 新建 `common/resilience/` 7 零件 + 单测（不影响任何线上路径）
2. 补全 `common/llm/reliability.py` 的 `invoke_llm`（新增函数，旧 `call_with_retry`/`RedisDegradeTracker` 保留）
3. 新建 `common/mcp/reliability.py` 的 `call_tool`
4. 切 `rag-service` rate_limit 到 common 零件（唯一业务文件改动，行为不变，灰度按 rag 自身策略）
5. 回归：限流单测 + 熔断隔离验证 + rag 限流迁移行为不变
- **Rollback**：rag rate_limit 切换可单文件 revert；common 新增文件删除即可无残留；`invoke_llm`/`call_tool` 未被业务调用前回滚零影响。

## Open Questions

（无）—— 错误码号段已由用户拍板：A5015（Token 超限）/ A5016（任务超时）/ A5017（LLM 熔断），续用 A5 域末尾，不与既有码冲突。
