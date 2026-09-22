# AI 电话催收生态赋能系统

> 规范驱动开发（Specification-Driven Development）项目，规划 7 个 AI 子系统。  
> **实现深度（求职/Demo 口径）：2 个深切片 + 5 个占位** — `rag-service`、`complaint-service` 已深做；其余服务在对应里程碑前可为 health 脚手架，**不是**七系统业务功能全部完备。

## 技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| 语言/运行时 | Python | 3.11.15 |
| Web 框架 | FastAPI + Uvicorn | 0.104.1 / 0.24.0 |
| ORM/迁移 | SQLAlchemy + Alembic | 2.0.23 / 1.3.0 |
| 数据库 | PostgreSQL | 15 |
| 缓存 | Redis | 7 |
| 向量库 | Milvus + BGE-M3 | 2.4 / 1024 维 |
| 稀疏检索 | Elasticsearch | 8 |
| 重排序 | bge-reranker (rerank-service:8101) | — |
| AI 编排 | LangChain + LangGraph | 0.1 / 0.0.40 |
| 大模型 | 阿里云 DashScope qwen-plus | — |

## 快速开始

```bash
# 1. 创建 conda 环境
conda create -n ai_collection python=3.11.15
conda activate ai_collection

# 2. 安装依赖
make compile  # 生成 requirements.lock
make install  # 安装依赖

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入真实密钥

# 4. 启动基础设施
make dev

# 5. 数据库迁移
make migrate

# 6. 冒烟测试
make smoke
```

## 7 服务端口

| # | 服务 | 端口 | 里程碑 | 实现深度（当前） |
|---|---|---|---|---|
| 1 | data-platform | 8001 | M1 | 占位 / 脚手架 |
| 2 | rag-service | 8002 | M1 | **深做**（混合检索等） |
| 3 | qc-service | 8003 | M1 | 占位 / 脚手架 |
| 4 | call-service | 8004 | M2 | 占位 / 脚手架 |
| 5 | case-service | 8005 | M2 | 占位 / 脚手架 |
| 6 | complaint-service | 8006 | M3 | **深做**（LangGraph 研判等） |
| 7 | review-service | 8007 | M4 | 占位 / 脚手架 |

辅助服务：embedding-service (8100) / rerank-service (8101) / Elasticsearch (9200)

`common/` = 跨切面（config / security / llm transport·reliability / trace 等），**不含** RAG 产品实现（RAG 在 `services/rag-service`）。新服务接入见 `docs/platform/new-service-checklist.md`。

## API 网关（北向单入口）

独立 Nginx 容器 `ai-api-gateway` 监听宿主 **8080**，只做路径转发与横切治理（限流 / CORS / Trace / 舱壁），不承载业务逻辑。各后端直连端口（8006/8003/8002/7860）仍可独立访问，网关可单独启停。

### 启动

```bash
# 先确认 8080 空闲，且 complaint/qc/rag/gradio 已在运行
lsof -i:8080
docker compose -f docker-compose.gateway.yml up -d
```

停止网关（不影响后端）：`docker compose -f docker-compose.gateway.yml down`

### 路由表

| 网关路径 | 后端 |
|---|---|
| `/api/v1/complaints`、`/api/v1/stats`、`/api/v1/mode` | complaint-service :8006 |
| `/api/v1/qc` | qc-service :8003 |
| `/api/v1/kb`、`/api/v1/sensitive-words` | rag-service :8002 |
| `/rag/` | Gradio :7860 |
| `/complaint/`、`/qc/`、`/` | 演示静态页（同源托管） |
| `/gateway/health` | 网关存活探活 |

### 单 origin 演示

浏览器只访问 `http://localhost:8080`：

- 导航页：`http://localhost:8080/`
- 投诉页：`http://localhost:8080/complaint/`（默认 `API_BASE=/api/v1`）
- 质检页：`http://localhost:8080/qc/`（默认 `API_BASE=/api/v1`）
- 知识库 Gradio：`http://localhost:8080/rag/`

页面请求走同一 origin，不再手填 8006/8003。输入框仍可改回直连地址。

### 远程演示

本地 8080 全链路可用后，用单条隧道暴露整套系统（免费版 cpolar 只提供单隧道）：

```bash
cpolar http 8080
```

Linux 宿主需 Docker 能解析 `host.docker.internal`（compose 已写 `extra_hosts: host.docker.internal:host-gateway`）。

## 目录结构

```
ai_collection/
├── services/          # 7 子系统代码
├── common/            # 公共能力层（9 个包）
├── deploy/            # embedding/rerank Dockerfile + nginx
├── migrations/        # Alembic 迁移
├── scripts/           # smoke 脚本 + seed 数据
├── docs/              # 设计文档归档
├── 标准工程目录结构/  # 规范层（step1~step8）
├── openspec/          # 宪法层（specs）
└── demand/            # 历史参考（只读）
```

## 规范文档索引

| 层级 | 位置 | 职责 |
|---|---|---|
| 宪法层 | openspec/specs/ | 强制红线 |
| 规范层 | 标准工程目录结构/step1~step8 | 详细规格 |
| 参考源 | demand/ | 历史材料（只读） |

## 工程化还没做的事情

P2 API 网关已落地，见上文「API 网关（北向单入口）」。本期不含 JWT 鉴权与 HTTPS（后续上服务器另开 change）。

`mcp-gateway-standalone` P3（call-service 真成 MCP Server 再做） 7 
把现在 嵌在投诉服务进程里 的 MCP 调用逻辑，拆成一个 独立运行的容器服务 。
作用            说明 
进程隔离       MCP 网关挂了不拖垮投诉服务，反之亦然 
统一鉴权审计    所有 MCP 调用经过一个地方，鉴权/日志/限流集中做 
多服务复用     投诉、质检都能调同一个 MCP 网关，不用各自内嵌一套 
独立扩缩容     MCP 调用量大时，单独给网关加副本，不用扩投诉服务

`queue-stream-upgrade` P5（P1 真丢任务后再做）
把现在的 简单 Redis List 队列 升级为 Redis Stream 消费组 ，任务不丢, 多 worker 竞争, 失败重试, 死信队列,对账补偿, 补上死信和对账。

能力                  简单 List              Redis Stream 
任务不丢            ❌ pop 走就没了             ✅ 要 ACK 才删 
多 worker 竞争      ❌ 自己实现              ✅ 消费组原生支持 
失败重试            ❌ 没有                  ✅ 自动重试 
死信队列            ❌ 没有                   ✅ 3 次失败入 DLQ 
对账补偿            ❌ 没有                  ✅ 每日扫描未 ACK 任务  


P2 API 网关 ✅ 已落地（见上文「API 网关」）
P3 MCP 独立网关 ⚪ 待做 
P4 LLM 独立网关 ⚪ 默认跳过 
队列升级 ⚪ 待做