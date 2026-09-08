# Spec Delta: complaint-service/scaffold

## Purpose

为 complaint-service 建立最小可运行脚手架：目录结构、配置体系、依赖声明、依赖注入、FastAPI 入口。复用 M0 common 模块，不重复造轮子。所有交付在 `services/complaint-service/` 下。

## ADDED Requirements

### Requirement: 目录结构对齐 PRD 第23章

系统 SHALL 在 `services/complaint-service/app/` 下创建完整子包结构，对齐 PRD 第23章文件结构树：`api/`、`api/routes/`、`graph/`、`graph/nodes/`、`mcp/`、`llm/`、`prompt/`、`prompt/templates/`、`models/`、`service/`、`utils/`。每个子包 MUST 含 `__init__.py`。MUST NOT 删除批次0 已有的 `prompt/templates/` 和 `tests/mock/`。

#### Scenario: 子包 __init__.py 齐全

- **WHEN** 列出 `services/complaint-service/app/` 下各子目录
- **THEN** api/、api/routes/、graph/、graph/nodes/、mcp/、llm/、prompt/、prompt/templates/、models/、service/、utils/ 均含 `__init__.py`

#### Scenario: 批次0 产物保留

- **WHEN** 检查 `services/complaint-service/app/prompt/templates/`
- **THEN** 批次0 的 8 个 YAML 文件仍存在，未被删除或覆盖

### Requirement: 配置项继承 common Settings

系统 SHALL 在 `app/config.py` 定义 `ComplaintSettings`，继承 `common.config.settings.Settings`（pydantic-settings，`env_file=".env"`, `extra="ignore"`），新增以下字段：

- `COMPLAINT_MAX_LOOPS: int = 3`
- `COMPLAINT_TASK_TIMEOUT: int = 1800`
- `COMPLAINT_MAX_TOKENS: int = 50000`
- `COMPLAINT_MAX_ACTIVE: int = 20`
- `MCP_GATEWAY_MODE: str = "inline"`（inline | standalone）
- `LLM_MODEL_PRIMARY: str = "deepseek-r1"`
- `LLM_MODEL_FALLBACK_1: str = "qwen-plus"`
- `LLM_MODEL_FALLBACK_2: str = "qwen-turbo"`
- `LLM_DEEPSEEK_API_KEY: str`
- `LLM_DEEPSEEK_BASE_URL: str`
- `LLM_DASHSCOPE_API_KEY: str`

#### Scenario: 配置可 import 且默认值正确

- **WHEN** 在服务根执行 `from app.config import settings`
- **THEN** `settings.COMPLAINT_MAX_LOOPS == 3` 且 `settings.LLM_MODEL_PRIMARY == "deepseek-r1"`

#### Scenario: 环境变量可覆盖

- **WHEN** 设置环境变量 `COMPLAINT_MAX_ACTIVE=50` 后 import
- **THEN** `settings.COMPLAINT_MAX_ACTIVE == 50`

### Requirement: 依赖声明补充 LangGraph 与 asyncpg

系统 SHALL 在 `services/complaint-service/pyproject.toml` 声明以下依赖：`langgraph>=0.0.40`、`langgraph-checkpoint-postgres`、`asyncpg>=0.29.0`。其余依赖复用全局 `requirements.lock` 约定。

#### Scenario: 依赖可被解析

- **WHEN** 检查 `pyproject.toml` 的 dependencies
- **THEN** 包含上述三个依赖及其版本约束

### Requirement: 依赖注入工厂

系统 SHALL 在 `app/api/deps.py` 提供：
- `get_session()`：复用 `common.db.session.get_session`（async generator，yield AsyncSession）
- `get_mcp_client()`：本服务 MCP Client 单例工厂（批次1 允许返回 Mock 或 None 占位）
- `get_llm_provider()`：本服务 LLM Provider 单例工厂（批次1 允许返回 Mock 或 None 占位）

#### Scenario: get_session 可注入

- **WHEN** FastAPI 依赖项使用 `Depends(get_session)`
- **THEN** 获得 `common.db.session` 的 AsyncSession

#### Scenario: MCP/LLM 单例工厂可调用

- **WHEN** 调用 `get_mcp_client()` 或 `get_llm_provider()`
- **THEN** 返回可调用对象或 None（不抛异常，不阻塞启动）

### Requirement: FastAPI 入口与生命周期

系统 SHALL 在 `app/main.py` 提供 FastAPI 应用：
- 注册 `common.trace.middleware`、`common.errors.handlers.register_exception_handlers`
- 挂载路由（批次1 至少含健康检查路由）
- `startup` 生命周期预留 `AsyncPostgresSaver` + MCP Client 初始化；PG/MCP 未配置时跳过或标记 degraded，不得因缺依赖导致进程起不来
- `shutdown` 生命周期预留连接池清理
- 健康检查接口存在且返回 200；探活字段可含 PG/Redis/MCP 状态（失败时 degraded，仍返回 200）

#### Scenario: uvicorn 可启动

- **GIVEN** 在 `services/complaint-service/` 目录
- **WHEN** 执行 `uvicorn app.main:app --host 0.0.0.0 --port 8006`
- **THEN** 进程正常启动，不因 PG/MCP 未配置而崩溃

#### Scenario: 健康检查返回 200

- **WHEN** `GET /health` 或 `GET /api/v1/health`
- **THEN** HTTP 200，响应体含服务状态（PG/Redis/MCP 可标记 degraded）
