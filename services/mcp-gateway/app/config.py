"""mcp-gateway 配置。

- 从环境变量读取（前缀 MCP_GATEWAY_），提供合理默认值，禁止硬编码；
- 下游地址默认值为 docker network 服务名（容器内禁止 host.docker.internal）；
  宿主机本地调测时经 .env / 环境变量覆盖为 http://localhost:<映射端口>；
- 与任务 8.2 的 .env 变量对齐：MCP_GATEWAY_ENABLE_AUTH / MCP_GATEWAY_AUTH_API_KEY。
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    # env_prefix + env_file 对齐 common/config/settings.py 约定：根 .env 为单一事实源，
    # 本地调测从项目根运行时读取 .env（如 MCP_GATEWAY_SERVICE_NAME）；容器部署经 compose
    # env_file: .env 注入同名环境变量，二者同源不漂移。
    # extra="ignore" 必须显式开启：env_prefix 对 .env 文件同样生效，.env 中其它服务
    # 的裸 key（PG_HOST/ENABLE_AUTH 等）不带 MCP_GATEWAY_ 前缀，若 forbid 会启动崩溃。
    model_config = SettingsConfigDict(
        env_prefix="MCP_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务监听：端口 9000 与既有约定 MCP_GATEWAY_URL=http://mcp-gateway:9000 对齐
    HOST: str = "0.0.0.0"
    PORT: int = 9000

    # 运行环境 / 日志
    ENV: str = "dev"
    LOG_LEVEL: str | None = None

    # 鉴权开关（任务 6.x；默认关闭直通）。开启后 POST /mcp 与 GET /metrics 均需
    # Authorization: Bearer <JWT|API Key>，/health 始终放行（见 app/auth.py）
    ENABLE_AUTH: bool = False
    AUTH_API_KEY: str = ""

    # ===== Batch 2：下游服务地址（docker network 服务名为默认值）=====
    # rag-service：rag_kb 检索（容器内 8000；宿主机映射 8002）
    RAG_SERVICE_URL: str = "http://rag-service:8000"
    # data-platform：business_ticket 快照读 / data_platform 回写 / call_qc qc-source
    # （容器内 8000；宿主机映射 8001）
    DATA_PLATFORM_URL: str = "http://data-platform:8000"
    # qc-service：call_qc MCP server endpoint /mcp（容器内 8000；宿主机映射 8003）
    QC_SERVICE_URL: str = "http://qc-service:8000"

    # 桥 HTTP 超时（秒）：快照/回写等内部快接口
    BRIDGE_HTTP_TIMEOUT: float = 5.0
    # rag 检索超时（秒）：检索含 ES+向量+rerank，源桥为 30s
    RAG_SEARCH_TIMEOUT: float = 30.0

    # ===== Batch 3：可靠性层 =====
    # 服务名：熔断/限流 key 派生前缀（{svc}:mcp_degraded:{server} / {svc}:mcp_ratelimit:{server}）。
    # 对齐 spec 键型 csmp:mcp_degraded:{server}；启动时经 set_default_service 注入
    # common.llm.reliability（resolve_service_name 无配置会 fail-fast，此处显式接线）。
    SERVICE_NAME: str = "csmp"
    # per-server 限流窗口（秒）与 QPS 覆盖（JSON，如 {"rag_kb": 10}）；
    # 缺省值对齐 complaint-service 维3 配置（rag_kb=20/business_ticket=50/call_qc=30/data_platform=30）
    MCP_RATE_LIMIT_WINDOW: int = 1
    MCP_RATE_LIMIT_QPS: str = ""


settings = GatewaySettings()
