## Why

投诉服务已完成批次0～10（数据/LLM/MCP/图/API/业务服务/灰度灾备/测试CI冒烟压测基线），但尚未生产容器化与多环境配置。当前仅有根 `.env` 与 dev compose 中的 complaint-service:8006，缺四环境差异矩阵、MCP 网关 prod 独立容器约定、REQ-CJ-053 资源规格、生产禁止项落地校验。批次11 补齐模块13，使服务可在 dev/test/pre/prod 四环境通过 `docker compose` 一致启动并满足生产安全约束。

## What Changes

- **13.1 Dockerfile**：对齐/完善 `services/complaint-service/Dockerfile`（已存在则增量），保持 M0 统一模板（`python:3.11.15-slim` + `requirements.lock` + `common/` + `services/complaint-service/app/` + `CMD uvicorn app.main:app`），容器内端口 8000，compose 暴露 8006。Dockerfile 内 `HEALTHCHECK` **可选**（compose 侧已有 healthcheck 即可）。
- **13.2 compose**：在仓库根 `docker-compose.dev.yml` 增量补齐 complaint-service 依赖（postgres+redis+rag-service）、Redis DB6、dev MCP 进程内；新增 `docker-compose.complaint.yml`（prod 基线）含 **具名双实例**（`complaint-service-a/b`、`mcp-gateway-a/b` stub，**不依赖** Compose Swarm `deploy.replicas`）+ 内网隔离；资源规格对齐 REQ-CJ-053（limits 声明；本机可缩小磁盘）。
- **13.3 多环境配置**：新增 `services/complaint-service/config/{dev,test,pre,prod}.env`，差异矩阵对齐 PRD 第22章；**生产禁止项映射到现有 Settings 字段**（禁止幽灵开关名）；密钥不入库。

## Capabilities

### New Capabilities
- `complaint-service/deploy-docker`: complaint-service 生产 Dockerfile 规范与构建验证（对齐 M0 模板、镜像可 build、健康检查可通）。
- `complaint-service/deploy-compose`: complaint-service 多服务 compose 编排规范（端口 8006、依赖、MCP 网关 inline/standalone 双模式、REQ-CJ-053 资源规格、prod 具名多实例）。
- `complaint-service/deploy-env`: complaint-service 四环境（dev/test/pre/prod）配置差异矩阵与加载约定（LLM 模型链、MCP 模式、熔断、灰度、生产禁止项、密钥不硬编码）。

### Modified Capabilities
（无）

## Impact

- **代码（仅 apply 时）**：`services/complaint-service/Dockerfile`（增量）、`docker-compose.dev.yml`（增量）、新增 `docker-compose.complaint.yml`、新增 `services/complaint-service/config/*.env`。
- **配置加载**：沿用 pydantic-settings；compose `env_file` 注入。禁止项与差异矩阵以 **已有字段** 为准（见 design D4/D6）：`MCP_GATEWAY_MODE`、`MCP_GATEWAY_URL`、`LLM_CIRCUIT_BREAKER_*`、`COMPLAINT_TASK_TIMEOUT`、`USE_MOCK_*`、`LLM_MODEL_*`、`REDIS_DB`、`LLM_COT_KEY`（占位）。
- **可选最小字段（apply 允许）**：仅当需让 `LOG_LEVEL` 真正生效时，可在 `ComplaintSettings` **新增** `LOG_LEVEL: str` 并接到 logging；**不**新增 `COT_PLAINTEXT` / `MASKING_DISABLED` / `CIRCUIT_BREAKER_DISABLED` / `USE_UNREVIEWED_PROMPT` 等幽灵布尔名。
- **灰度**：运行时仍走 Redis `mode_service`（`MODE_ACTIVE` / `gray_percent`），**不**在 env 文件伪造灰度开关字段；矩阵仅文档约定。
- **测试**：不改动 batch10 测试语义；冒烟用现有脚本变量 **`PORT=8006`**（非 `COMPLAINT_SERVICE_URL`）。
- **外部依赖**：MCP 网关 prod 容器为 stub；真实 MCP Server 由批次12 外部对齐；密钥由部署环境注入。
- **Non-Goals**：批次12 外部对齐正文、K8s/HPA 全量、真密钥入库、Skill 架构、本轮改业务代码（规划修订阶段）。
