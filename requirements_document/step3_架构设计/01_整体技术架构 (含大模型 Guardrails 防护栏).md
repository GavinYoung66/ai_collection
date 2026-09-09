# 整体技术架构（含大模型 Guardrails 防护栏）

> 文档类型：架构设计（规范层）
> 版本：v1.8
> 状态：定稿
> 更新日期：2026-09-09
> 约束声明：遵循 step1/01 全局统一口径与 openspec/specs/01 架构强制规范；服务名/端口/里程碑以 step1/01 为准；demand/ 仅作历史参考。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅分层与防护链路 | — |
| v1.0 | 2026-08-19 | 填充四层架构图、7 系统边界、依赖图、技术栈、部署拓扑、目录结构；Text2SQL 移出 MVP | 规范定稿 |
| v1.1 | 2026-09-08 | 补充集成分层准则（Agent=MCP / 管理API=HTTP / 异步=事件） | Ellis Guo |
| v1.2 | 2026-09-08 | 公共能力层去「RAG∈common」歧义；补实现深度与容量模型口径（platform-arch-unification-v5） | Ellis Guo |
| v1.3 | 2026-09-09 | P1-8 §六技术栈锁定表 LangGraph 0.0.40→1.2.11 + langgraph-checkpoint-postgres 3.1.2（对齐 complaint-service 实际落地 AsyncPostgresSaver） | Ellis Guo |
| v1.4 | 2026-09-09 | 批次2 §3.2/§五 LLM 推理参数按场景分层（评测 t=0.1+seed 固定 / 生产默认 0.3、0.1~0.5 可配、seed 不固定）；§3.2 RAG score<0.6 明确为精排后最终融合分数口径（对齐 step5/03 §6.2、step5/01 v1.5） | Ellis Guo |
| v1.5 | 2026-09-09 | 批次4（D3-1/D3-2/D3-5 架构级一致性）：§3.2 qc-service 告警通道标注含 audit_logs + Redis Stream 持久化队列（对齐 step6/02 §3.1/§3.2）；§五 公共能力层补 reliability 多副本熔断共享 Redis `{service}:llm_degraded` 源（对齐 step6/01 §5.1）；§5.1 容量口径表注明确 MVP/Demo 口径、非生产容量 | Ellis Guo |
| v1.7 | 2026-09-09 | 对齐 DD-02 V1.5：删质检告警 ≤3s；call→qc 改为通话结束后离线；核心模块 qc-realtime→qc-offline | Ellis Guo |
| v1.8 | 2026-09-09 | §七容器依赖表 qc-service 依赖补 data-platform(8001)（qc 订阅 call.session.ended 后需主动 HTTP 拉 DP ODS 全文，对齐 DD-02 §8.1 依赖表） | 规范复审 |

## 一、设计原则

1. 权责分离：传统系统负责流程调度、数据落库、业务执行；AI 仅负责认知判断、打分、建议输出，隔离大模型幻觉带来的业务风险。
2. 链路分离：实时交互链路低延迟优先，不经过数据中台；离线计算链路统一走数据中台，保障口径一致与性能稳定。
3. 模块解耦：七大子系统独立部署、独立迭代；管理面走 HTTP/WS，Agent 取证走 MCP（见原则 6）；禁止跨模块直接 import 代码。
4. 规范优先：所有架构决策不得违背 openspec/specs 宪法层与 step1 全局口径，冲突时以宪法层为准并同步修订。
5. 最小闭环优先：M1 阶段先跑通"数据中台 + RAG + 质检"小闭环，其余子系统积木式叠加。**实现深度口径**：当前深做切片为 `rag-service` 与 `complaint-service`；其余五个子系统可为 health 占位直至对应里程碑，不得表述为七系统业务功能已全部完备。
6. 集成分层（platform-integration-boundaries）：
   - **Agent / 工作流工具取证** → MCP（节点禁止散落直连下游 HTTP；MCP Server 内部可再 HTTP 调 rag/qc/业务系统）；
   - **服务管理面 / CRUD / 人机 API** → HTTP/JSON（经网关或内网公共接口）；
   - **异步通知**（通话结束、质检完成、回流）→ 事件（Redis Pub-Sub 或等价）；
   - 禁止跨服务 `import` 内部包；共享能力下沉 `common/`（配置、安全、trace、LLM **transport/reliability/guardrails** 等跨切面）。**`rag-service`(8002) 是独立业务子系统，不是 `common` 库；混合检索/入库/Gradio 归属 rag-service。**

## 二、技术分层架构

```mermaid
flowchart TB
    A[接入层<br/>Vue3前端 / 呼叫中心 / RTC语音流] --> GW[网关层<br/>nginx/traefik 生成X-Trace-Id]
    GW --> SVC[服务层 7大子系统<br/>各为Controller→Service→Repository]
    SVC --> COM[公共能力层<br/>LLM transport·reliability·Guardrails / RTC工具 / 安全·trace·日志]
    SVC --> RAG[rag-service 8002<br/>混合检索·入库·多模态·Gradio]
    COM --> STO[存储层<br/>PostgreSQL public+dw / Redis / 对象存储]
    RAG --> STO2[Milvus / ES / PG / Redis]
```

| 层 | 职责 | 延迟要求 |
|---|---|---|
| 接入层 | 前端交互、呼叫中心对接、RTC 语音流接入 | — |
| 网关层 | 路由、鉴权、限流、生成并透传 X-Trace-Id | 网关损耗 < 50ms |
| 服务层 | 7 子系统业务实现（含 **rag-service 知识库产品**），每服务内 Controller→Service→Repository(DAO) 单向依赖 | 实时应答 ≤1.5s（外呼）；质检为离线任务，无通话中 3s SLA |
| 公共能力层 | LLM HTTP transport / reliability / Guardrails 钩子、RTC/VAD/状态机工具类、安全脱敏审计、trace/告警日志 | **不含** RAG 产品（检索/入库属 rag-service）；公共组件不持业务状态 |
| 存储层 | PostgreSQL public（业务）+ dw（中台）双 schema、Milvus 向量、Redis 缓存、对象存储录音 | 普通查询 P95 < 500ms |

## 三、七大子系统服务边界

每个子系统为独立 FastAPI 微服务，部署为独立容器；共享能力下沉到 `common/` 层。

| # | 子系统 | 服务名 | 端口 | 里程碑 | 核心模块 |
|---|---|---|---|---|---|
| 1 | 催收AI数据中台 | data-platform | 8001 | M1 | data-sync / data-clean / data-warehouse / data-scheduler |
| 2 | 混合RAG与多模态知识库 | rag-service | 8002 | M1 | kb-ingest / kb-chunk / kb-search / kb-version |
| 3 | AI录音质检系统 | qc-service | 8003 | M1 | qc-offline / qc-scoring / qc-review |
| 4 | AI实时语音外呼与交互 | call-service | 8004 | M2 | call-dispatch / dialog-engine / asr-tts / transfer |
| 5 | AI智能分案与策略匹配 | case-service | 8005 | M2 | scoring / layering / routing / assign / reassign |
| 6 | AI投诉异议与合规研判 | complaint-service | 8006 | M3 | evidence / verdict / extract / solution |
| 7 | AI催收智能复盘与画像 | review-service | 8007 | M4 | classify / script-mine / report / portrait |

### 3.1 服务依赖关系

```mermaid
graph LR
    dp[data-platform] --> pg[(PostgreSQL)]
    rag --> pg
    rag --> mil[(Milvus)]
    rag --> es[(Elasticsearch)]
    rag --> emb[embedding-service]
    rag --> rnk[rerank-service]
    rag --> rds[(Redis)]
    qc --> pg
    qc --> rds
    qc -->|加载规则词库| rag
    call --> pg
    call --> rds
    call -->|话术检索| rag
    call -->|通话结束事件| qc
    call -->|喂入通话| dp
    case --> pg
    case -->|用户特征| dp
    comp[complaint-service] --> pg
    comp -->|合规知识经 MCP rag_kb| rag
    comp -->|质检经 MCP call_qc| qc
    rv[review-service] --> pg
    rv -->|宽表| dp
```

> 依赖图中 call→rag / qc→rag 为管理面 **HTTP**；call→qc 为 **事件**（`call.session.ended`，qc 拉全文）；complaint→rag / complaint→qc 为 Agent **MCP**。详见 step3/02。

### 3.2 职责边界要点

- data-platform：不参与实时通话链路，不持业务写权限（仅读业务库 + 写 dw schema）。
- rag-service：**独立业务子系统（8002）**，只管理知识内容与检索/入库，不关心催收流程；检索 score<0.6（score 指混合检索 [Milvus IP 向量分 + ES BM25] 经 bge-reranker 精排后的最终融合分数，口径定义见 step4/02 §四.1）返回空并标记，由调用方兜底。能力不放在 `common/`。
- qc-service：只做**离线**检测与打分，不做业务决策、不介入通话；告警走 Redis Pub-Sub 即时推送事后主管 + audit_logs 落库 + Redis Stream（`alert:stream`），可靠性见 step6/02 §3.1/§3.2。
- call-service：话术生成必经 RAG 检索 + Guardrails 校验；confidence<0.7 转人工；15 轮熔断；**单节点外呼并发上限 50 路**（见容量模型）。
- case/complaint/review：AI 只输出判断与建议，不直接改写业务核心数据。

## 四、LLM 安全防护链路

```
用户输入 →【前置 Guardrails 拦截】→ 业务逻辑 → LLM 推理 →【后置 Guardrails 校验】→ 输出给外部
```

- 前置拦截：敏感词、违规意图、停止致电标记在进入 LLM 前拦截；
- 后置校验：LLM 输出经红线 7 条 + 结构化字段强校验，不符则丢弃并返回兜底话术；
- 任意一关拦截，返回兜底回答、记告警日志，违规内容不向下游传递（详见 step1/03 兜底话术表）。

## 五、公共能力层

`common/` 只放**跨服务真正共享**的能力；**不含**混合检索产品实现。

- LLM：`common.llm.transport`（HTTP 执行）+ reliability / Provider 组合（重试、降级键）；结构化输出参数按场景分层注入（评测/回归集 temperature 0.1+seed 42 固定；生产对话生成默认 0.3、可配置 0.1~0.5、seed 不固定，见 step5/03 §6.2 / response_format json_object 等）；
- reliability 熔断：pybreaker 进程内快速熔断；跨副本部署以 Redis `{service}:llm_degraded` 为共享熔断源、各副本读写对齐（多副本边界见 step6/01 §5.1）；
- Guardrails：共享前后置钩子（敏感词/红线），红线对齐 openspec/specs/00；领域规则可由各服务注册；
- RTC 语音公共工具：VAD 静音检测、语音打断、会话状态机（工具类，非独立业务服务）；
- 安全 / 配置 / trace：脱敏、审计、平台 Settings、X-Trace-Id 全链路透传、异常告警日志。

**RAG 归属（勿混）：** 混合检索（Milvus 稠密 + Elasticsearch BM25 + rerank）、多模态入库、Gradio 工作台均在 **`services/rag-service`**。其他服务经 HTTP `/kb/*`（管理面）或 MCP `rag_kb`（Agent）调用，不 `import` rag 内部包。

> Text2SQL 组件仅 M5+ 引入，MVP 期间账单查询走 RAG + 结构化接口，不启用 Text2SQL。

### 5.1 MVP 容量模型（口径表）

| 链路类型 | MVP/Demo 口径 | 说明 |
|----------|---------------|------|
| 单节点外呼并发 | **50 路** | step1/01、call-service 限流；与「质检吞吐」不是同一指标 |
| 质检/转写吞吐 | 已结束通话的离线质检任务吞吐 | **离线能力目标**，不等于外呼 50 路并发；实现以 qc-service 里程碑为准 |
| 投诉研判压测基线 | 约 20 并发持续 / 峰值约 100 工单/小时 | 见 DD-05；非知识库篇数规模 |
| 知识库规模 | MVP 种子级（百～千篇量级演示） | **未验证**「10 万篇 / 月 10 万工单」；简历勿写为已测事实 |
| 检索链路 | 一期仅 Milvus 稠密检索（单路）；ES BM25 + bge-reranker 为**二期** | 见 step4/02 §七/§八（>5 万篇或命中率 <60% 时评估启用） |

> **容量口径注（批次4 D3-5）**：上表为 **MVP/Demo 口径**容量，非生产容量；生产容量模型另行评估。下游文档（如 step6/02 外呼并发 >48/50 路告警阈值）同属该 MVP/Demo 口径，不得视为生产承诺。

## 六、技术栈锁定

| 维度 | 锁定 |
|---|---|
| 语言/运行时 | Python 3.11.15 |
| Web 框架 | FastAPI 0.104.1 + Uvicorn async |
| ORM/迁移 | SQLAlchemy 2.0 async + Alembic |
| AI 编排 | LangChain 0.1 + LangGraph 1.2.11（+ langgraph-checkpoint-postgres 3.1.2，AsyncPostgresSaver） |
| 大模型 | 阿里云 DashScope qwen-plus（OpenAI 协议兼容） |
| 向量库 | Milvus 2.4 standalone |
| Embedding | BGE-M3 本地部署（1024 维） |
| 稀疏检索 | Elasticsearch 8（BM25）（二期/可选，MVP 可不下发） |
| 重排序 | bge-reranker / rerank-service：8101（二期/可选，MVP 可不下发） |
| 数据库/缓存 | PostgreSQL 15 + Redis 7 |
| ASR/TTS | 阿里云 NLS（WebSocket 流式） |
| 呼叫中心 | 阿里云 CCC（HTTP API） |
| 调度/熔断/限流 | APScheduler / pybreaker / asyncio.Semaphore |
| 前端 | Vue3 + Vite5 + Pinia + Element Plus + TypeScript |

详细版本与 pip-tools 哈希约束见 step5/01。PostgreSQL 统一 public（业务）+ dw（中台）双 schema 共用单实例，ClickHouse 不引入 MVP。

## 七、部署拓扑（docker-compose）

| 服务 | 端口 | 依赖 | 里程碑 |
|---|---|---|---|
| postgres:15-alpine | 5432 | — | M0 |
| etcd v3.5.5 | 2379 | — | M0 |
| minio | 9000,9001 | — | M0 |
| elasticsearch 8 | 9200 | — | 二期/可选（MVP 可不下发） |
| milvus v2.4.0 | 19530 | etcd,minio | M0 |
| redis:7-alpine | 6379 | — | M0 |
| embedding-service | 8100 | — | M0 |
| rerank-service | 8101 | — | 二期/可选（MVP 可不下发） |
| data-platform | 8001 | postgres,redis | M1 |
| rag-service | 8002 | postgres,milvus,redis,embedding | M1 |
| qc-service | 8003 | postgres,redis,rag-service,data-platform | M1 |
| call-service | 8004 | postgres,redis,rag-service | M2 |
| case-service | 8005 | postgres,data-platform | M2 |
| complaint-service | 8006 | postgres,rag-service,qc-service | M3 |
| review-service | 8007 | postgres,data-platform | M4 |
| gateway (nginx/traefik) | 80,443 | 各业务服务 | M1 |
| frontend (nginx+Vue3 dist) | 80 | gateway | M3 |

### 网络与卷

- 网络：`ai_collection_backend`（内部服务间通信，不对外）+ `ai_collection_frontend`（仅 gateway、frontend）；外部仅暴露 gateway 80/443。
- 卷：`pg_data`、`milvus_data`、`etcd_data`、`minio_data`、`redis_data`，命名规范 `<service>_data`。

### 环境分层

| 维度 | dev | staging | prod |
|---|---|---|---|
| 部署 | docker-compose 单机 | docker-compose 单机 | docker-compose 多节点 / k8s |
| 数据 | 种子数据 | 脱敏生产副本 | 真实数据 |
| 大模型 | Mock 或低成本模型 | 真实模型 | 真实模型 |
| ASR/TTS/呼叫中心 | Mock | 阿里云测试环境 | 阿里云生产 |
| 日志级别 | DEBUG | INFO | INFO + ERROR 告警 |
| HTTPS | 关闭 | 开启 | 开启 + HSTS |

分文件：`docker-compose.yml`（基础）+ `docker-compose.dev.yml`（Mock + DEBUG + 源码热重载）+ `docker-compose.prod.yml`（资源限制 + HTTPS + 不挂源码）。

## 八、目录结构（monorepo）

```
ai_collection/
├── services/                # 7 子系统代码，各服务独立
│   ├── data-platform/       # 8001
│   ├── rag-service/         # 8002
│   ├── qc-service/          # 8003
│   ├── call-service/        # 8004
│   ├── case-service/        # 8005
│   ├── complaint-service/   # 8006
│   └── review-service/      # 8007
├── common/                  # 跨切面公共能力（config/db/security/llm transport·reliability/…）；不含 RAG 产品
├── deploy/                  # embedding-service + rerank-service Dockerfile
├── migrations/              # Alembic 迁移脚本
├── scripts/                # smoke 脚本、seed 数据、敏感词初始化
├── docs/                   # 设计文档归档
├── 标准工程目录结构/        # 规范层（step1~step8）
├── openspec/                # 宪法层（specs）
└── demand/                  # 历史参考（只读，不参与运行）
```

每个服务内部统一为 `api（Controller）/ service / repository（DAO）/ schemas / tests` 五段，遵循 openspec/specs/01 三层单向依赖；跨服务禁止互相 import 内部代码，必须走 HTTP/WS（管理面）或 MCP（Agent 取证），见原则 6。
