## Context

现状约束（已通过代码核实，详见 proposal.md「Impact」）：

- 8 个业务服务分散在多个 compose 文件；主 `docker-compose.yml` 只跑基础设施。本期抓取目标锁定 `docker-compose.dev.yml` 单实例全栈。
- mcp-gateway 已有 `/metrics` 但指标名带服务前缀（`mcp_gateway_requests_total`），且其 `MetricsMiddleware` 为纯 ASGI 实现（避免缓冲破坏 `/mcp` SSE）。
- 所有真实 LLM 通道汇聚于 `common/llm/transport.py` 的 `post_chat_completions`，该函数返回完整 dict body（含 `usage`），但上游只用 `message_content_from_completions` 提取 content，`usage` 被丢弃。
- 生产三服务（complaint/rag/qc）均走服务自有 transport，无一走 `_default_llm_transport`。

## Goals / Non-Goals

**Goals:**
- 统一 8 服务 `/metrics` 暴露规范与指标命名，消灭 mcp-gateway 的服务前缀指标。
- 在 `post_chat_completions` 单点透传 usage，覆盖所有走该函数的 transport，改动面最小。
- Prometheus/Grafana 接入主 compose，抓取 `docker-compose.dev.yml` 单实例全栈，不引入跨 compose 网络依赖。
- `invoke_llm` 埋点不修改任何对外签名、返回值结构、异常类型。

**Non-Goals:**
- 不实现 `docker-compose.complaint.yml` 双实例抓取（端口与 dev.yml 冲突）。
- 不改造 rag-service `LLMFactory` 与 qc-service `_as_transport` 的 usage 透传——若其底层不调 `post_chat_completions`，本期 `token_source=missing`。
- 不做告警、日志采集、tracing、长期存储、业务指标。

## Decisions

### D1: 8 服务宿主机端口分配表（直接采用，不再扫描）

| 服务 | 宿主机端口 | 容器端口 | 来源 compose |
|---|---|---|---|
| data-platform | 8001 | 8000 | docker-compose.dev.yml |
| rag-service | 8002 | 8000 | docker-compose.dev.yml |
| qc-service | 8003 | 8000 | docker-compose.dev.yml |
| call-service | 8004 | 8000 | docker-compose.dev.yml |
| case-service | 8005 | 8000 | docker-compose.dev.yml |
| complaint-service | 8006 | 8000 | docker-compose.dev.yml |
| review-service | 8007 | 8000 | docker-compose.dev.yml |
| mcp-gateway | 9009 | 9000 | docker-compose.dev.yml |

注：mcp-gateway 容器内监听 9000（非 8000/8080）；9009 被选是因为宿主机 9000 已被 minio 占用；8080 是 `docker-compose.gateway.yml` 里 nginx 的端口，与本 change 无关。

**备选方案**：通过 docker network 让 Prometheus 直接加入 dev 栈网络。否决理由：跨 compose 网络需要显式声明 `external: true` 共享网络，会把"启动顺序"和"网络归属"耦合到多个 compose 文件，维护成本高；`host.docker.internal` 方案对 macOS 原生支持、Linux 通过 `extra_hosts` 兼容，零侵入。

### D2: 纯 ASGI MetricsMiddleware（沿用 mcp-gateway 模式下沉）

**决策**：`common/observability/metrics.py` 提供纯 ASGI `MetricsMiddleware`，禁止改用 `BaseHTTPMiddleware`。

**理由**（来自 mcp-gateway 实测）：`BaseHTTPMiddleware` 会缓冲 response body，破坏 `/mcp` 的 SSE 流式响应。mcp-gateway 现有实现 [main.py:33-55](file:///Users/ellisguo/myproject/my_work/ai_collection/services/mcp-gateway/app/main.py#L33-L55) 是`__call__` 级别的 ASGI 中间件，直接包装 `scope/receive/send`，下沉到 common 时保持同款实现。

**备选**：FastAPI 的 `@app.middleware("http")`。否决理由：底层就是 `BaseHTTPMiddleware`，存在同样的 SSE 缓冲问题。

### D3: mcp-gateway 指标名迁移说明

**决策**：废弃 `mcp_gateway_requests_total{method,path,status}` 与 `mcp_gateway_active_connections`，迁移到 `http_requests_total{svc="mcp-gateway",method,path,status}` 与 `http_requests_in_flight{svc="mcp-gateway"}`。

**理由**：统一指标名让 Grafana 可以用同一套 PromQL 模板渲染所有服务（`sum by (svc)(rate(http_requests_total[1m]))`），避免每个服务一个仪表盘。`svc` 标签已能区分服务，指标名再带服务前缀是冗余。

**迁移成本**：mcp-gateway 测试中引用旧指标名的断言需最小更新（grep `mcp_gateway_requests_total` 即可定位）；无生产流量依赖旧指标名（Prometheus 尚未部署），无向后兼容负担。

### D4: mcp-gateway `/metrics` 鉴权白名单

**决策**：把 `/metrics` 从 `services/mcp-gateway/app/auth.py` 的 `_protected_kind` 移除（或 `__call__` 短路放行），使其在 `ENABLE_AUTH=true` 时仍可被 Prometheus 抓取。

**理由**：内部系统，`/metrics` 暴露的指标不含 PII（path 已白名单化），无凭证保护必要；让 Prometheus 配 bearer_token 会把"凭证管理"引入到本已复杂的 sops 体系，得不偿失。

**备选**：在 prometheus.yml 的 mcp-gateway job 配 `bearer_token_file`。否决理由：需要把 Bearer token 落到 prometheus 容器可读的路径，引入额外的密钥分发链路。

### D5: token usage ContextVar 透传位置——`post_chat_completions` 层

**决策**：在 `common/llm/transport.py` 的 `post_chat_completions` 内部、成功拿到 dict body 后，把 `body.get("usage")` 写入 `_llm_last_usage: ContextVar[dict | None]`。

**理由**：
- **单点覆盖**：`_default_llm_transport`（reliability.py）、`RealLLMTransport`（complaint-service）都调 `post_chat_completions`，一处改动全部覆盖。
- **零签名侵入**：不改 `LLMTransport = Callable[..., Awaitable[str]]` 的契约，所有服务自有 transport 不受影响。
- **可观测兜底**：body 不含 `usage` 时写入 `None`，`invoke_llm` 读取后落 `token_source=missing`，明确区分"没采到"vs"采到 0"。

**备选 A**：改 `_default_llm_transport` 单独透传。否决理由：complaint 生产路径走 `RealLLMTransport`，不走 `_default_llm_transport`，会导致投诉服务 `token_source=missing`，V4 验收失败。

**备选 B**：改 `LLMTransport` 签名为返回 `(content, usage)`。否决理由：破坏所有服务自有 transport 的契约，影响面过大。

**ContextVar 生命周期**：`invoke_llm` 进入时 `reset`（防上次残留）→ `post_chat_completions` 写入 → `invoke_llm` 的 `finally` 块中读取并清零（覆盖成功与失败/异常路径——失败路径 `_call_model` 抛异常而非返回，若在"成功返回后"读取会漏失败路径导致跨调用污染）。用 ContextVar 而非全局变量：asyncio 任务间天然隔离。

### D6: 抓取目标锁定 `docker-compose.dev.yml` 单实例全栈

**决策**：Prometheus 唯一抓取目标 = `docker-compose.dev.yml`；`docker-compose.complaint.yml` 双实例（8006/8007）抓取列入 Non-Goals。

**理由**：dev.yml 单实例 complaint-service 占 8006，complaint.yml 双实例 a/b 占 8006/8007，**端口冲突**导致两个 compose 不能同时启动；当前生产场景以 dev.yml 全栈联调为主，双实例是灰度场景，另开 change 处理。

### D7: Grafana provisioning 内置 dashboard

**决策**：通过 `deploy/grafana/provisioning/{datasources,dashboards}` 自动装配，datasource URL 用 `http://prometheus:9090`（同一 compose 网络内服务名解析）；dashboard JSON 文件入仓，包含「服务 RED」与「LLM 用量」两个 section。

**理由**：provisioned dashboard 可版本化、可评审、可用 Git diff 追踪变化；手工在 UI 里配的 dashboard 容器重建即丢。

## Risks / Trade-offs

- **`prometheus-client` 镜像漂移**：根 `requirements.lock` 当前不含该包，仅 mcp-gateway 服务级 requirements 声明（`prometheus-client>=0.19,<1`）；rag/qc/data-platform/call/case/review 无服务级 requirements 文件、镜像只装根 lock——若不在根 lock 补齐，7 个服务容器启动即 ImportError，且**重启后才暴露** → 强制 tasks 1.0 先行（根 `requirements.in` 追加 + 重新生成 lock + 镜像重建后 `docker exec pip list` 验证），版本与 mcp-gateway 声明保持一致；mcp-gateway 服务级声明保留不动（冗余无害）。
- **`host.docker.internal` 在 Linux 需 `extra_hosts: host.docker.internal:host-gateway`** → 已在 prometheus 服务配置中显式声明，compose 文件注释注明 macOS 原生支持、Linux 兼容。
- **mcp-gateway 指标重命名导致既有测试断言失败** → 允许最小更新（仅替换指标名，不断言业务逻辑变化），在 tasks.md 单列一个任务跟踪。
- **`token_source=missing` 时 `llm_tokens_total` 不增长，可能让 dashboard 看起来"token 吞吐为 0"** → dashboard 面板加 `token_source` 标签分组说明，并在 design 中明确这是已知口径，待后续 transport 改造批次补齐。
- **8 个服务全部接入 `mount_metrics` 可能引入性能开销** → 纯 ASGI 中间件开销极小（仅在请求完成时打 3 个指标），且 mcp-gateway 生产已用同款实现无性能问题，可接受。
- **`_llm_last_usage` ContextVar 在异步任务隔离失效时可能跨调用污染** → `invoke_llm` 进入时显式 `reset`，`finally` 块中读取并清零（覆盖成功与失败/异常路径），双保险。
- **Grafana dashboard JSON 手写易错** → 先手工在 Grafana UI 里拼好面板再 export JSON 入仓，或直接用 code 生成；tasks.md 给出最低可验收的面板清单，不追求美观。

## Migration Plan

1. **阶段一**：新增 `common/observability/` 模块与单测，不改任何服务——验证纯 ASGI 中间件与 mount_metrics 在 pytest 环境可用。
2. **阶段二**：mcp-gateway 迁移到 common 帮助函数，同步最小更新测试断言，跑 mcp-gateway 单测全绿。
3. **阶段三**：其余 7 个服务接入 `mount_metrics`，逐一启动验证 `/metrics` 可达。
4. **阶段四**：修改 `common/llm/transport.py` 与 `common/llm/reliability.py` 埋点，跑 common 与三服务单测全绿。
5. **阶段五**：新增 `deploy/prometheus/` 与 `deploy/grafana/provisioning/`，改主 `docker-compose.yml`，启动全栈做 V1~V5 验收。
6. **回滚**：所有改动均为纯增量，回滚 = `git revert`；mcp-gateway 指标名回滚需同步还原测试断言。

## Open Questions

（无。所有决策点均已在本 design 中闭环。）
