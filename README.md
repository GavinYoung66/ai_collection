# AI 电话催收生态赋能系统

> 规范驱动开发（Specification-Driven Development）项目。
> **实现深度口径：数据中台 / 知识库 / 全量质检 / 投诉研判四服务真实事件闭环已跑通**；其余服务在对应里程碑前为 health 脚手架，不是七系统业务功能全部完备。

## 1. 项目定位

面向**贷后催收场景**。传统催收四个痛点：人力成本高、合规风险不可控、质检只能抽样、策略靠人工经验。

技术路线：**大模型 + RAG + 多智能体编排**，拆为七个独立微服务，覆盖数据中台、知识库、全量质检、智能外呼、分案、投诉研判、复盘的完整业务闭环。核心原则：**AI 只出草稿不做最终判定，所有输出经人工审核**。

| 类别 | 选型 | 版本 |
|---|---|---|
| 语言/运行时 | Python | 3.11.15 |
| Web 框架 | FastAPI + Uvicorn | 0.104.1 / 0.24.0 |
| ORM/迁移 | SQLAlchemy + Alembic | 2.0.23 / 1.3.0 |
| 数据库 | PostgreSQL / Redis | 15 / 7 |
| 向量库 / 稀疏检索 | Milvus (BGE-M3 1024 维) / Elasticsearch | 2.4 / 8 |
| AI 编排 | LangChain + LangGraph | 0.1 / 0.0.40 |
| 大模型 | 阿里云 DashScope qwen-plus | — |
| 事件总线 | RocketMQ（唯一模式，事件信封白名单） | 5.x |

## 2. 整体业务流程（事件闭环）

```
dp 种子入库
  └─ etl.task.done ──→ qc-service 开检（R1~R14 规则 + LLM 复核 + RAG 检索）
                          └─ qc.check.completed ──→ dp 消费回写快照质检列
                                                        └─ complaint.created（仅 case_id）
                                                              └─ complaint-service 幂等建单
                                                                    → LangGraph 研判（证据包三桥反查
                                                                      → reflection 判定 → human_review 断点）
                                                                    → approve 后 write_session 回写快照投诉列
                                                                    → complaint.result.ready ──→ dp 确认
旁路：rag 敏感词变更 ── kb.sensitive_updated ──→ qc 词表热刷新（≤60s，version 轮询兜底）
```

两条铁律：**跨服务只走事件总线**（通知式，事件只带 ID 与 trace_id，订阅方回查明细，禁止 HTTP 直推业务事件）；**智能体取证只走 MCP**（同步请求-响应，下游变更对 Agent 透明）。

## 3. 整体架构（三层）

```
┌─ 接入层：Nginx 统一网关 :8080（路径转发 + 限流/CORS/Trace/舱壁）
├─ 微服务层：7 个服务（独立容器 / 独立端口 / 独立 schema）
└─ 公共底座：common/（LLM 编排、限流熔断降级重试、脱敏加密、追踪、事件总线、错误码）
```

| # | 服务 | 端口 | 实现深度 | 内部要点 |
|---|---|---|---|---|
| 1 | data-platform | 8001 | 闭环参与 | 种子/ETL、快照两路回流、`etl.task.done`/`complaint.created` 发布、`qc.check.completed`/`complaint.result.ready` 订阅 |
| 2 | rag-service | 8002 | **深做** | ES BM25 + Milvus 混合检索 + rerank(8101)；敏感词版本轮询 + 总线热刷新 |
| 3 | qc-service | 8003 | 闭环参与 | R1~R14 规则 + LLM 复核；六出站/四入站全上总线；`alert:stream` 仅进程内 XADD |
| 4 | call-service | 8004 | 脚手架（M2） | — |
| 5 | case-service | 8005 | 脚手架（M2） | — |
| 6 | complaint-service | 8006 | **深做** | LangGraph 多节点图 + MemorySaver 断点续跑；三 MCP 桥（案件/通话质检/回流）；双入口（事件必经 judge、HTTP 只建单）；SIM_AUTO_REVIEW 仿真开关 |
| 7 | review-service | 8007 | 脚手架（M4） | — |

辅助服务：embedding(8100) / rerank(8101) / ES(9200) / Gradio 演示(7860)。

`common/` = 跨切面公共底座，**不含**业务实现。新服务接入见 `docs/platform/new-service-checklist.md`。

## 4. API 网关（北向单入口）

独立 Nginx 容器 `ai-api-gateway` 监听 **8080**，只做路径转发与横切治理，不承载业务逻辑。

| 网关路径 | 后端 |
|---|---|
| `/api/v1/complaints`、`/api/v1/stats`、`/api/v1/mode` | complaint-service :8006 |
| `/api/v1/qc` | qc-service :8003 |
| `/api/v1/kb`、`/api/v1/sensitive-words` | rag-service :8002 |
| `/rag/` | Gradio :7860 |
| `/complaint/`、`/qc/`、`/` | 演示静态页（同源托管） |
| `/gateway/health` | 网关存活探活 |

```bash
lsof -i:8080                                              # 确认空闲
docker compose -f docker-compose.gateway.yml up -d        # 启动（后端须已运行）
docker compose -f docker-compose.gateway.yml down         # 停止（不影响后端）
```

单 origin 演示：浏览器只访问 `http://localhost:8080`（导航页 `/`、投诉 `/complaint/`、质检 `/qc/`、知识库 `/rag/`）。远程演示：`cpolar http 8080`（Linux 宿主已由 compose 写 `extra_hosts: host-gateway`）。

## 5. 工程化公共零件（common/）

| 能力 | 机制 |
|---|---|
| LLM 统一编排 | 选 Key → 限流 → 熔断（closed/open/half-open，按服务+资源维度 key）→ 有界重试（业务错才重试，超时不重试）→ fallback 换模型 → 降级 |
| 超时 | 三级层级：MCP total 30s < LLM total 120s < 投诉全局 30min |
| 并发 | 五维策略：工单接入 / MCP 调用 / LLM QPS / 高量缓冲 / 单工单锁 |
| 统一响应 | `{"code","message","data","trace_id"}`；统一错误码（A5xxx 等） |
| 数据安全 | PII（姓名/手机号/身份证/银行卡）出智能体前脱敏；CoT 推理字段 AES-256-GCM 加密，双授权管理员可解密 |
| 链路追踪 | 本地 P0 + LangSmith/Langfuse P1 解耦，统一入口 gate，span 名与业务动作一致，环境变量显式清理防 SDK 绕过 |
| 事件总线 | RocketMQ 唯一模式；信封白名单（`case_id`/`session_id`/`trace_id`）；消费组 `gid_{svc}_{channel}`；进程内 hooks（`csmp.*`）走观测轨不上业务 Topic |
| 质量红线 | L4 后合规验证（幻觉检测 / 规则源验证 / 红线拦截）；规则与案例输出必带 RAG 源 ID |

## 6. 快速开始

```bash
conda create -n ai_collection python=3.11.15 && conda activate ai_collection
make compile && make install      # 依赖
cp .env.example .env              # 填真实密钥
make dev && make migrate          # 基础设施 + 迁移
make smoke                        # 冒烟
```

## 7. 目录与规范

```
ai_collection/
├── services/          # 7 子系统代码
├── common/            # 公共能力层
├── deploy/            # embedding/rerank Dockerfile + nginx
├── migrations/        # Alembic 迁移
├── scripts/           # smoke 脚本 + seed 数据
├── docs/              # 设计文档归档
├── 标准工程目录结构/  # 规范层（step1~step8）
├── openspec/          # 宪法层（specs + changes）
└── demand/            # 历史参考（只读）
```

| 层级 | 位置 | 职责 |
|---|---|---|
| 宪法层 | openspec/specs/ | 强制红线（变更经 changes/ 流程，归档同步主 spec） |
| 规范层 | 标准工程目录结构/step1~step8 | 详细规格 |
| 参考源 | demand/ | 历史材料（只读） |

## 8. 工程化待办

- **P3 MCP 独立网关**：把嵌在投诉服务进程内的 MCP 调用拆成独立容器（进程隔离 / 统一鉴权审计 / 多服务复用 / 独立扩缩容），待 call-service 真成 MCP Server 再做
- **P4 LLM 独立网关**：默认跳过
- **P5 队列升级**：Redis List → Redis Stream 消费组（ACK 不丢任务 / 多 worker 竞争 / 失败重试 / DLQ / 对账补偿），待真丢任务后再做
- 本期不含 JWT 鉴权与 HTTPS（上服务器另开 change）
