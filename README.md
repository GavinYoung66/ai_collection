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

## 8. 完成进度与待办清单（开发进度条）

> 图例：🟢 ✅ 已完成　|　🔴 ❌ 未完成（含部分完成/进行中）　|　工作量为人天粗估

### 8.1 已完成清单（✅）

按维度盘点当前已落地的能力。

#### A. 功能闭环

| # | 项 | 状态 |
|---|---|---|
| 1 | data-platform 闭环参与（种子/ETL/快照两路回流/事件发布订阅） | 🟢 ✅ |
| 2 | rag-service 深做（ES BM25 + Milvus 混合检索 + rerank + 敏感词热刷新） | 🟢 ✅ |
| 3 | qc-service 闭环参与（R1~R14 规则 + LLM 复核 + 六出站/四入站全上总线） | 🟢 ✅ |
| 4 | complaint-service 深做（LangGraph 图 + MemorySaver 断点续跑 + 三 MCP 桥 + 双入口） | 🟢 ✅ |
| 5 | 四服务真实事件闭环（etl.task.done → qc.check.completed → complaint.created → complaint.result.ready） | 🟢 ✅ |
| 6 | 11/11 验收项全 PASS（run_min_loop.py 可重复执行） | 🟢 ✅ |
| 7 | call-service / case-service / review-service 脚手架（health 端口就绪） | 🟢 ✅ |

#### B. 公共底座（common/）

| # | 项 | 状态 |
|---|---|---|
| 8 | LLM 统一编排 invoke_llm（选 Key → 限流 → 熔断 → 有界重试 → fallback → 降级） | 🟢 ✅ |
| 9 | MCP 可靠性层（per-server 熔断 + 30s 超时） | 🟢 ✅ |
| 10 | 事件总线 RocketMQ 唯一模式（信封白名单 + 幂等消费 + hooks 观测轨） | 🟢 ✅ |
| 11 | PII 脱敏 + CoT AES-256-GCM 加密 + 双授权解密 | 🟢 ✅ |
| 12 | 链路追踪（P0 本地 + P1 LangSmith/Langfuse 解耦 + 环境变量显式清理） | 🟢 ✅ |
| 13 | 错误码统一 `{"code","message","data","trace_id"}` + A5xxx | 🟢 ✅ |
| 14 | 鉴权下沉 common/security/authn.py（JWT 验签 + API Key 恒定时间比对） | 🟢 ✅ |
| 15 | 敏感词 AC 自动机匹配 | 🟢 ✅ |
| 16 | 提示词 YAML 配置化（dialog/qc_review/fallback_scripts） | 🟢 ✅ |
| 17 | Pydantic 结构化输出 schema | 🟢 ✅ |
| 18 | 规则兜底话术引擎（LLM 降级） | 🟢 ✅ |

#### C. API 网关（北向单入口，14 项能力）

| # | 项 | 状态 |
|---|---|---|
| 19 | 单入口路径路由（四服务 + Gradio + 静态页同源托管） | 🟢 ✅ |
| 20 | 只做转发不做业务（边界约束） | 🟢 ✅ |
| 21 | upstream 舱壁隔离 + 被动熔断 + 非幂等不重试 | 🟢 ✅ |
| 22 | 全局限流 20r/s + 429 统一信封 | 🟢 ✅ |
| 23 | 流式/长连接透传 + 超时分级（3s/15s/120s） | 🟢 ✅ |
| 24 | 鉴权开关 + auth_request sidecar + 身份透传（ENABLE_AUTH 默认 false） | 🟢 ✅ |
| 25 | CORS 白名单（.env 唯一事实源，禁用 `*`） | 🟢 ✅ |
| 26 | TLS + 安全响应头（X-Frame-Options 等，TLS_ENABLED 开关） | 🟢 ✅ |
| 27 | Trace 标识透传与注入 | 🟢 ✅ |
| 28 | /metrics 指标端点（stub_status + nginx-prometheus-exporter） | 🟢 ✅ |
| 29 | JSON 访问日志（LOG_JSON 开关） | 🟢 ✅ |
| 30 | 灰度路由开关（split_clients + CANARY_WEIGHT，默认 0） | 🟢 ✅ |
| 31 | 共享网络 ai-shared-net（upstream 用容器服务名） | 🟢 ✅ |
| 32 | 配置模板化（nginx.conf.template + envsubst 白名单渲染） | 🟢 ✅ |

#### D. 工程化与部署

| # | 项 | 状态 |
|---|---|---|
| 33 | Docker Compose 编排（dev/gateway/complaint/smoke 四份） | 🟢 ✅ |
| 34 | Alembic 数据库迁移（0001~0014） | 🟢 ✅ |
| 35 | Makefile（dev/test/lint/migrate/smoke） | 🟢 ✅ |
| 36 | CI workflow（complaint-service 单测 + E2E + prompt 回归 + smoke） | 🟢 ✅ |
| 37 | 单测套件（common + 四服务，1402 passed / 0 failed / 3 skipped） | 🟢 ✅ |
| 38 | Locust 压测脚本 + stub 基线准入 | 🟢 ✅ |
| 39 | 冒烟脚本（smoke_all + 各服务 smoke 脚本） | 🟢 ✅ |
| 40 | OpenSpec 规范驱动（specs + changes 归档流程） | 🟢 ✅ |

---

### 8.2 待办进度条

#### 🔴 P0：上生产硬门槛（不做上不了生产）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 1 | MCP 网关独立容器 | 5~8 | 🔴 ❌ | 项目硬性约束要求生产独立容器；当前 complaint-service 是进程内桥（伪 MCP 网关）；待 call-service 真成 MCP Server 再做 |
| 2 | CI 全服务覆盖 | 1 | 🔴 ❌ | 当前 CI 只跑 complaint-service；common/rag/qc/data-platform 改动无门禁 |
| 3 | 密钥管理（vault/sops 二选一） | 2 | 🔴 ❌ | .env 明文存 LLM API Key/PG 密码/JWT secret；对外暴露前必须移出 |
| 4 | 数据库备份脚本（PG/Milvus/ES） | 1 | 🔴 ❌ | 无备份=数据丢了无法恢复 |
| 5 | 服务 /metrics 端点 + Prometheus 抓取配置 + Grafana dashboard | 2~3 | 🔴 ❌ | 当前只有网关有 metrics，业务服务自身不暴露；Prometheus 无抓取配置；无 Grafana |
| 6 | LLM 用量可观测（llm-usage-observability） | 1~2 | 🔴 ❌ | invoke_llm 内部无结构化 per-call 日志；先看清用量再调阈值 |

#### 🟡 P1：强烈建议（不做能跑但要付出运维代价）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 7 | 统一中台前端 MVP（登录 + 质检工作台 + 投诉研判工作台） | 15~20 | 🔴 ❌ | 当前三个演示页（原生 HTML+JS）定位是"演示与验收"非"业务用户日常工作"；无统一登录/无共享状态/无工作流 |
| 8 | CD 流水线（构建镜像 → 推仓库 → 滚动部署） | 3~5 | 🔴 ❌ | 当前部署靠手工 docker-compose up，无审计 |
| 9 | 日志采集（filebeat/vector → ELK/Loki） | 2 | 🔴 ❌ | 当前日志在容器里，容器重建即丢 |
| 10 | 告警通知（Prometheus alertmanager → 飞书/钉钉） | 1 | 🔴 ❌ | 出问题没人知道 |
| 11 | runbook 运维手册 + 应急流程 | 1~2 | 🔴 ❌ | 出问题不知道怎么处理 |
| 12 | 环境分离（dev/staging/prod 三份 .env） | 1 | 🔴 ❌ | 当前一份 .env 跑所有环境 |
| 13 | 修复 complaint-service real_transport.py 绕过 invoke_llm | 0.5 | 🔴 ❌ | 自己手写限流+重试，无 fallback/降级/统一返回结构；影响 LLM 用量统计口径 |
| 14 | call-service / case-service / review-service 真实实现 | — | 🔴 ❌ | 当前脚手架，M2/M4 里程碑 |

#### 🟢 P2：可延后（当前无需求或规模未到）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 15 | 知识库管理台（替换 Gradio 演示页） | 4~6 | 🔴 ❌ | 文档上传→向量化→检索测试→敏感词管理 |
| 16 | 运营监控台（接 Prometheus + 事件总线） | 4~6 | 🔴 ❌ | LLM 用量/限流/熔断状态/事件堆积/慢查询 |
| 17 | 审计与合规台（含 CoT 双授权解密工作流） | 4~6 | 🔴 ❌ | 操作日志/CoT 解密双授权/PII 脱敏数据查看 |
| 18 | 通知中心（SSE + 事件订阅） | 2~3 | 🔴 ❌ | 事件总线关键事件前端实时推送 |
| 19 | K8s manifests | — | 🔴 ❌ | 500 人内部单租户，docker-compose 够用；要上 k8s 再做 |
| 20 | 灰度真启用（CANARY_WEIGHT > 0） | — | 🔴 ❌ | 单版本，开关已备好但无需求 |
| 21 | LLM 独立网关（llm-gateway-standalone） | 2 | 🔴 ❌ | common 已有完整 invoke_llm 编排器，独立化收益当前用不上；暂缓 |
| 22 | 队列升级（Redis List → Redis Stream 消费组） | — | 🔴 ❌ | ACK 不丢任务/多 worker 竞争/失败重试/DLQ/对账补偿；待真丢任务后再做 |
| 23 | 配置中心（Apollo/Nacos） | — | 🔴 ❌ | .env 单一事实源够用 |
| 24 | DR 灾备 | — | 🔴 ❌ | 内部系统，RPO/RTO 容忍度高时可不急 |

---

### 8.3 里程碑对照

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 基础底座 | 🟢 ✅ | common/ + 网关 + 事件总线 + 数据库迁移 |
| M1 质检系统 | 🟢 ✅ | qc-service 闭环参与 |
| M2 外呼/分案 | 🔴 ❌ | call-service / case-service 脚手架 |
| M3 投诉研判 | 🟢 ✅ | complaint-service 深做 + 事件闭环 |
| M3.5 投诉 V2 | 🟢 ✅ | real-llm + 事件化 + 图状态机修复 |
| M4 复盘 | 🔴 ❌ | review-service 脚手架 |
| 生产上线 | 🔴 ❌ | P0 六项未完成 |
