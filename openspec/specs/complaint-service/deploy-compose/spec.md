## Purpose

定义投诉服务多服务 compose 编排规范：端口 8006、依赖链、MCP 网关 inline/standalone 双模式、REQ-CJ-053 资源规格、prod 多实例。

## Purpose

定义投诉服务多服务 compose 编排规范：端口 8006、依赖链、MCP 网关 inline/standalone 双模式、REQ-CJ-053 资源规格、prod 具名多实例（非 Swarm replicas）。

## Requirements

### Requirement: complaint-service 服务编排

系统 SHALL 在 compose 中声明 complaint-service 服务，端口映射 `8006:8000`（prod 第二实例可用 `8007:8000`），`env_file` 指向对应环境配置，`depends_on` 包含 `postgres`（service_healthy）、`redis`（service_healthy）、`rag-service`（**service_started**，不要求 healthy），并配置 `curl` healthcheck 指向容器内 `8000/health`。

#### Scenario: dev 编排可 up

- **WHEN** 执行 `docker compose -f docker-compose.dev.yml up -d complaint-service`
- **THEN** complaint-service 容器 healthy，宿主机 `http://localhost:8006/health` 返回 200

#### Scenario: 依赖链完整

- **WHEN** complaint-service 启动
- **THEN** postgres 与 redis 已 healthy 后才启动；rag-service 仅需 started（可降级）

### Requirement: MCP 网关双模式

系统 SHALL 支持 MCP 网关两种模式：dev 环境 `MCP_GATEWAY_MODE=inline`（进程内，无独立容器）；prod 环境 `MCP_GATEWAY_MODE=standalone`，由独立 `mcp-gateway-a` / `mcp-gateway-b` stub 容器提供（真实服务由批次12 对齐），不暴露公网端口。多实例 SHALL 用**具名服务**实现，SHALL NOT 依赖 Docker Swarm `deploy.replicas` 作为本批验收依据。

#### Scenario: dev 进程内 MCP

- **WHEN** `ENV=dev` 且 `MCP_GATEWAY_MODE=inline`
- **THEN** complaint-service 进程内加载 MCP Client，不依赖独立 mcp-gateway 容器

#### Scenario: prod 独立 MCP 网关具名双实例

- **WHEN** 执行 prod compose up
- **THEN** 存在 `mcp-gateway-a` 与 `mcp-gateway-b`（或等价具名）至少两个容器，complaint-service 通过 `MCP_GATEWAY_URL`（如 `http://mcp-gateway:9000`）访问内网网关，网关端口不映射到公网

### Requirement: Redis DB6

系统 SHALL 让 complaint-service 使用 Redis DB6（`REDIS_DB=6`），与其他服务隔离，compose 中显式覆盖 `REDIS_DB=6`。

#### Scenario: DB6 隔离

- **WHEN** complaint-service 写入会话数据
- **THEN** 数据落在 Redis DB6，不与其他服务的 DB0/DB2 冲突

### Requirement: REQ-CJ-053 资源规格与具名多实例

系统 SHALL 在 prod 编排中声明资源规格：complaint-service 4 核 8GB、PostgreSQL 4 核 8GB / 100GB SSD（本机可缩小并注释）、Redis 2 核 4GB、MCP 网关 2 核 4GB。complaint-service 至少 2 个具名实例（如 `complaint-service-a` 映射 8006、`complaint-service-b` 映射 8007）；mcp-gateway 至少 2 个具名实例。`deploy.resources.limits` 可作为规格声明写入；验收以具名容器 running/healthy 为准，不以 Swarm replicas 计数为准。

#### Scenario: prod 多实例可验

- **WHEN** 执行 `docker compose -f docker-compose.complaint.yml up -d` 后 `docker compose -f docker-compose.complaint.yml ps`
- **THEN** 可见至少两个 complaint-service 具名容器与至少两个 mcp-gateway 具名容器为 running（或 healthy）

#### Scenario: 资源限额声明

- **WHEN** 查看 prod compose
- **THEN** complaint-service 实例声明 cpus=4 / memory=8g（或等价），PG/Redis/MCP 网关规格符合 REQ-CJ-053 文档值
