## Why

7 个 AI 子系统当前裸暴露多个端口（complaint:8006 / qc:8003 / rag:8002 / gradio:7860 等），前端写死多个 origin（投诉页默认 `http://localhost:8006`、质检页默认 `http://localhost:8003`），跨域需各服务单独配；cpolar 免费版只提供单隧道，无法支撑远程演示多端口；北向流量也没有统一的限流、X-Trace-Id 注入与 upstream 故障隔离入口。需要一个独立 Nginx 反向代理作为唯一北向入口，只做转发与横切治理，不承载业务逻辑。

## What Changes

- 新增独立 Nginx 容器（`ai-api-gateway`，宿主端口 8080），作为浏览器/演示侧唯一入口，独立 compose 文件部署，不改写 `docker-compose.dev.yml`
- 按 URL 前缀路径反代：`/api/v1/complaints|stats|mode` → 8006；`/api/v1/qc` → 8003；`/api/v1/kb` 与 `/api/v1/sensitive-words` → 8002；`/rag/` → Gradio 7860
- upstream 舱壁隔离：每个被代理服务独立连接池与 keepalive，单服务故障不连累其他路由；网关层 5xx（upstream 连接拒绝→502、读超时→504）返回统一 JSON 信封（`code=B0008`/`B0009` + `trace_id`），不返回 Nginx 默认 HTML
- 全局限流：按来源 IP 固定阈值 20 r/s、burst 40 nodelay；超限返回 HTTP 429（显式 `limit_req_status 429`，覆盖 Nginx 默认 503）与统一信封 JSON（`code=B0006` + Retry-After），不返回 Nginx 默认 HTML 错误页
- CORS：允许同源/跨域演示访问并正确响应 OPTIONS 预检（`OPTIONS` 由网关直接响应，不透传后端，避免后端未实现 OPTIONS 返回 405）
- Trace：请求带 `X-Trace-Id` 透传，缺失时网关注入并透传给下游
- 流式透传：Gradio 路由支持 WebSocket/Upgrade 且关闭 proxy_buffering，SSE/WS 长连接不被缓冲或误杀；超时分级 connect 3s / read 120s
- 大文件上传放行：网关请求体上限设为 100MB（覆盖 Nginx 默认 1MB 限制），多模态文件（PPTX/图片/音频）经网关不被 413 拦截；20MB 业务大小上限仍由 rag-service 后端按 B0001 裁决
- 同源静态托管：网关托管投诉/QC 两个演示页与一个导航页；两个 `web/index.html` 默认 API_BASE 改为同源相对路径 `/api/v1`（输入框保留可手改）
- Gradio 适配子路径：`docker-compose.yml` 的 gradio 服务增加 `GRADIO_ROOT_PATH=/rag` 环境变量，并在 `gradio/app.py` 的 `demo.launch()` 增加约 1 行 `root_path=os.environ.get("GRADIO_ROOT_PATH", None)` 启动参数适配（现有 launch 未读取该变量，仅加环境变量不生效）；该改动仅为前端入口的子路径适配，不涉及任何业务逻辑

## Capabilities

### New Capabilities

- `platform/api-gateway`: 北向统一 HTTP 入口能力。单入口路径反代、upstream 舱壁隔离、全局限流（统一信封 B0006）、CORS 预检、X-Trace-Id 透传/注入、流式长连接透传、静态演示页同源托管；仅转发，不解析业务 payload、不实现任何业务逻辑。

### Modified Capabilities

（无。本次不修改任何现有 capability 的需求。后端业务逻辑 Python 代码零改动；唯一 Python 改动是 Gradio 启动入口约 1 行 `root_path` 参数适配，不属于业务逻辑。）

## Impact

- **新增文件**：`deploy/gateway/nginx.conf`、`docker-compose.gateway.yml`、`deploy/gateway/www/index.html`
- **小改配置/前端**：`services/complaint-service/web/index.html`、`services/qc-service/web/index.html` 的默认 API_BASE；`docker-compose.yml` gradio 增加 `GRADIO_ROOT_PATH=/rag`
- **唯一 Python 改动（非业务逻辑）**：`services/rag-service/gradio/app.py` 的 `demo.launch()` 增加约 1 行 `root_path` 启动参数以读取上述环境变量；现有代码未读取该变量，不改则子路径方案不生效
- **不受影响**：全部后端业务逻辑（投诉研判、质检引擎、RAG 检索/入库等）Python 代码与现有单测；服务间容器网络直连（Gradio→rag、qc→rag）保持不变
- **运维影响**：本地与演示环境多一个 8080 入口容器；远程演示可通过单条 cpolar 隧道（`cpolar http 8080`）暴露整套系统
- **明确边界**：不含 JWT 鉴权、HTTPS 证书、MCP 独立网关（P3）、LLM 网关（P4）；访问日志不挂卷持久化、不接 Prometheus/Grafana/告警系统
