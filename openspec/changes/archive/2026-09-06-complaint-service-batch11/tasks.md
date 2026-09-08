# Tasks: complaint-service-batch11（模块13 部署多环境）

依赖：批次0～10 已归档。串行执行：13.1 → 13.2 → 13.3。

## 13.1 Dockerfile（对齐 M0 模板）

- [x] 13.1 检查并增量完善 `services/complaint-service/Dockerfile`：确认基于 `python:3.11.15-slim`、`COPY requirements.lock`、`COPY common/`、`COPY services/complaint-service/app/ ./app/`、`ENV PYTHONPATH=/app`、`CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]`；确保 `curl` 已安装（当前已装）。Dockerfile 内 `HEALTHCHECK` **可选**（compose healthcheck 已覆盖则可不加）。
  **验证**：`docker build -f services/complaint-service/Dockerfile -t complaint-service:local .` 成功；`docker run --rm complaint-service:local ls /app/app/main.py /app/common/config/settings.py` 均存在；`docker run --rm complaint-service:local which curl` 返回路径。

## 13.2 compose（dev 增量 + prod 基线）

- [x] 13.2.1 增量修改仓库根 `docker-compose.dev.yml` 的 `complaint-service` 服务：补 `REDIS_DB=6`、`MCP_GATEWAY_MODE=inline`、`depends_on` 增加 `rag-service: { condition: service_started }`（与 prod **统一**用 started，不强卡 healthy）；保持端口 `8006:8000`、`env_file: .env`、bind mount 热重载。
  **验证**：`docker-compose.dev.yml` 为 override，须与 base 合并：`docker compose -f docker-compose.yml -f docker-compose.dev.yml config --services | grep complaint-service` 正常；`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service` 后 `curl -s http://localhost:8006/health` 返回含 `status` ok。（单独 `-f docker-compose.dev.yml` 会因 redis 等未定义而失败，勿用。）

- [x] 13.2.2 新增仓库根 `docker-compose.complaint.yml`（prod 基线）：用**具名双实例**（**禁止**把 Swarm `deploy.replicas` 当本机验收依据）：
  - `complaint-service-a`：`8006:8000`，`env_file: services/complaint-service/config/prod.env`，`MCP_GATEWAY_MODE=standalone`，`MCP_GATEWAY_URL=http://mcp-gateway:9000`，depends_on postgres+redis（healthy）+ rag-service（**started**）+ mcp-gateway-a/b；资源声明 cpus=4 memory=8g
  - `complaint-service-b`：`8007:8000`，同配置（第二实例）
  - `mcp-gateway-a` / `mcp-gateway-b`：stub，不暴露公网端口，内网 9000，健康检查；资源声明 cpus=2 memory=4g；可选 network alias `mcp-gateway`
  - `postgres`：cpus=4 memory=8g；volume **声明** 100GB（注释：本机可缩小）
  - `redis`：cpus=2 memory=4g；`REDIS_DB=6`
  - `rag-service`：按需声明
  **验证**：`docker compose -f docker-compose.complaint.yml config` 无语法错误；`docker compose -f docker-compose.complaint.yml up -d` 后 `docker compose -f docker-compose.complaint.yml ps` 可见 `complaint-service-a`、`complaint-service-b`、`mcp-gateway-a`、`mcp-gateway-b` 为 running/healthy；`curl -s http://localhost:8006/health` 返回 200。

## 13.3 多环境配置文件

- [x] 13.3.1 新建 `services/complaint-service/config/dev.env`：`ENV=dev`、`MCP_GATEWAY_MODE=inline`、`MCP_GATEWAY_URL=`、`LLM_MODEL_PRIMARY=qwen-turbo`、`USE_MOCK_MCP=true`、`USE_MOCK_LLM=true`、`REDIS_DB=6`、`LOG_LEVEL=DEBUG`、`COMPLAINT_TASK_TIMEOUT=3600`、`LLM_CIRCUIT_BREAKER_THRESHOLD=9999`、`LLM_CIRCUIT_BREAKER_RECOVERY=60`；密钥占位（`change_me`/空）。不写幽灵禁止项字段名。
  **验证**：文件内可 grep 到 `ENV=dev`、`MCP_GATEWAY_MODE=inline`、`LOG_LEVEL=DEBUG`、`COMPLAINT_TASK_TIMEOUT=3600`。

- [x] 13.3.2 新建 `services/complaint-service/config/test.env`：`ENV=test`、`MCP_GATEWAY_MODE=standalone`、`MCP_GATEWAY_URL=http://mcp-gateway:9000`、`LLM_MODEL_PRIMARY=qwen-turbo`、`LLM_MODEL_FALLBACK_1=qwen-plus`、`USE_MOCK_MCP=true`、`USE_MOCK_LLM=true`、`REDIS_DB=6`、`LOG_LEVEL=INFO`、`COMPLAINT_TASK_TIMEOUT=1800`、`LLM_CIRCUIT_BREAKER_THRESHOLD=3`、`LLM_CIRCUIT_BREAKER_RECOVERY=60`。
  **验证**：grep 确认 `MCP_GATEWAY_MODE=standalone` 且 `LLM_MODEL_FALLBACK_1=qwen-plus`。

- [x] 13.3.3 新建 `services/complaint-service/config/pre.env`：`ENV=pre`、`MCP_GATEWAY_MODE=standalone`、`MCP_GATEWAY_URL=http://mcp-gateway:9000`、`LLM_MODEL_PRIMARY=deepseek-r1`、`LLM_MODEL_FALLBACK_1=qwen-plus`、`USE_MOCK_MCP=false`、`USE_MOCK_LLM=false`、`LOG_LEVEL=INFO`、`COMPLAINT_TASK_TIMEOUT=1800`、熔断 THRESHOLD=3；灰度仅文档约定（不写伪造 `GRAY_*` env）。
  **验证**：grep 确认 `LLM_MODEL_PRIMARY=deepseek-r1` 且 `USE_MOCK_LLM=false`。

- [x] 13.3.4 新建 `services/complaint-service/config/prod.env`：`ENV=prod`、`MCP_GATEWAY_MODE=standalone`、`MCP_GATEWAY_URL=http://mcp-gateway:9000`、`LLM_MODEL_PRIMARY=deepseek-r1`、`LLM_MODEL_FALLBACK_1=qwen-plus`、`LLM_MODEL_FALLBACK_2=qwen-turbo`、`USE_MOCK_MCP=false`、`USE_MOCK_LLM=false`、`LOG_LEVEL=INFO`、`COMPLAINT_TASK_TIMEOUT=1800`、`LLM_CIRCUIT_BREAKER_THRESHOLD=3`、`LLM_CIRCUIT_BREAKER_RECOVERY=60`；密钥占位。**禁止项落地**：正向保证 standalone + 熔断开启；**不要**引入 `COT_PLAINTEXT`/`MASKING_DISABLED`/`CIRCUIT_BREAKER_DISABLED`/`USE_UNREVIEWED_PROMPT` 作为「安全开关」写入 true/false 假装生效（可选：apply 时仅新增 `LOG_LEVEL` 接线）。
  **验证**：`grep 'MCP_GATEWAY_MODE=standalone' config/prod.env` 命中；`grep 'LLM_CIRCUIT_BREAKER_THRESHOLD=3' config/prod.env` 命中；`grep -E 'MCP_GATEWAY_MODE=inline|COT_PLAINTEXT=true|MASKING_DISABLED=true|CIRCUIT_BREAKER_DISABLED=true|USE_UNREVIEWED_PROMPT=true' config/prod.env` 无输出；`grep 'LLM_MODEL_FALLBACK_2=qwen-turbo' config/prod.env` 命中。

## 集成验证（13.3 完成后）

- [x] 13.4 端到端冒烟：`docker compose -f docker-compose.complaint.yml up -d` → 等待 healthy → `PORT=8006 bash scripts/smoke/smoke_complaint_service.sh` 退出码 0；或本机降级：`cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH uvicorn app.main:app --port 8006` + `PORT=8006 bash ../../scripts/smoke/smoke_complaint_service.sh`。
  **验证**：冒烟脚本输出 `[PASS]` 全绿，`echo $?` 为 0。（脚本只认 `PORT`，不认 `COMPLAINT_SERVICE_URL`。）

- [x] 13.5 不破坏既有测试：`cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH pytest tests/unit/ tests/integration/ tests/e2e/ tests/regression/ -q` 全部 passed（batch10 测试语义不变）。
  **验证**：pytest 退出码 0，无新失败。（conda `ai_collection` 仅在本步或本机 uvicorn 冒烟需要时启用。）
