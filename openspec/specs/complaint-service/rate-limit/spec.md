## Purpose

定义限流服务：5 维限流（工单接入/MCP 令牌桶/LLM 令牌桶/动态消费/单工单锁），配置由 YAML 管理。

## Requirements

### Requirement: 5 维限流

系统 SHALL 在 `app/service/rate_limiter.py` 实现 5 维限流（配置由 `config/rate_limiter.yaml` 管理，不硬编码）：
1. 工单接入：Semaphore(max_active=20)，超限入 Redis 有序队列 `csmp:queue:pending`（max 500，FIFO，超时 10min）
2. MCP 调用：令牌桶按 Server（业务 50/质检 30/RAG 20 QPS）
3. LLM QPS：令牌桶按模型（deepseek-r1 5/qwen-plus 10/qwen-turbo 30）
4. 高投诉涌入缓冲：队列消费速率动态调整
5. 单工单：SETNX 锁 `csmp:complaint:lock:{id}`，TTL=**TTL_COMPLAINT_LOCK（1800s）**，复用 `redis_keys.complaint_lock_key`

#### Scenario: 工单接入超限排队

- **WHEN** 活跃工单达 20
- **THEN** 新工单进入 csmp:queue:pending

#### Scenario: 单工单幂等锁

- **WHEN** 同一 complaint_id 重复执行
- **THEN** SETNX 失败，跳过重复执行

### Requirement: YAML 配置驱动

系统 SHALL 从 `config/rate_limiter.yaml` 读取限流参数，运行时可热加载，禁止硬编码阈值。

#### Scenario: 加载配置

- **WHEN** rate_limiter 初始化
- **THEN** 参数来自 YAML，非硬编码
