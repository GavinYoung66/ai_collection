## 1. 网关骨架与容器

- [x] 1.1 创建 `deploy/gateway/nginx.conf`，定义 4 个 upstream（complaint:8006、qc:8003、kb:8002、web:7860，经 host.docker.internal）与独立 keepalive 连接池；`nginx -t` 校验语法通过
- [x] 1.2 创建 `docker-compose.gateway.yml`（nginx:1.27-alpine、容器名 ai-api-gateway、8080:8080、只读挂载配置、extra_hosts host-gateway、restart unless-stopped、healthcheck `wget -qO- http://localhost:8080/gateway/health`）；启动前 `lsof -i:8080`（或等价命令）确认 8080 未被占用，再 `docker compose -f docker-compose.gateway.yml up -d`，容器处于 running 且 health 状态为 healthy（`docker inspect` 可见）

## 2. 路由与静态托管

- [x] 2.1 配置路径路由（前缀不带尾斜杠：complaints/stats/mode→8006，qc→8003，kb 与 sensitive-words→8002），各 location 统一用 `proxy_pass http://<upstream>;`（不带尾路径，保留原始 URI，禁止 `proxy_pass http://<upstream>/;`，否则 URI 前缀被剥离致后端 404）；curl 验证三条核心路由及无尾斜杠根路径 `POST /api/v1/complaints` 均返回与直连一致的响应信封、不 404，并断言经网关与直连后端收到的 URI 一致
- [x] 2.2 只读挂载两个 `web/` 目录到 `/complaint/`、`/qc/`，新增 `deploy/gateway/www/index.html` 三入口导航页；浏览器访问 `/`、`/complaint/`、`/qc/` 均可打开
- [x] 2.3 配置 `/gateway/health` 返回 200 存活标识；curl 验证返回存活 JSON
- [x] 2.4a Gradio root_path 适配：`docker-compose.yml` gradio 增加 `GRADIO_ROOT_PATH=/rag`，并在 `services/rag-service/gradio/app.py` 的 `demo.launch()` 增加 `root_path=os.environ.get("GRADIO_ROOT_PATH", None)`（约 1 行，现有 launch 未读取该变量，缺此步则子路径 404）；重启 gradio 容器后日志/启动参数确认 root_path 已读取生效（独立于网关，可先于 2.4b 验证）
- [x] 2.4b 网关 `/rag/` 反代 7860 并启用 Upgrade/WebSocket（`proxy_http_version 1.1` + `Upgrade`/`Connection` 头透传、`proxy_buffering off`）；浏览器访问 `:8080/rag/` 页面与静态资源无 404，流式/WebSocket 连接稳定（依赖 2.4a 完成后联调）
- [x] 2.5 配置 http 级 `client_max_body_size 100m`；用 `curl -F file=@<数MB文件> http://localhost:8080/api/v1/kb/documents` 验证经网关不返回 413、结果与直连 8002 一致；再用 21MB 文件验证经网关得到后端 B0001 信封（而非网关 413 HTML）

## 3. 横切治理

- [x] 3.1 配置全局限流（按 IP 20 r/s、burst 40 nodelay）与 `limit_req_status 429` + 自定义 `error_page 429`，超限输出统一信封 JSON（code=B0006 + Retry-After）；连发 50 次验证超限返回 JSON 而非默认 HTML，并断言 HTTP 状态码=429（非 Nginx `limit_req` 默认 503）
- [x] 3.2 配置 CORS 与 OPTIONS 预检（允许头含 Authorization/Content-Type/X-Trace-Id/X-User-Id），OPTIONS 由网关直接响应（不 `proxy_pass` 到后端）；`curl -X OPTIONS` 验证预检通过，并断言 OPTIONS 不到达后端（后端访问日志无 OPTIONS 记录）
- [x] 3.3 配置 X-Trace-Id 透传/缺失注入，访问日志记录 trace_id、status、request_time、upstream_response_time；带与不带 header 各请求一次，下游与网关日志均能看到正确标识
- [x] 3.4 配置流式透传（proxy_buffering off、Upgrade/Connection 头）与超时分级（connect 3s / read 120s）；经 `/rag/` 使用流式输出验证不中断、不缓冲

## 4. 前端同源改造

- [x] 4.1 将 `services/complaint-service/web/index.html` 默认 API_BASE 改为同源相对路径 `/api/v1`（保留输入框可手改）；经网关打开页面，网络面板请求落在同源 :8080 且无 8006 硬编码
- [x] 4.2 将 `services/qc-service/web/index.html` 默认 API_BASE 改为 `/api/v1`；经网关完成一次质检查询，网络面板无 8003 硬编码

## 5. 集成验证与文档

- [x] 5.1 舱壁验证：停掉 rag-service 后 `/api/v1/complaints` 仍返回 200，仅 kb 路由返回网关层错误（502/504），并断言 504 响应体为统一 JSON 信封（`code=B0009` + `trace_id`）而非 Nginx 默认 HTML；恢复 rag 后 kb 路由自动恢复
- [x] 5.2 端到端走查：浏览器仅用 :8080 完成投诉页、质检页、Gradio 三入口访问与各自核心操作；记录结果
- [ ] 5.3 运行现有后端单测确认零回归（后端代码未改，测试应全绿）
- [x] 5.4 更新 README 网关章节：启动命令、路由表、单 origin 演示方式与 `cpolar http 8080` 远程演示步骤
- [x] 5.5 回滚/新旧并存验证：`docker compose -f docker-compose.gateway.yml down` 停网关容器后，直连端口 8006/8003/8002/7860 仍可独立访问（curl 各端口健康检查返回 200，各后端服务不受影响）；再次 `up -d` 网关恢复，确认网关入口与直连端口两种访问方式可随时切换
