## MODIFIED Requirements

### Requirement: Redis Key 模式

系统 SHALL 使用 9 个 Key 模式，DB6，前缀 `csmp:`，并提供与 PRD 9.3 一致（本变更另有注明者除外）的 TTL 常量（秒；永不过期为 `None`），复用 common Redis 连接：其中第 1-6、8、9 项在 `app/utils/redis_keys.py` 定义；第 7 项改由 common LLM 韧性层按模型名派生（complaint-resilience-wiring 变更）：

1. `csmp:session:{session_id}` — STRING(JSON)，TTL 24h（`TTL_SESSION=86400`）
2. `csmp:ticket:{id}:pkg` — STRING(JSON)，TTL 24h（`TTL_TICKET_PKG=86400`）
3. `csmp:verdict:{id}:latest` — STRING(JSON)，TTL 24h（`TTL_VERDICT_LATEST=86400`）
4. `csmp:rslv:{id}:latest` — STRING(JSON)，TTL 24h（`TTL_RSLV_LATEST=86400`）
5. `csmp:hotspots:{start}:{end}` — STRING(JSON)，TTL 6h（`TTL_HOTSPOTS=21600`）
6. `csmp:gather:lock:{id}` — STRING(SET NX EX)，TTL 120s（`TTL_GATHER_LOCK=120`）
7. `csmp:llm_degraded:{model}` — STRING("1")，TTL 60s：由 `common.llm.reliability.invoke_llm` 在主模型失败时经 `mark_degraded_flag` SETEX，按模型隔离、到期自动恢复。取代批次3 的全局永不过期键 `csmp:llm_degraded`（`LLM_DEGRADED_KEY` / `TTL_LLM_DEGRADED` 从 `app/utils/redis_keys.py` 移除）
8. `csmp:task:timeout:{session_id}` — STRING，TTL 30min（`TTL_TASK_TIMEOUT=1800`）
9. `csmp:mode:active` — STRING，永不过期（`TTL_MODE_ACTIVE=None`）

#### Scenario: 9 个 Key 均可构造

- **WHEN** 调用 redis_keys 模块的各 Key 构造函数，或经 common `make_llm_breaker_key("csmp", model)` 派生第 7 项
- **THEN** 返回符合上述模式的字符串，前缀均为 `csmp:`，且第 7 项带模型段后缀

#### Scenario: TTL 常量对齐 PRD 9.3

- **WHEN** 读取 redis_keys 中的 TTL_* 常量与 common `_DEGRADE_TTL_SECONDS`
- **THEN** 与上表秒数一致（永不过期项为 `None`）；第 7 项按本变更为 60s，不再是批次3 的永不过期

#### Scenario: 复用 common Redis 连接

- **WHEN** 检查 redis_keys.py 的 import / 文档约定
- **THEN** 不新建独立连接池；连接复用 common Redis 配置

#### Scenario: 全局永不过期 LLM 键已废弃

- **WHEN** 检查 `app/utils/redis_keys.py` 与代码库
- **THEN** 不存在 `LLM_DEGRADED_KEY` / `TTL_LLM_DEGRADED` 定义，也无任何代码读写裸键 `csmp:llm_degraded`
