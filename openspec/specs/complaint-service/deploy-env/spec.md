## Purpose

定义投诉服务四环境（dev/test/pre/prod）配置差异矩阵与加载约定，对齐 PRD 第22章，保证生产安全约束落地到**可读 Settings 字段**（禁止幽灵开关名）。

## Requirements

### Requirement: 四环境配置文件

系统 SHALL 提供 `services/complaint-service/config/{dev,test,pre,prod}.env` 四个环境配置文件，由 compose 的 `env_file` 加载，覆盖 `common/config/settings.py` 与 `app/config.py` 的字段；不得在代码中硬编码密钥。

#### Scenario: 四文件存在

- **WHEN** 检查 `services/complaint-service/config/`
- **THEN** 存在 `dev.env`、`test.env`、`pre.env`、`prod.env` 四个文件

#### Scenario: 密钥不硬编码

- **WHEN** grep `services/complaint-service/config/*.env` 与 `app/` 源码
- **THEN** 密钥字段（`LLM_DEEPSEEK_API_KEY`、`LLM_DASHSCOPE_API_KEY`、`JWT_SECRET`、`AES_KEY`、`LLM_COT_KEY`）均为占位值或由部署注入，无真实生产密钥

### Requirement: LLM 模型链差异

系统 SHALL 按环境配置 LLM 模型链：dev `qwen-turbo`；test `qwen-turbo + qwen-plus`；pre `deepseek-r1 + qwen-plus`；prod `deepseek-r1` 主 + `qwen-plus` 一级备 + `qwen-turbo` 二级备（对应 `LLM_MODEL_PRIMARY`/`LLM_MODEL_FALLBACK_1`/`LLM_MODEL_FALLBACK_2`）。

#### Scenario: prod 模型链完整

- **WHEN** 加载 `prod.env`
- **THEN** `LLM_MODEL_PRIMARY=deepseek-r1`、`LLM_MODEL_FALLBACK_1=qwen-plus`、`LLM_MODEL_FALLBACK_2=qwen-turbo`

#### Scenario: dev 单模型省钱

- **WHEN** 加载 `dev.env`
- **THEN** `LLM_MODEL_PRIMARY=qwen-turbo`，fallback 可为空或同值

### Requirement: MCP 模式差异

系统 SHALL 配置：dev `MCP_GATEWAY_MODE=inline`；test/pre/prod `MCP_GATEWAY_MODE=standalone`（prod 必须 standalone，禁止进程内）。

#### Scenario: prod 禁止进程内 MCP

- **WHEN** 加载 `prod.env`
- **THEN** `MCP_GATEWAY_MODE=standalone` 且 `MCP_GATEWAY_URL` 非空

### Requirement: 生产禁止项（映射到现有字段）

系统 SHALL 在 `prod.env` 用**现有 Settings 字段**落实 PRD 生产禁止项，SHALL NOT 依赖无人读取的幽灵布尔（如 `COT_PLAINTEXT`、`MASKING_DISABLED`、`CIRCUIT_BREAKER_DISABLED`、`USE_UNREVIEWED_PROMPT`）作为唯一安全手段。

映射约定：
- 禁止进程内 MCP → `MCP_GATEWAY_MODE=standalone` + 非空 `MCP_GATEWAY_URL`
- 禁止关闭熔断 → `LLM_CIRCUIT_BREAKER_THRESHOLD=3` 且 `LLM_CIRCUIT_BREAKER_RECOVERY=60`
- 禁止 CoT 明文调试 / 关闭脱敏 / 未评审 Prompt → 由既有业务路径与回归门禁保证；`prod.env` SHALL NOT 包含将这些能力打开的调试键（负向 grep）

#### Scenario: prod 安全开关（正向）

- **WHEN** 加载 `prod.env`
- **THEN** `MCP_GATEWAY_MODE=standalone`，且 `LLM_CIRCUIT_BREAKER_THRESHOLD` 与 `LLM_CIRCUIT_BREAKER_RECOVERY` 为有效正值（熔断未关闭）

#### Scenario: prod 安全开关（负向）

- **WHEN** grep `prod.env` 中危险调试键
- **THEN** 不存在 `MCP_GATEWAY_MODE=inline`，且不存在将 `COT_PLAINTEXT`/`MASKING_DISABLED`/`CIRCUIT_BREAKER_DISABLED`/`USE_UNREVIEWED_PROMPT` 设为开启的赋值

### Requirement: 调试与超时差异

系统 SHALL 按环境配置：日志级别（dev DEBUG / test INFO / pre INFO / prod INFO，对齐 PRD「INFO+ERROR 告警」的文件侧写 INFO）、任务超时（dev `COMPLAINT_TASK_TIMEOUT=3600` / 其余 1800）、熔断（dev 用高阈值近似关如 `THRESHOLD=9999`；test/pre/prod 开启为 3）、灰度模式（文档约定 pre/prod 可灰度；运行时 Redis `mode_service`，env 不伪造灰度字段）。`LOG_LEVEL` 写入四环境文件；若 Settings 尚无该字段，apply 允许最小新增并接线 logging。

#### Scenario: 日志与超时差异

- **WHEN** 加载四环境文件
- **THEN** dev `LOG_LEVEL=DEBUG` 且 `COMPLAINT_TASK_TIMEOUT=3600`；prod `LOG_LEVEL=INFO` 且 `COMPLAINT_TASK_TIMEOUT=1800`

#### Scenario: prod 熔断开启

- **WHEN** 加载 `prod.env`
- **THEN** `LLM_CIRCUIT_BREAKER_THRESHOLD=3` 与 `LLM_CIRCUIT_BREAKER_RECOVERY=60`，熔断未关闭
