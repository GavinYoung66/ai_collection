# Proposal: complaint-service-batch1

## 为什么（Why）

M3 投诉异议智能研判系统的后续所有批次（ORM、MCP、LangGraph、LLM、PromptOps、API、业务服务、灰度降级）都依赖 complaint-service 的项目脚手架。批次0 已交付 Prompt 模板和 Mock 基线，但服务本身只有 health 空壳，缺少可启动的 FastAPI 入口、配置体系、依赖注入和 LangGraph/PostgreSQL 依赖声明。若无脚手架，后续批次无法落地。

本批次建立 complaint-service 的最小可运行骨架，复用 M0 common 模块（logging/trace/errors/db.session/settings），为后续批次提供统一的配置、DI、入口和生命周期基线。

## 交付什么（What）

在 `services/complaint-service/` 下交付 5 类产物（严格串行 1.1→1.5）：

| ID | 产物 | 说明 |
|----|------|------|
| 1.1 | 目录结构 | 对齐 PRD 第23章文件结构树，创建各子包 `__init__.py`；保留批次0已有 `prompt/templates/` 和 `tests/mock/` |
| 1.2 | 配置体系 | `app/config.py` 继承 `common.config.settings.Settings`，定义投诉研判专有配置项 |
| 1.3 | 依赖声明 | `pyproject.toml` 补充 `langgraph`、`langgraph-checkpoint-postgres`、`asyncpg` |
| 1.4 | 依赖注入 | `app/api/deps.py` 提供 DB session、MCP Client、LLM Provider 单例工厂 |
| 1.5 | FastAPI 入口 | `app/main.py` 注册路由/中间件/异常处理/生命周期，健康检查返回 200 |

## 完成标准

- `uvicorn app.main:app` 可在 `services/complaint-service/` 下启动
- 健康检查接口返回 200
- 未配置 PG/MCP 时以 degraded 模式启动，不因缺依赖导致进程起不来

## 非目标（What NOT）

- 不实现业务 API/研判逻辑（批次7）
- 不实现 ORM/DDL（批次2）
- 不实现 LangGraph 节点/边/State Schema（批次4）
- 不实现真实 MCP Client/LLM Provider（批次3、5）
- 不实现 Prompt 加载器（批次6）
- 不改 `common/`（不强行新增 `BaseLLMProvider`）

## 路径硬约束

所有新增/修改文件必须在 `services/complaint-service/` 下；禁止写到仓库根 `app/`、`tests/`。
