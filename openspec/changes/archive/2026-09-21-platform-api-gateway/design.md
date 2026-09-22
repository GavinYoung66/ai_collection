## Context

各后端服务以独立容器/进程暴露在宿主端口（complaint 8006、qc 8003、rag 8002、Gradio 7860）。两个演示页 `services/{complaint-service,qc-service}/web/index.html` 是纯静态页，通过页面内 `API_BASE` 输入框调用后端，默认值写死了 8006/8003。Gradio 是第三个前端，其到 rag-service 的调用发生在容器网络内的服务端（`RAG_API_BASE=http://rag-service:8000/api/v1`），与浏览器无关。本次以后端业务逻辑零改动为约束引入北向网关；唯一例外是 Gradio 启动入口需约 1 行 `root_path` 参数适配（见 D2），不触碰任何业务代码。

## Goals / Non-Goals

**Goals:**
- 浏览器侧只访问一个 origin（:8080）即可使用三个前端与其后端 API
- 网关独立容器、独立 compose，可单独启停/回滚，不改动 `docker-compose.dev.yml` 的服务定义
- 统一限流、CORS、trace 透传，upstream 舱壁隔离
- 支撑单条 cpolar 隧道远程演示整套系统

**Non-Goals:**
- 不做 JWT/auth_request 鉴权、不做 HTTPS/证书（后续上服务器时另开 change）
- 不做 MCP 独立网关（P3）与 LLM 网关（P4）
- 不改任何后端业务逻辑 Python 代码（研判、质检、检索/入库等）；唯一允许的 Python 改动是 Gradio 启动入口约 1 行 `root_path` 参数适配；不强制服务间内部调用绕行网关
- 不做按用户/租户的差异化限流，仅按来源 IP 的演示级限流
- 不做访问日志挂卷持久化、不接 Prometheus/Grafana/告警系统（演示/开发阶段容器日志即可排障，可观测体系后续按需引入）

## Decisions

### D1：容器网络采用 host.docker.internal，不改 dev compose
网关通过 `host.docker.internal:<宿主端口>` 访问已映射到宿主的各服务，compose 中加 `extra_hosts: host.docker.internal:host-gateway` 兼容 Linux。
- 备选：并入 dev compose 网络用容器名 `rag-service:8000`。更"正宗"，但要改动现有 compose 与多个网络，违背零改动与可独立回滚目标；后续可平滑迁移。
- 性能与迁移：该方案流量路径为容器→`host.docker.internal`→宿主端口映射→目标容器，多一次宿主网络绕行；演示/开发场景性能可接受（本地回环延迟可忽略）。生产环境应迁回容器内网直连（用容器名/内部网络通信，去掉宿主端口绕行），届时合并入 dev compose 网络或独立 compose network。

### D2：Gradio 以子路径 `/rag/` 接入，而非根路径
网关用前缀路由 + WebSocket/Upgrade + `proxy_buffering off` 反代到 7860。根路径 `/` 留给三入口导航页。

子路径必须同时具备两处改动，缺一不可（已核对 `services/rag-service/gradio/app.py:990-995`：现有 `demo.launch()` 仅读取 `GRADIO_SERVER_NAME/PORT`，未传 `root_path`）：
1. `docker-compose.yml` gradio 增加环境变量 `GRADIO_ROOT_PATH=/rag`
2. `demo.launch()` 增加约 1 行 `root_path=os.environ.get("GRADIO_ROOT_PATH", None)`——否则只加环境变量无效，子路径资源仍 404

该 1 行属于前端启动入口的部署适配，不涉及检索等业务逻辑。
- 备选：Gradio 占根路径、导航页放别处。会挤压投诉/质检页入口，且单隧道演示缺少统一着陆页。

### D3：静态演示页由网关同源托管
只读挂载两个 `web/` 目录到 `/complaint/`、`/qc/`，新增极简 `www/index.html` 导航页；两个 index.html 默认 `API_BASE` 改为相对路径 `/api/v1`（输入框保留，可手改）。
- 备选：让用户用 file:// 直接打开页面。无法同源、仍需手填端口，达不到单 origin 目标。

### D4：限流超限返回 429 + 统一信封而非默认 503 HTML
Nginx `limit_req` 默认对超限返回 503，与项目 `B0006`（限流）语义对应的 429 不一致，会让验收二义。故显式 `limit_req_status 429` 钉死状态码，再以 `error_page 429` 转自定义 JSON：`{"code":"B0006",...}` + `Retry-After`，与项目统一响应信封/错误码体系一致。阈值固定 20 r/s、burst 40 nodelay，集中写在配置顶部便于后续调整。

### D5：Trace 透传优先、缺失注入
入口用 `map`/变量判断 `$http_x_trace_id`，为空时生成请求标识并通过 `proxy_set_header X-Trace-Id` 注入；访问日志记录该标识、status、request_time、upstream_response_time。

### D6：超时分级
`proxy_connect_timeout 3s`（快速暴露死实例）、`proxy_read_timeout 120s`（容忍 LLM/流式长响应），避免用单一短超时误杀长请求。

已核实代码确认 `POST /api/v1/kb/documents`（文档上传）为异步入库：`app/api/documents.py:58-116` 的 `upload_document` 在校验通过后调用 `schedule_background_ingest()`，该函数经 `asyncio.create_task()` 调度后台任务（`app/services/ingest_service.py:125-135`），立即返回 `{"status":"processing"}`；解析、嵌入、Milvus/ES 写入均在后台 `run_background_ingest_job` 中完成，不在 HTTP 请求内阻塞。`save_upload_to_staging`（`app/api/deps.py:226`）仅做文件落盘 + 大小校验，无重处理。故 `proxy_read_timeout 120s` 对该路由足够，无需单独放宽 per-route 超时。

### D7：请求体上限放行到 100MB，业务大小仍由后端裁决
已核实 rag-service 上传校验：`KB_MAX_FILE_SIZE` 默认 20MB（`app/rag_settings.py`、Dockerfile ENV 一致），超限时在解析/落盘前抛业务错误 B0001。网关若沿用 Nginx 默认 1MB 上限，会在请求到达后端前直接 413，破坏"经网关与直连一致"。
- 决策：网关 `client_max_body_size 100m`（http 级统一），作为宽松外层边界，容纳 multipart 编码开销并覆盖现有素材体积；20MB 的业务合法性仍由后端 B0001 裁决。
- 取舍：超过 100MB 的请求网关仍会 413（与直连的 B0001 不同），但本系统素材远小于该值，且后端本就拒绝 >20MB，该差异在业务范围内不可达；集中配置，未来需要可调整。
- 备选：网关精确设 20m。因 multipart 边界/表单开销会使整包略大于净文件大小，临界请求可能被网关先 413，反而制造不一致，故不取。

### D8：OPTIONS 预检在网关层闭环，不透传后端
预检（`OPTIONS`）是浏览器跨域协议握手，与业务无关；后端（FastAPI 等）默认不注册 OPTIONS 路由，透传会触发 405。故在网关用 `if ($request_method = OPTIONS)` 分支直接返回预检响应，不进入 `proxy_pass`。正式请求（GET/POST 等）才转发后端。
- 备选：后端统一注册 OPTIONS 路由。增加后端改动，违背"后端业务逻辑零改动"约束，且预检与业务无关本不该进后端。
- 凭证限制：本期 CORS 用 `Access-Control-Allow-Origin *`，不携带 cookie（`Access-Control-Allow-Credentials` 不设或设 false）；未来若需带凭证的跨域请求，MUST 回显具体 Origin 且 MUST NOT 用 `*`（浏览器规范禁止 `*` + credentials 组合），届时另开 change。

### D9：proxy_pass 不带尾路径，保留原始 URI
Nginx `proxy_pass` 行为：带尾路径（含 `/`）会触发 URI 改写（按 location 前缀替换剥离），不带尾路径则原样透传 `$request_uri`。本网关须把 `/api/v1/complaints/...`、`/api/v1/qc/...` 等完整路径透传给后端（后端路由注册在 `/api/v1/...`），故各 location 统一用 `proxy_pass http://<upstream>;`（分号前无 `/` 或路径），MUST NOT 写成 `proxy_pass http://<upstream>/;`，否则 `/api/v1/qc/rules/versions` 会被剥离成 `/rules/versions` 导致后端 404。
- 备选：`proxy_pass http://<upstream>$request_uri;` 或 `rewrite`。等效但多此一举，且易与 `location` 尾斜杠交互产生边界 case。

### D10：网关层 5xx 统一信封，复用项目错误码 B0008/B0009
已核实项目错误码体系（`common/errors/codes.py`）：`B0008`=网关错误、`B0009`=服务暂不可用，正好覆盖网关层 5xx。故用 `error_page 502 504` 转自定义 JSON：502→`code=B0008`、504→`code=B0009`，并附 `trace_id`，与限流（429→B0006）一样不返回 Nginx 默认 HTML。后端业务 5xx（如 rag-service 自身抛 500）会原样透传，不在网关改写。
- 备选：网关统一返回 B0007（系统内部错误）。无法区分 upstream 不可达（502/504）与服务端错误（500），不利于排障，故按 HTTP 语义细分 B0008/B0009。

## Risks / Trade-offs

- [host.docker.internal 在个别 Linux 环境解析异常] → 已通过 `extra_hosts: host-gateway` 兜底；文档注明 Linux 要求
- [Gradio 子路径资源/重定向未全部走 ROOT_PATH 导致 404] → 以 `/rag/` 完整走查页面与队列/SSE；必要时补 `proxy_redirect` 与子请求头
- [location 尾斜杠不一致造成 404/301] → 路由表逐条 curl 验证，前缀与服务端实际路由（`/api/v1/...`）严格对齐
- [限流误伤正常批量操作] → burst 40 提供缓冲；阈值集中配置可快速调整，演示场景人工操作远低于阈值
- [cpolar 穿透后限流 key 共享] → 经 cpolar 隧道访问时，所有外部请求的来源 IP 均为穿透节点 IP，按 IP 的 20 r/s 变为全员共享额度（单用户可用带宽下降），且未处理 `X-Forwarded-For`；本期不改限流 key 逻辑（演示场景人工操作远低于阈值，可接受）。后续正式部署时用 `set_real_ip_from <代理/穿透段>` + `real_ip_header X-Forwarded-For` 从代理头提取真实客户端 IP，恢复 per-client 限流（不在本次范围）
- [单网关成为浏览器侧单点] → 演示/开发阶段可接受；`restart: unless-stopped`，且网关纯无状态、重启秒级恢复；生产可加多副本（不在本次范围）
- [静态页内仍可能残留其他硬编码地址] → 以网络面板验收，确认运行时无 8006/8003 请求

## Migration Plan

1. 仅新增网关文件、少量前端默认值、gradio 环境变量与启动入口 1 行 `root_path` 适配，不触碰任何后端业务逻辑，无需数据迁移
2. 先独立起网关（`docker compose -f docker-compose.gateway.yml up -d`），与现有直连方式并存，可随时切回直连端口验证
3. 回滚：停止网关容器即可，各服务直连端口不受影响；两个 index.html 默认值改动不影响手填地址（由 tasks 5.5 显式验证：停网关后 8006/8003/8002/7860 直连仍可访问，再 `up -d` 恢复）
4. 远程演示：确认本地 8080 全链路后，`cpolar http 8080` 暴露单隧道

## Open Questions

- 未来上服务器后的正式域名与 HTTPS 证书方案（Certbot）——不影响本次本地/演示实现，部署阶段再定
