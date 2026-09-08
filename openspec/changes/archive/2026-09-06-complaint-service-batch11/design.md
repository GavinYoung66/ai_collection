## Context

当前状态：
- `services/complaint-service/Dockerfile` 已存在并对齐 M0 模板（`python:3.11.15-slim` + `requirements.lock` + `common/` + `COPY services/complaint-service/app/` + `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`），容器内端口 8000；`curl` 已装；Dockerfile 内 `HEALTHCHECK` **可选**（compose healthcheck 已覆盖）。
- 仓库根 `docker-compose.dev.yml` 已声明 `complaint-service:8006`，`env_file: .env`，`depends_on: postgres+redis`，bind mount 热重载；缺 `rag-service` 依赖、Redis DB6 隔离、MCP 网关 prod 容器。
- 仅有根 `.env`（宿主机视角），无四环境差异文件；`ComplaintSettings` 已定义 `MCP_GATEWAY_MODE`、`MCP_GATEWAY_URL`、`LLM_MODEL_*`、`USE_MOCK_*`、`LLM_COT_KEY`、`COMPLAINT_TASK_TIMEOUT`、`LOG_LEVEL`；common Settings 已有 `LLM_CIRCUIT_BREAKER_THRESHOLD/RECOVERY`、`REDIS_DB`。**无** `COT_PLAINTEXT` / `MASKING_DISABLED` / `CIRCUIT_BREAKER_DISABLED` / `USE_UNREVIEWED_PROMPT`（`extra="ignore"` 会静默丢弃幽灵 env）。
- 灰度运行时在 Redis（`mode_service`），非 env。
- 冒烟脚本 `scripts/smoke/smoke_complaint_service.sh` 使用 **`PORT`（默认 8006）**，不认 `COMPLAINT_SERVICE_URL`。

目标（见 proposal.md）：补齐模块13，使服务可在 dev/test/pre/prod 四环境一致启动并满足生产安全约束。

## Goals / Non-Goals

**Goals**
- 生产可用 Dockerfile（M0 模板对齐，增量不重写无关层）。
- compose 可 `up`：complaint-service:8006，依赖 postgres+redis+rag-service；dev MCP 进程内 / prod MCP 网关独立 stub（具名双实例）。
- REQ-CJ-053 资源规格写入 design 与 prod compose（声明式；本机磁盘可缩小）。
- 四环境 `config/{dev,test,pre,prod}.env` 差异矩阵对齐 PRD 第22章；生产禁止项映射到**可读 Settings 字段**。
- 验证：`docker compose up` 后 health 可达；`PORT=8006` 冒烟跑通（或本机降级）。

**Non-Goals**
- 批次12 外部对齐正文（真实 MCP Server / RAG 数据 / LLM 配额 / 业务系统）。
- K8s / HPA 全量（design 预留接口，本批次不落地）。
- 真 LLM / MCP 生产密钥入库（占位 + 部署注入）。
- Skill 架构。
- 改动 batch10 已验收的测试语义。
- 依赖 Docker Swarm / `deploy.replicas` 实现多副本。

## Decisions

### D1：Dockerfile 增量完善，不重写

**选择**：保留现有 Dockerfile 主体，仅按需增补。容器内端口固定 8000，由 compose 映射 8006。`HEALTHCHECK` **可选**——优先依赖 compose `healthcheck`，避免与 Dockerfile 重复强制。

**理由**：现有 Dockerfile 已通过 M0 模板验证，重写风险大于收益。

**替代**：新建 Dockerfile.prod —— 否决。

### D2：compose 采用「dev 增量 + prod 基线文件」双轨；多实例用具名服务

**选择**：
- `docker-compose.dev.yml`：增量补齐 `rag-service` 依赖（**统一** `condition: service_started`）、`REDIS_DB=6`、dev `MCP_GATEWAY_MODE=inline`、bind mount 热重载。
- 新增 `docker-compose.complaint.yml`：prod 基线。
  - **双实例不写 `deploy.replicas`**（非 Swarm 下常被忽略，验收会假绿）。改为具名服务：
    - `complaint-service-a`：宿主机 `8006:8000`（冒烟入口）
    - `complaint-service-b`：宿主机 `8007:8000`（第二实例探活；LB/Nginx 可选预留）
    - `mcp-gateway-a` / `mcp-gateway-b`：仅内网 9000，不暴露公网；可用 network alias `mcp-gateway` 供 `MCP_GATEWAY_URL=http://mcp-gateway:9000` 解析（stub 无真 LB 亦可接受）
  - 资源：`deploy.resources.limits` **可作为声明**写入（对齐 REQ-CJ-053 文档）；本机非 Swarm 可不生效——验收以「服务进程数/具名容器 up」为准，不以 Swarm replicas 为准。
  - PG 磁盘：**声明** 100GB volume；注释「本机可缩小（如 10GB）」。

**理由**：dev/prod 差异大；具名服务在 Compose V2 本机可验。

**替代**：Swarm + `deploy.replicas` —— 否决（本机/CI 默认无 Swarm）。单文件 + profile —— 否决。

### D3：MCP 网关 prod 容器为 stub

**选择**：`mcp-gateway-a/b` 用最小镜像 stub，标注「真实实现由批次12 外部对齐」；complaint 通过 `MCP_GATEWAY_URL=http://mcp-gateway:9000`（或显式 `http://mcp-gateway-a:9000`）访问。

**理由**：真实 4 个 MCP Server 尚未就绪，stub 保证编排可跑通。

### D4：四环境 env + 现有字段真相源（禁止幽灵开关）

**选择**：`services/complaint-service/config/{dev,test,pre,prod}.env`；compose `env_file` 指向对应文件。密钥均为 `change_me` / 空。

**生产禁止项 → 现有/约定映射（禁止写入无人读的布尔名）**：

| PRD 禁止项 | 落地方式（本批） |
|---|---|
| 禁止进程内 MCP | `MCP_GATEWAY_MODE=standalone` 且 `MCP_GATEWAY_URL` 非空 |
| 禁止关闭熔断 | `LLM_CIRCUIT_BREAKER_THRESHOLD≥1` 且 `LLM_CIRCUIT_BREAKER_RECOVERY≥1`（prod 强制 3 / 60） |
| 禁止 CoT 明文调试 | **无** `COT_PLAINTEXT` 字段；CoT 走 AES（`LLM_COT_KEY` 部署注入）；prod.env **不得**出现 `COT_PLAINTEXT=true` 等调试别名；文件级 grep 确认无此类键 |
| 禁止关闭脱敏 | **无** `MASKING_DISABLED`；脱敏由既有业务层保证；prod.env **不得**出现 `MASKING_DISABLED=true` |
| 禁止未评审 Prompt | **无** `USE_UNREVIEWED_PROMPT`；由 Prompt 评审+batch10 回归门禁保证；prod.env **不得**出现该键为 true |

**可选 apply**：`LOG_LEVEL` 已接入 `ComplaintSettings` + `setup_logging`；**不**新增上表幽灵布尔字段。

**灰度**：矩阵写「pre/prod 可灰度」仅为文档；运行时 Redis `mode_service`，env **不**伪造 `GRAY_*` / `MODE_ACTIVE`。

**差异矩阵**（对齐 PRD 第22章；本批取值注明）：

| 维度 | dev | test | pre | prod |
|---|---|---|---|---|
| ENV | dev | test | pre | prod |
| MCP_GATEWAY_MODE | inline | standalone | standalone | standalone |
| MCP_GATEWAY_URL | (空) | http://mcp-gateway:9000 | http://mcp-gateway:9000 | http://mcp-gateway:9000 |
| LLM_MODEL_PRIMARY | qwen-turbo | qwen-turbo | deepseek-r1 | deepseek-r1 |
| LLM_MODEL_FALLBACK_1 | (空) | qwen-plus | qwen-plus | qwen-plus |
| LLM_MODEL_FALLBACK_2 | (空) | (空) | (空) | qwen-turbo |
| USE_MOCK_MCP | true | true | false | false |
| USE_MOCK_LLM | true | true | false | false |
| REDIS_DB | 6 | 6 | 6 | 6 |
| LOG_LEVEL | DEBUG | INFO | INFO | INFO（PRD：INFO+ERROR 告警；本批文件写 INFO） |
| COMPLAINT_TASK_TIMEOUT | **3600**（PRD 60min） | 1800 | 1800 | 1800 |
| LLM_CIRCUIT_BREAKER_THRESHOLD | **9999**（近似关，现网无 disabled） | 3 | 3 | **3**（强制开） |
| LLM_CIRCUIT_BREAKER_RECOVERY | 60 | 60 | 60 | 60 |
| 灰度 | Redis 默认；文档关 | Redis 可切 | Redis 可灰度 | Redis 按策略 |
| CoT / 脱敏 / 未评审 Prompt | 见映射表；不写幽灵字段 | 同左 | 同左 | 同左 + grep 门禁 |

### D5：REQ-CJ-053 资源规格

| 组件 | CPU | 内存 | 磁盘 | 副本 |
|---|---|---|---|---|
| complaint-service | 4 核 | 8 GB | — | ≥2（prod：a/b 具名） |
| PostgreSQL | 4 核 | 8 GB | 100 GB SSD（本机可缩小） | 1 |
| Redis | 2 核 | 4 GB | — | 1（DB6） |
| MCP 网关 | 2 核 | 4 GB | — | ≥2（prod：a/b 具名） |

### D6：rag-service 依赖条件统一

**选择**：dev 与 prod compose 对 `rag-service` 一律 `depends_on: condition: service_started`（非 `service_healthy`），避免最小 up / rag 未就绪时卡住；postgres/redis 仍 `service_healthy`。

**理由**：RAG 对 complaint 可降级；spec/tasks 不得再写 healthy。

### D7：冒烟验证变量

**选择**：一律 `PORT=8006 bash scripts/smoke/smoke_complaint_service.sh`。禁止文档使用不存在的 `COMPLAINT_SERVICE_URL`。

## Risks / Trade-offs

- **[MCP stub 行为 ≠ 真实]** → 批次12 对齐；本批仅编排/健康。
- **[具名双实例无内置 LB]** → a=8006、b=8007；Nginx 可选预留；冒烟只打 8006。
- **[deploy.resources 本机可能不生效]** → 声明对齐规格；验收看容器 up，不卡 Swarm。
- **[LOG_LEVEL]** → `ComplaintSettings.LOG_LEVEL` + `setup_logging(..., level=)` 已接线；四环境 env 生效。
- **[PG 100GB]** → compose 注释 + `labels` 落档；命名卷本机不强制配额，生产挂 ≥100GB 块设备。
- **[MCP health 假绿]** → lifespan/`MCPGateway.health_check` 在 standalone 且非 Mock 时 HTTP 探活 `MCP_GATEWAY_URL`。
- **[脱敏/未评审 Prompt 无 env 开关]** → 不假装有字段；靠代码路径 + CI/回归 + prod.env 负向 grep。
- **[密钥占位]** → 不提交真实密钥。

## Migration Plan

1. **13.1 Dockerfile**：确认 M0 模板；HEALTHCHECK 可选。
2. **13.2 compose**：dev 增量（rag=`service_started`、REDIS_DB=6、inline）；prod 具名 a/b + stub 网关 + 资源声明。
3. **13.3 env**：按 D4 矩阵创建四文件；禁止项映射 + 负向 grep。
4. **验证**：`docker compose -f docker-compose.complaint.yml up -d` → `curl :8006/health` → `PORT=8006` 冒烟。
5. **回滚**：删 prod compose 与 config/*.env，还原 dev compose 增量。

## Open Questions

- mcp-gateway stub 镜像选型（`nginx:alpine` vs 自定义）——实现时定。
- 是否加 Nginx 把 a/b 收敛到单一 8006——可选，非本批完成标准。
