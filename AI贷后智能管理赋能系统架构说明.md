# AI 贷后智能管理赋能系统 · 架构说明

> **文档版本**：v2.1（评审修订版）  
> **更新日期**：2026-11-01  
> **适用代码**：`dev` 分支，校对于提交 `0f3b9a9`（含工作区改动）  
> **维护**：项目组  
> **核对口径**：关键结论均对照源码；未实测的目标值单独标注，不与实测结果混写。

**阅读路径**

- **15 分钟速读**：§0 定位 → §1 三张王牌 → §7 前端接入契约 → §11 成熟度与边界
- **完整阅读**：§0 → §11，按顺序
- **深入实现**：§5 RAG、§6 LangGraph，配合附录 A 术语表与附录 C 代码索引

**目录**

- [0. 一句话定位](#sec0)
- [1. 三张王牌：30 秒速览](#sec1)
- [2. 系统全景与分层](#sec2)
- [3. 数据流向与调用链路](#sec3)
- [4. AI 与业务的边界](#sec4)
- [5. RAG 多模态混合检索](#sec5)
- [6. LangGraph 多 Agent 编排](#sec6)
- [7. 前端接入契约](#sec7)
- [8. 技术栈与选型理由](#sec8)
- [9. 工程底座：韧性、安全与可观测](#sec9)
- [10. 部署、容量与演进](#sec10)
- [11. 工程成熟度与已知边界](#sec11)
- [附录 A. 术语表](#secA)
- [附录 B. 详细缺陷清单](#secB)
- [附录 C. 事实校对与代码索引](#secC)

---

<a id="sec0"></a>
## 0. 一句话定位

这是一个**面向贷后催收场景的「LLM + RAG + 多 Agent」微服务系统**。

业务原则只有一条：

> **AI 只出草稿、做认知判断，不做最终判定；所有会影响定责与回写的 AI 输出，都必须经过人工复核。**

工程原则则是：业务是垂直的（催收），但工程底座是通用的——LLM 韧性编排、RAG 混合检索、MCP 协议、事件驱动、多 Agent 编排，这些能力可以直接迁移到其他 AI 业务系统。

### 0.1 系统规模

| 维度 | 数量 | 说明 |
|---|---|---|
| 前端 | 1 个 Vue3 管理台 | 智能投诉、合规质检、RAG 知识库、LLM 用量、系统管理 |
| 业务微服务 | 7 个 | 端口 8001–8007，独立容器、独立数据库 schema |
| 深度参与闭环 | 4 个 | data-platform、rag-service、qc-service、complaint-service |
| 脚手架服务 | 3 个 | call-service、case-service、review-service（仅 health 接口） |
| AI 基础设施 | 5 类 | embedding、rerank、Milvus、Elasticsearch、DashScope 大模型 |
| 中间件 | 4 类 | Nginx、RocketMQ、Redis、PostgreSQL |
| 部署定位 | 单机 | 500 人内部单租户，Docker Compose，不上 K8s |

### 0.2 它不是什么

- **不是自动决策系统**：没有人工 `approve`，AI 结论不会回写数据中台。
- **不是通用聊天机器人**：所有能力围绕催收质检与投诉研判，知识库、规则、话术都是领域资产。
- **不是完成态产品**：外呼、分案、复盘三个服务目前是脚手架，本文会明确标注成熟度，不把规划写成现状。

---

<a id="sec1"></a>
## 1. 三张王牌：30 秒速览

如果只读一节，读这里。三张王牌分别回答三个问题：**AI 如何分工、知识如何检索得准、工具如何接入得稳。**

### 1.1 王牌一：LangGraph 多 Agent + Human-in-the-Loop

**一句话价值**：把一次复杂的投诉研判拆成 13 个可审计节点，3 个专业 Agent 并行出结论，`verdict` 只出草稿，人工 `approve` 之后才回写。

| 维度 | 内容 |
|---|---|
| 关键数字 | 13 个图节点；3 个并行研判 Agent（事实 / 合规 / 诉求）+ 1 个融合 Agent；反思循环上限 3 次；人工驳回上限 3 次（第 3 次强制升级） |
| 可靠性设计 | 图状态经 checkpointer 持久化到 PostgreSQL，服务重启后可断点续跑 |
| 业务边界 | 高置信不会跳过人审，只决定“先生成报告还是先审草稿”；两条路径最终都汇合到 `human_review` |
| 代码依据 | [builder.py](services/complaint-service/app/graph/builder.py)、[edges.py](services/complaint-service/app/graph/edges.py)、[state.py](services/complaint-service/app/models/state.py) |

### 1.2 王牌二：RAG 双路混合检索

**一句话价值**：同一句查询同时走 Milvus 语义召回和 Elasticsearch 关键词召回，RRF 融合后做 rerank 精排，再用两道质量闸门过滤低质量结果。

| 维度 | 内容 |
|---|---|
| 关键数字 | 2 路召回；10 步主链路；BGE-M3 1024 维；RRF 参数 k=60；闸门阈值 0.6 / 0.35 |
| 典型收益 | 既能理解“能减免吗”的语义，又能精确命中“CS20260020”这类案件号 |
| 指标状态 | 设计目标为 top-3 准确率 ≥85%、召回率 ≥90%；**当前 Mock 环境未实测**，不能当作已验证结果（详见 §5.6） |
| 代码依据 | [search_service.py](services/rag-service/app/services/search_service.py)、[rag_settings.py](services/rag-service/app/rag_settings.py) |

### 1.3 王牌三：MCP 统一取证协议

**一句话价值**：Agent 不直接 HTTP 调下游服务，只认 4 个标准 MCP server；下游服务如何变更，Agent 都不感知，出口还统一做 PII 脱敏。

| 维度 | 内容 |
|---|---|
| 关键数字 | 4 个 server：`business_ticket` / `call_qc` / `rag_kb` / `data_platform`；单次调用超时 30s；PII 双层脱敏 |
| 运行模式 | 开发默认进程内 Mock；真实路径经 MCP SDK 连接独立 `mcp-gateway` 容器 |
| 核心收益 | 工具边界稳定、调用可审计、下游变更对 Agent 透明 |
| 代码依据 | [services/mcp-gateway](services/mcp-gateway/)、[gateway.py](services/complaint-service/app/mcp/gateway.py) |

### 1.4 数字看板

| 指标 | 数值 | 口径与状态 |
|---|---|---|
| 业务微服务 | 7 | 其中 4 个深度参与闭环 |
| 自动化测试 | 1402 passed / 0 failed / 3 skipped | CI 记录（2026-10），测试数会随代码演进变化 |
| 最小闭环验收 | 11 / 11 PASS | `run_min_loop.py` 可重复执行 |
| 错误码 | 93 条 | [codes.py](common/errors/codes.py) |
| CI 门禁 | 6 个并行 job | 5 个服务 job + 1 个 common/脚手架 job |
| 网关能力 | 14 项 | 路由、限流、鉴权、CORS、Trace、超时分级等 |

> 说明：以上均为可在仓库内复核的工程指标。**业务效果指标（检索准确率、研判耗时 P95、人工替代率）目前没有实测基线**，本文只写设计目标，不把目标包装成结果。

---

<a id="sec2"></a>
## 2. 系统全景与分层

先看全景图。这是理解后文所有内容的基础。

```
┌─────────────────────────────────────────────────────────────────────┐
│ 浏览器：Vue3 SPA（frontend/）                                       │
│ 智能投诉 / 合规质检 / RAG 知识库 / LLM 用量 / 系统管理               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS  /api/v1/*（统一信封）
┌──────────────────────────────▼──────────────────────────────────────┐
│ Nginx 网关 :8080                                                    │
│ 路由 · 限流 20 r/s · JWT 鉴权（auth_request）· CORS · Trace 注入      │
│ 超时分级：普通 15s / judge 与 qc replay 专用 120s                     │
└───────┬──────────────────┬───────────────────┬──────────────────────┘
        │                  │                   │
┌───────▼────────┐ ┌───────▼────────┐ ┌────────▼───────────────────┐
│ 7 个业务微服务  │ │ auth 侧车      │ │ AI 能力层                   │
│ 8001–8007      │ │ login/refresh  │ │ embedding:8100              │
│                │ │ /me            │ │ rerank:8101 · ES:9200       │
└───────┬────────┘ └────────────────┘ └────────────────────────────┘
        │
        │ complaint-service 经 MCP 协议调用独立 MCP 网关
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ MCP 网关（独立容器，容器内 :9000；dev 宿主机 9009→9000）             │
│ 4 个 server：business_ticket / call_qc / rag_kb / data_platform      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 内部 HTTP
                    ┌──────────▼──────────────────┐
                    │ rag / qc / data-platform     │
                    └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ 公共底座 common/                                                    │
│ LLM 韧性编排 · 事件总线 · 脱敏与加密 · 链路追踪 · 错误码             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ 存储与中间件：PostgreSQL · Redis · Milvus · Elasticsearch · RocketMQ  │
└─────────────────────────────────────────────────────────────────────┘
```

> 两个容易误读的点：  
> 1. **MCP 网关不经 Nginx 对外暴露**，它只服务 Agent 取证，由 `complaint-service` 通过 MCP 协议调用。  
> 2. **“7 个微服务”只数业务服务**；auth 侧车、MCP 网关、embedding、rerank、ES 等属于基础设施容器，单独计数。

### 2.1 各层职责

| 层 | 类比 | 职责 | 关键组件 |
|---|---|---|---|
| 接入层 | 前台 / 门卫 | 唯一入口，只做转发与横切治理，不写业务逻辑 | Nginx :8080 |
| 业务服务层 | 各业务部门 | 7 个独立进程，各自负责一块业务，独立部署、独立 schema | 8001–8007 |
| AI 能力层 | 外部专家库 | 向量化、重排、全文检索、大模型调用 | embedding / rerank / Milvus / ES / DashScope |
| 公共底座 | 公共工具间 | 被所有服务复用的横切能力，不含业务逻辑 | `common/` |
| 存储层 | 档案室 | 业务数据、向量、缓存、全文索引、事件 | PG / Redis / Milvus / ES / RocketMQ |

### 2.2 7 个业务微服务与成熟度

成熟度等级定义：

- **L0 占位**：只有 `/health`，无业务接口。
- **L1 可用**：核心 API 可用，但未接入全链路。
- **L2 闭环**：参与端到端事件闭环，有真实数据流转。
- **L3 深度实现**：业务完整、边界情况有处理、有对应测试。

| 服务 | 端口 | 一句话职责 | 成熟度 |
|---|---|---|---|
| data-platform | 8001 | 数据中台：接原始案件、预处理、快照回流，是全链路对账的枢纽 | L2 |
| rag-service | 8002 | RAG 知识库：多格式入库 + 混合检索 + 敏感词词典 | L3 |
| qc-service | 8003 | 录音合规质检：R1–R14 规则 + LLM 复核打分 | L3 |
| call-service | 8004 | AI 实时语音外呼（ASR/TTS 对话） | L0（M2） |
| case-service | 8005 | 智能分案与策略匹配 | L0（M2） |
| complaint-service | 8006 | 投诉异议研判：LangGraph 多 Agent | L3 |
| review-service | 8007 | 智能复盘与画像 | L0（M4） |

> 当前策略是“最小闭环优先”：先把「质检 + 投诉研判」这条核心链跑通，再补外呼、分案、复盘。这不是遗漏，而是有意的范围控制。

### 2.3 基础设施容器

| 容器 | 作用 | 端口 / 备注 |
|---|---|---|
| Nginx | 统一网关 | :8080 |
| auth-sidecar | 登录、refresh、/me、错误审计 | 仅内网可达 |
| mcp-gateway | Agent 工具协议网关 | 容器内 :9000；dev 宿主机映射 9009→9000（9000 被 MinIO 占用） |
| embedding | BGE-M3 向量化 | :8100 |
| rerank | bge-reranker-large 精排 | :8101 |
| Elasticsearch | BM25 稀疏检索 | :9200 |
| Milvus | 稠密向量库 | :19530 |
| PostgreSQL | 业务库 + `dw` 数仓 schema | :5432 |
| Redis | 缓存、限流计数、qc 告警流 | :6379 |
| RocketMQ | 跨服务事件总线 | NameServer 9876 / Proxy 8081 |

---

<a id="sec3"></a>
## 3. 数据流向与调用链路

系统有两条链路，理解它们就理解了全系统的血液循环：

- **同步链路**：前端 → 网关 → 服务 → LLM/RAG，适合“用户点按钮等结果”。
- **异步链路**：跨服务用事件通知，适合“一个动作触发下游一串处理”。

### 3.1 同步链路：以「投诉研判」为例

这是系统里最完整的一条请求，把网关、限流、LangGraph、MCP、RAG、LLM、人工复核串在一起。

```
前端 POST /api/v1/complaints/{id}/judge   body: {"run_graph": true}
  │  前端超时 110s < 网关 judge location 120s
  ▼
Nginx 网关  ← JWT 鉴权、X-Trace-Id 注入、限流、路由
  ▼
complaint-service :8006
  ├─ 五维限流：用户 / 工单 / 并发 / 幂等 / 排队
  │    ├─ 重复提交 → B0005（同工单处理中）
  │    ├─ 队列满   → B0006（请稍后重试），或 202 进入延迟队列
  │    └─ 已有在途 → 幂等 no-op，返回当前状态
  ▼
LangGraph 图（13 个节点）
  ├─ 需要取证 → MCP 网关 :9000 → rag / qc / data-platform
  └─ 需要推理 → DashScope qwen-plus（经 common/llm 韧性编排）
  ▼
verdict 草稿 → human_review（interrupt 挂起，checkpoint 持久化）
  ▼
前端轮询 GET /api/v1/complaints/{id}/status
  └─ 看到 task_status = pending_review（这是正常业务态，不是错误）
  ▼
人工 PUT /api/v1/complaints/{id}/review  {"review_action": "approve"}
  ▼
恢复图 → sample_backflow → 回写数据中台 → 前端轮询到 resolved
  ▼
前端拉 GET /api/v1/complaints/{id}/verdict 展示结论与引用
```

**前端视角的三个关键点**

1. **研判是长任务**：`judge` 可能返回“已受理 / 排队中 / 202 延迟队列”，不等于已经出结果。正确做法是「触发 → 轮询 `/status` → 终态拉 `/verdict`」。
2. **`pending_review` 是正常状态**：AI 已经完成草稿，正在等人。不要视为失败提示。
3. **每次请求都带 `trace_id`**：排查问题时拿后 8 位去对日志、MCP 调用记录和 Langfuse。

### 3.2 异步链路：事件驱动的业务闭环

跨服务的“通知”不直接 HTTP 调对方，而是发一条事件到 RocketMQ，订阅方收到后**回查明细**。

```
data-platform 种子入库
  └─ etl.task.done ──────────────> qc-service
                                      └─ qc.check.completed ──> data-platform 回写快照
                                                                  └─ complaint.created ──> complaint-service
                                                                                              └─ 多 Agent 研判
                                                                                                    └─ 人工 approve
                                                                                                          └─ complaint.result.ready ──> data-platform 确认
```

**为什么事件里只带 ID，不带业务数据？**

这是系统的核心设计原则之一：订阅方收到 `etl.task.done` 后，**主动回查** data-platform 拉明细。

好处有四条：

- 事件很轻，不会因为塞入整段通话文本而膨胀；
- 不怕数据过期，订阅方永远拿到最新状态；
- 天然幂等，重复消费不会产生脏数据；
- 事件格式稳定，新增业务字段不需要改事件契约。

> 类比：公司内部用钉钉通知你「有事找你」，而不是把整份文件贴在通知里；你收到通知后自己去系统里看最新内容。

### 3.3 事件设计的三个硬约束

**① 数据白名单**

事件数据体只允许携带以下字段（[envelope.py](common/eventbus/envelope.py)）：

```text
case_id, session_id, qc_record_id, complaint_no, backfill_date
```

`EventEnvelope` 使用 Pydantic `extra="forbid"` 加自定义校验器：`data` 里出现白名单之外的字段会**直接抛错拒绝**，并记录审计日志。发布侧和消费侧各校验一次。

**② 幂等消费**

订阅侧用「事件 ID + 消费组」做去重，重复投递不会重复执行业务。

**③ 回查与对账**

事件只负责“提醒”，真实数据以回查结果为准。快照回流后由 data-platform 做对账，发现不一致时进入人工处理。

**失败路径的现状**

| 失败场景 | 当前行为 | 缺口 |
|---|---|---|
| 消费处理失败 | RocketMQ 重试；服务内记录错误 | 项目内尚无统一 DLQ 与补偿脚本 |
| 回查失败 | 事件重试；多次失败后依赖人工介入 | 缺少自动对账告警 |
| 重复消费 | 事件 ID 去重，直接跳过 | 已覆盖 |
| 事件积压 | 可通过事件端点和监控发现 | 缺少专门的堆积告警规则 |

### 3.4 同步与异步的选择标准

| 场景 | 选择 | 原因 |
|---|---|---|
| 用户在页面点按钮、等结果 | 同步 | 需要即时反馈，错误要能直接展示 |
| 跨服务状态通知 | 异步事件 | 解耦、削峰、订阅方可回查最新数据 |
| 研判这类长耗时任务 | 同步触发 + 轮询 | 网关有 HTTP 超时上限，不能长期占用连接 |
| 批量质检、回写、回流 | 异步 | 允许延迟，失败可重试 |
| 人工审批后的回写 | 事件 | 审批与回写解耦，回写失败可重放 |

---

<a id="sec4"></a>
## 4. AI 与业务的边界

这是整套系统最重要的设计章节。它回答一个评审必问的问题：**哪些事 AI 可以做、哪些事 AI 不能做、出现问题时由谁最终负责？**

### 4.1 三层决策模型

| 层 | 擅长 | 不擅长 | 在本系统中的职责 |
|---|---|---|---|
| **规则层** | 确定性判断、可解释、毫秒级 | 语义理解、模糊场景 | 敏感词命中、质检规则 R1–R14、红线拦截、状态机、权限、脱敏 |
| **LLM 层** | 语义理解、归纳、生成 | 确定性、可复现、成本控制 | 事实还原、合规判断、诉求与情绪分析、判责草稿、报告生成 |
| **人工层** | 最终责任、例外处理 | 规模化、速度 | 复核草稿、改判、驳回、升级、最终定责与回写 |

三层不是替代关系，而是**逐层收敛**：规则先挡红线，LLM 再做认知判断，人工做最终决定。

### 4.2 各环节的决策归属

| 环节 | 规则层 | LLM 层 | 人工层 | 最终权威 |
|---|---|---|---|---|
| 分案（规划中） | 硬规则兜底：极高风险强制分配资深坐席 | 风险打分（规划中） | 坐席 / 主管调整 | 规则优先 |
| 质检 | R1–R14 规则命中、AC 自动机敏感词 | 语义复核、违规解释、复核打分 | 复核队列处理争议样本 | 规则命中即记录；LLM 分作为复核依据 |
| 投诉研判 | 状态机、权限、红线拦截 | 取证、事实还原、合规判断、诉求分析、判责草稿 | `approve` / 改判 / 驳回 / 升级 | **人工 approve 之后才回写** |
| 话术生成 | 模板与红线约束 | 生成候选话术 | 人工采纳或修改 | 人工 |
| 回写数据中台 | 事件 + 幂等 + 对账 | 不参与 | `approve` 触发 | 人工 |

### 4.3 人审边界：高置信也不跳过人审

这是最容易被误读的一点。`verdict` 节点之后有两条分支：

- 如果 `task_status = pending_review`、LLM 降级、或没有生成草稿 → **先进入 `human_review`**；
- 否则（高置信）→ **先生成报告/答辩稿，再进入 `human_review`**。

两条路径最终都汇合到 `human_review`。**高置信只改变“先审草稿还是先出报告”，不改变“必须人审”的结论。**

人工复核支持 6 个动作：

| 动作 | 含义 | 后续 |
|---|---|---|
| `approve` | 通过 | 回写数据中台，任务进入 `resolved` |
| `modify_verdict` | 改判 | 以人工修改为准后回写 |
| `modify_response` | 修改答复 | 以人工修改为准后回写 |
| `reject_rejudge` | 驳回重判 | 回到取证/研判；前 2 次有效，第 3 次强制升级 |
| `add_evidence` | 补充证据 | 回到取证/研判流程 |
| `escalate` | 升级 | 转高级复核 `pending_senior_review` |

### 4.4 两个循环上限不是一回事

读者容易把“反思 3 次”和“驳回 2 次”读成矛盾。它们是两个独立计数器：

| 计数器 | 作用 | 上限 | 超限行为 |
|---|---|---|---|
| `loop_count` | 反思 Agent 认为证据不足时，重新取证的次数 | `max_loops = 3` | 若已有案件证据则强制进入研判；否则转人工 |
| `reject_count` | 人工驳回重判的次数 | 3 次 | 第 3 次驳回强制 `escalate`，转高级复核 |

实现分别位于 [state.py](services/complaint-service/app/models/state.py) 与 [review_service.py](services/complaint-service/app/service/review_service.py)。

### 4.5 降级与兜底矩阵

| 依赖故障 | 系统行为 | 前端表现 | 是否有人工兜底 |
|---|---|---|---|
| LLM 熔断 / 全链失败 | fallback 链耗尽后返回 degraded，不伪造结果；节点走规则草稿并转人审 | `pending_review` 或错误码 `A5017`（正向提示“已转人工审核”） | 是 |
| LLM 单次超时 | 不重试，直接换下一个模型 | 最终可能 `A5016`（超时转人审） | 是 |
| LLM Token 超限 | 中止调用，转人审 | `A5015`（正向提示） | 是 |
| Embedding 服务不可用 | 降级为 ES-only 检索，跳过稠密闸门，由 rerank 闸门兜底 | 检索结果可能变少，`degraded=true` | 是 |
| 双路检索分数都低 | 返回兜底话术，不硬编答案 | `A3004` 或空结果提示 | 是 |
| MCP 下游超时 / 熔断 | 单次 30s 超时；节点降级并转人审 | 任务停在 `pending_review` | 是 |
| 图锁不可用（Redis 不可用） | 退化为进程内守卫，继续执行 | 可能有重复提交风险，由幂等回流兜底 | 部分 |
| Checkpoint 存储不可用 | 图无法持久化/恢复 | 任务失败或转人工 | 是 |
| 研判队列满 | 返回 `B0006` 或 202 进入延迟队列 | 提示稍后重试 / 显示排队中 | 是 |

### 4.6 五条不可逾越的边界

1. **没有人工 `approve`，AI 结论不回写。**
2. **全链失败不伪造结果**：降级必须显式返回 `degraded`，由上层决定转人审还是规则兜底。
3. **红线规则优先于 LLM 解释**：合规红线命中时，不采纳 LLM 的“合理解释”。
4. **PII 出 Agent 前必须脱敏**；CoT 加密链路的端到端状态见附录 B（原语已实现，投诉路径仍是占位串）。
5. **前端不复制后端业务规则**：“能不能操作、该显示什么”以后端返回的状态字段和错误码为准。

---

<a id="sec5"></a>
## 5. RAG 多模态混合检索

这是 `rag-service` 的核心。先澄清两个容易混淆的概念：

- **「多模态」指入库侧**：能处理 pdf / docx / pptx / xlsx / 图片 / 音频 / md / txt 等多种文件格式（图片走 OCR、音频走 ASR、表格走结构化抽取），最终统一转成文本 chunk 再向量化。
- **「混合检索」指查询侧**：查询时同时走「稠密向量检索」和「稀疏全文检索」两条路，再融合排序。

所以它本质是：**多格式文档 → 统一文本 → 双路检索 → 融合重排**。不是 CLIP 那种“图片搜图片”的跨模态语义对齐。

### 5.1 一条查询的完整管线

| # | 步骤 | 做什么 | 失败 / 降级 |
|---|---|---|---|
| 1 | Query 改写 | 多轮会话补全、意图改写（LLM，开关门控） | 失败则用原句 |
| 2 | Embedding | BGE-M3，1024 维，本地 :8100 | 失败降级为 ES-only |
| 3 | 双路召回 | Milvus 稠密 + ES BM25 稀疏，并行 | 单路失败则用另一路 |
| 4 | RRF 融合 | 按排名算 `1/(k + rank)`，k=60 | — |
| 5 | 去重 | doc_id 去重 + 内容级去重 | — |
| 6 | 业务加权 | 新通话 ×1.5、高风险 ×1.3 等（可选） | — |
| 7 | Rerank 精排 | bge-reranker-large，本地 :8101 | 失败则用原始分 |
| 8 | 实体 ID 加权 | 含 `CS20260020` 等实体 ID 的 chunk 强制置顶 | — |
| 9 | 行级权限过滤 | 按角色过滤；异常时 fail-closed，宁可少给不漏给 | — |
| 10 | 双闸门 + 兜底 | 稠密相似度 < 0.6 或 rerank 分 < 0.35 判定无匹配 | 返回兜底话术“知识库暂无明确指引” |
| 11 | 可选 LLM 问答 | `answer=true` 时把 top chunk 喂给 LLM，生成带引用的回答 | 失败则只返回检索结果 |

### 5.2 为什么混合检索比单一检索强

| | 稠密向量（Milvus） | 稀疏检索（ES BM25） |
|---|---|---|
| 擅长 | **语义**相似：换个说法也能命中 | **关键词**精确：案件号、专有名词 |
| 弱项 | 精确 ID 反而可能漏 | 同义改写会漏 |

**融合**之后，既懂语义又懂精确词。这在催收场景很关键，因为用户会问：

> “CS20260020 这单能减免吗？”

这句话既需要精确锁定案件号，又需要理解“减免”的语义。单走向量容易漏案件号，单走关键词容易漏同义表达。

### 5.3 RRF 融合：为什么不能直接比分数

Milvus 的相似度分和 ES 的 BM25 分量纲不同，不能直接相加。RRF（Reciprocal Rank Fusion）只看**排名**：

```text
rrf_score = 1 / (k + rank)     # k 默认 60
两路对同一 chunk 分别计算，再相加
```

一个 chunk 在两路里都排名靠前，RRF 分就高——自然偏向“语义和关键词都命中”的结果。

### 5.4 双闸门互补

| 闸门 | 判据 | 卡的是什么 | 失效场景 |
|---|---|---|---|
| 闸门 1 | 稠密召回最高相似度 < 0.6 | “语义上够不够像” | ES-only 降级时**跳过**（没有稠密分可比） |
| 闸门 2 | rerank 精排最高分 < 0.35 | “精排后够不够相关” | — |

**互补的含义**：闸门 1 用原始召回的语义相似度，闸门 2 用 rerank 后的相关性分。Embedding 不可用、降级为 ES-only 时，闸门 1 失效，闸门 2 仍然兜底——保证任何一条路径都有质量闸门。

代码里 `max_dense_score` 为空时会特意跳过闸门 1，而不是误判“分数 0 < 0.6 全拒”。

### 5.5 在系统中的落点

1. **qc-service**：加载敏感词词典（AC 自动机）做违禁词匹配；法规文档走检索。
2. **complaint-service 的合规节点**：通过 MCP 调 `search_regulations` 检索法规，作为判责依据，带源 ID 可回溯。
3. **call-service**（规划中）：话术检索，检索合规话术模板。

> 注意：敏感词本身用 AC 自动机做确定性匹配，不走向量检索；RAG 负责法规、话术、案例这类需要语义理解的知识。

### 5.6 评测与指标：目标值与实测值分开写

| 指标 | 设计目标 | 当前状态 | 说明 |
|---|---|---|---|
| top-3 准确率 | ≥ 85% | **未实测** | 外部依赖确认表中的目标，Mock 环境标为 N/A |
| 召回率 | ≥ 90% | **未实测** | 同上 |
| 检索 P95 延迟 | 未设定 | 未建立基线 | 需要生产流量或压测数据 |
| 降级触发率 | 未设定 | 未建立基线 | 依赖线上监控 |

**下一步建议**：建立一套人工标注的黄金集，至少测量 `hit@k`、MRR、P95 延迟、ES-only 降级率。在这些数据出来之前，文档不宣称“检索准确率达到 X%”。

---

<a id="sec6"></a>
## 6. LangGraph 多 Agent 编排

这是 `complaint-service` 的核心，也是系统 AI 能力最集中的部分。

### 6.1 先用前端概念理解 LangGraph

| LangGraph 概念 | 前端类比 |
|---|---|
| Graph | 一张有向流程图 / 状态机 |
| Node | 一个只做一件事的 worker |
| Edge | 路由：下一步去哪个节点 |
| State | 共享 store，所有节点读写同一份状态 |
| Reducer | 多个异步写操作合并同一个 store 字段时的 merge 函数 |
| Checkpointer | 把 store 持久化，刷新/重启后能恢复 |
| Interrupt | 在某个节点暂停，等人输入后再继续 |

### 6.2 主流程

```
orchestrator
   ↓
[evidence_case ∥ evidence_call_qc]  ←──────────────┐
   ↓                                                │
reflection ──证据不足且 loop < 3──> evidence_re_gather ─┘
   │
   ├─证据不足且 loop ≥ 3──> human_review
   ↓ 证据充分
judgment_fanout
   ├─ fact         （事实还原）
   ├─ compliance   （合规判断）
   └─ user_demand  （诉求/情绪）
   ↓
verdict（融合三方，只出草稿）
   ├─ pending_review / 降级 / 无草稿 ──> human_review
   └─ 高置信 ──> generate_package ──> human_review
                                          │
                        ┌─────────────────┼──────────────────┐
                   approve           reject（前 2 次）    escalate / 第 3 次 reject
                        ↓                 ↓                    ↓
                sample_backflow    evidence_re_gather    pending_senior_review
                        ↓
                     resolved
```

对应源码：[builder.py](services/complaint-service/app/graph/builder.py)、[edges.py](services/complaint-service/app/graph/edges.py)。

### 6.3 各 Agent 的分工与权限边界

| 节点 | 角色 | 职责 | 明确禁止 |
|---|---|---|---|
| orchestrator | 调度器 | 初始化状态、异常/超 Token 转人工 | 不调 LLM（纯代码） |
| evidence_case | 取证 Agent | 经 MCP 拉案件信息、历史工单、历史投诉 | 不做判责 |
| evidence_call_qc | 取证 Agent | 经 MCP 拉通话 ASR、质检违规，判断乱码/残缺 | 不判合规 |
| reflection | 反思 Agent | 评估证据是否充分、列出缺失项 | 不自行发起循环 |
| evidence_re_gather | 重新取证 | 按反思结果补充取证 | 不判责 |
| judgment_fanout | 扇出 | 触发三个研判 Agent 并行执行 | 不调 LLM（纯代码） |
| fact | 事实还原 Agent | 还原事件时间线 | 不判违规、不定责 |
| compliance | 合规 Agent | 先 RAG 查法规，再判是否违规 | 不做最终定责 |
| user_demand | 诉求 Agent | 分析诉求类型、情绪、二次投诉风险 | 不判责 |
| verdict | 综合判责 Agent | 融合三方输出，生成判责草稿 | 只有草稿权，无最终定责权 |
| generate_package | 产出物 Agent | 生成研判报告、答辩稿、改进建议 | — |
| human_review | 人工断点 | 挂起等人工 approve/reject/escalate | — |
| sample_backflow | 回写 | 把结果回写数据中台 | — |

**这个“分工 + 禁做”的设计是核心**：它强制每个 Agent 只做认知的一小步，避免单个 Agent 一步到位导致“幻觉污染整个结论”。事实、合规、判责被拆成三个独立 Agent，各自出结论，最后由 `verdict` 融合，再由人工兜底。

### 6.4 协作机制

**① 并行扇出与字段级合并**

- 初始取证时，`evidence_case` 和 `evidence_call_qc` 同时跑（一个查案件，一个查通话质检，互不依赖）。
- 进入研判后，`fact` / `compliance` / `user_demand` 同时跑。
- 并行写同一份 state 时，用自定义 reducer `merge_case_evidence` 做字段级合并，避免竞态丢数据（[state.py](services/complaint-service/app/models/state.py)）。

> 前端类比：多个并发请求都要往同一个 store 写数据，普通赋值会互相覆盖；reducer 就是那个“按字段合并”的 merge 函数。

**② 反思循环**

`reflection` 认为证据不足，就路由回 `evidence_re_gather` 重新取证，最多 `max_loops`（默认 3）次。超过上限后：

- 如果已有案件证据，强制进入研判，避免空答辩；
- 如果连案件证据都没有，转人工。

**③ 人工断点与断点续跑**

`human_review` 节点用 LangGraph 的 `interrupt` 把图挂起。挂起期间，图状态通过 checkpointer 持久化：

- 开发环境用 `MemorySaver`；
- 生产环境用 `AsyncPostgresSaver` 存 PostgreSQL；
- 服务重启后可以从断点继续，不丢已完成节点。

这落地了项目最高原则：**AI 只出草稿，人 approve 之后才真正回写、才算定责。**

### 6.5 恢复竞态守卫

图可以“挂起 → 恢复”，这会带来两个经典并发问题：

- 两个请求同时恢复同一张图；
- A 的锁过期 → B 拿到锁 → A 释放时误删 B 的锁。

实现位于 [graph_lock.py](services/complaint-service/app/service/graph_lock.py)：

- **三态返回值**：`acquired`（拿到）/ `held`（他人持有，跳过）/ `unavailable`（Redis 不可用，退化为进程内守卫）。区分“他人持锁”和“Redis 不可用”很重要，因为二者处理方式相反：前者跳过，后者继续执行。
- **释放用 Lua 原子 CAS**：`if get(key)==token then del(key)`，比对 token 才删，不做盲目 `DELETE`。
- **resume 锁分两段续期**：resume 分为“轮询等断点”和“执行 resume”两段；进入断点后、执行 resume 前显式续期，避免锁在执行段中途过期。

### 6.6 Schema 校验失败回灌

LLM 输出要过 Pydantic schema 强校验。模型偶尔会输出结构不对的 JSON。这里的处理不是“原样重试”，而是“把校验错误喂回给模型”（[provider.py](services/complaint-service/app/llm/provider.py)）：

```text
校验失败 → 把错误压缩成“字段 x 缺失 / 字段 y 类型错”（最多 10 条）
        → 作为一条新的 user message 追加到对话
        → 让模型定向修复，而不是盲目重采样
最多重试 3 次
```

普通重试是“同样的 prompt 再抽一次”，模型可能还是错；**回灌**是“告诉模型上次哪里错了”，命中率更高。这是一个很实用的 LLM 工程技巧。

### 6.7 前端视角

前端不需要理解 LangGraph 内部，只需要做四件事：

1. 触发研判；
2. 轮询 `/status`；
3. 展示节点时间线（节点名和状态由后端返回，前端不自造文案）；
4. 提交 `approve` / `reject` / `escalate`。

---

<a id="sec7"></a>
## 7. 前端接入契约

这一节是前端开发人员最需要直接使用的内容。所有格式均对照后端实现与前端请求层。

### 7.1 唯一入口

前端只认一个入口：

```text
/api/v1/*
```

由 Nginx 网关统一转发。前端**不感知**背后有几个服务、服务监听哪个端口。

- 开发环境 `ENABLE_AUTH` 可以为 `false`，方便本地调试；
- 生产环境必须设为 `true`，由 `auth_request` 子请求校验 JWT；
- 前端禁止直连 8001–8007 或 MCP 网关。

### 7.2 统一响应信封

所有接口（包括网关错误）都返回同一个外壳：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "complaint_id": "C20260001",
    "task_status": "pending_review",
    "progress": 100
  },
  "trace_id": "9f2c4e7a1b3d4c5e"
}
```

错误示例：

```json
{
  "code": "A5001",
  "message": "投诉工单不存在: C20260001",
  "data": null,
  "trace_id": "9f2c4e7a1b3d4c5e"
}
```

规则：

| 规则 | 说明 |
|---|---|
| 成功判定 | `code === 0` 或 `code === "0"`（历史遗留的 int/string 不一致，前端 `isSuccess()` 已兼容） |
| 错误码 | 字符串，如 `"B0002"`、`"A5001"`；禁止前端自造或改名 |
| `data` | 成功时为业务负载；失败时为 `null` |
| `trace_id` | 每个响应都有；错误提示只展示后 8 位 |
| 字段命名 | 保持 `snake_case`，前端不做 camelCase 自动转换 |
| 分页 | `{ items, total, page, page_size }` |
| HTTP 状态码 | 仍然使用（401/423/429/500 等），但响应体始终是信封；前端应按信封解析，不要只看 HTTP 状态 |

### 7.3 登录与鉴权

**登录**

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username": "operator", "password": "••••••"}
```

成功响应 `data`：

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_expires_in": 604800,
  "user": {
    "user_id": "0f3b...",
    "username": "operator",
    "real_name": "张三",
    "role": "agent",
    "status": "active"
  }
}
```

`expires_in` / `refresh_expires_in` 以配置为准（默认 access 24 小时、refresh 168 小时）。

**刷新**

```http
POST /api/v1/auth/refresh
Authorization: Bearer <refresh_token>
```

成功只返回新的 `access_token`、`expires_in`、`token_type`。

> **重要限制**：当前 refresh token 是**单次使用**的——刷新成功后服务端会吊销它，且不返回新的 refresh token。第二次刷新需要重新登录。这是当前实现的边界，前端不要在 401 循环里无限重试 refresh。

**前端存储约定**

- access token 只放内存 + `sessionStorage`，禁止 `localStorage`；
- 401 / `B0002` → 清登录态 → 跳 `/login?redirect=<当前路径>`；
- `423` / `B0011` → 展示“账号已锁定，请 15 分钟后重试”；
- 角色只用于隐藏入口，真正的权限判定以后端为准。

### 7.4 长任务：触发 + 轮询

以投诉研判为例。

**第一步：触发**

```http
POST /api/v1/complaints/C20260001/judge
Content-Type: application/json

{"run_graph": true}
```

可能的返回：

| 情况 | 返回 |
|---|---|
| 已受理并开始跑图 | `200`，`data.message = "judge accepted (graph)"` |
| 已在处理中（幂等） | `200`，`data.message` 说明 in progress / done |
| 排队等待执行槽 | `200`，`data.message = "judge queued (waiting for execution slot)"` |
| 队列满、进入延迟队列 | `202`，`data.message = "judge queued in deferred backlog"` |
| 同工单重复提交 | `B0005`“同工单正在处理中，请勿重复提交” |
| 队列满且无法排队 | `B0006`“排队队列已满，请稍后重试” |

`data` 形状：

```json
{
  "complaint_id": "C20260001",
  "task_status": "evidence_gathering",
  "verdict": null,
  "thread_id": "C20260001",
  "message": "judge accepted (graph)"
}
```

**第二步：轮询状态**

```http
GET /api/v1/complaints/C20260001/status
```

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "complaint_id": "C20260001",
    "task_status": "pending_review",
    "progress": 100
  },
  "trace_id": "9f2c4e7a1b3d4c5e"
}
```

状态枚举：

| `task_status` | 含义 | 是否继续轮询 |
|---|---|---|
| `pending` | 已建单，未启动研判 | 是 |
| `evidence_gathering` | 取证中 | 是 |
| `reflection_retry` | 反思重试 | 是 |
| `judging` | 研判中 | 是 |
| `draft_generated` | 草稿已生成 | 是 |
| `pending_review` | **待人工复核（AI 已完成）** | 否，展示复核界面 |
| `pending_senior_review` | 待高级复核 | 否 |
| `pending_callback` | 定责完成，待回电处置 | 否 |
| `resolved` | 已解决 | 否 |
| `closed` / `archived` | 已关闭 / 已归档 | 否 |

**第三步：拉取结论**

```http
GET /api/v1/complaints/C20260001/verdict
```

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "complaint_id": "C20260001",
    "verdict": "agent_violation",
    "confidence": 0.86,
    "response_draft": "经核查，坐席在通话中存在……",
    "regulation_refs": ["REG-2026-018"],
    "rag_source_ids": ["doc_7f3a", "doc_91c2"],
    "evidence_refs": ["doc_7f3a", "doc_91c2"]
  },
  "trace_id": "9f2c4e7a1b3d4c5e"
}
```

判责枚举：`agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`。

**第四步：提交复核**

```http
PUT /api/v1/complaints/C20260001/review
Content-Type: application/json

{
  "review_action": "approve",
  "reviewer": "operator",
  "comment": "同意判责",
  "reason": null
}
```

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "complaint_id": "C20260001",
    "task_status": "resolved",
    "review_action": "approve"
  },
  "trace_id": "9f2c4e7a1b3d4c5e"
}
```

`reject_rejudge` 与 `modify_*` 必须提供 `reason`；否则后端返回参数错误。

### 7.5 超时：各层是独立上界，不是严格嵌套

| 层级 | 数值 | 作用域 |
|---|---|---|
| 前端普通请求 | 12s | `TIMEOUT.normal` |
| 网关普通路由 | 15s | `/api/v1/*` 默认 |
| 前端研判 / 质检回放 | 110s | `TIMEOUT.judge` / `TIMEOUT.qcReplay` |
| 网关 judge / qc replay | 120s | 两个专用正则 location |
| MCP 单次工具调用 | 30s | Agent 取证 |
| 投诉 LLM 单次调用 | 120s | 单次 `invoke_llm` |
| 投诉任务全局 | 1800s | 图执行上限 |
| 账号锁定 | 15min | 连续 5 次登录失败 |

关键理解：

- **网关 120s 是 HTTP 响应上界**，不是后端任务上界。后端图仍可能继续执行到 1800s，前端应以 `/status` 的最终态为准。
- **单次 LLM 120s 与网关 120s 数值相同，但不是同一层**：网关的 120s 覆盖整张图，单次 LLM 的 120s 只是单次调用上限。实际运行时，网关通常先到点。
- 前端 110s 比网关 120s 少 10s，是为了留出网络与渲染余量。

### 7.6 错误码与 UI 行为

| 错误码 | 含义 | 前端行为 |
|---|---|---|
| `B0001` | 参数校验失败 | 展示 `message`，修正表单 |
| `B0002` | 未认证 / 凭证错误 | 清登录态，跳登录页 |
| `B0003` | 无权限 | 展示无权限页，不重试 |
| `B0004` | 资源不存在 | 展示空状态 |
| `B0005` | 重复操作 | 禁用按钮，提示勿重复提交 |
| `B0006` | 限流 | 读 `Retry-After`，退避重试 |
| `B0007` | 系统内部错误 | 展示通用错误 + trace 后 8 位 |
| `B0008` | 网关错误 | 展示通用错误，可重试 |
| `B0009` | 服务暂不可用 | 展示维护提示 |
| `B0010` | 合规策略拦截 | 展示拦截原因，不提供重试 |
| `B0011` | 账号已锁定 | 提示 15 分钟后重试 |
| `A3004` | 检索相关性不足 | 展示“未找到相关内容”空状态 |
| `A5001` | 投诉工单不存在 | 返回列表或空状态 |
| `A5002` | 工单状态已变更 | 刷新详情，不要覆盖 |
| `A5015` / `A5016` / `A5017` | Token 超限 / 超时 / LLM 熔断转人审 | **正向提示“已转人工审核”**，不要弹失败 |

前端错误码白名单在 [error-map.ts](frontend/src/shared/utils/error-map.ts)，禁止前端新增或重定义错误码。

### 7.7 轮询与并发的四条规则

1. **不要重复点“触发研判”**：后端有幂等守卫，重复请求会返回当前状态或 `B0005`，但前端仍应禁用按钮。
2. **轮询要退避**：建议 2s 起，逐步放宽到 5–10s；不要固定 200ms 高频轮询。
3. **收到 504 不要立刻重试**：网关超时不等于后端任务失败。先查 `/status`，再决定是否重试。
4. **保留 `trace_id`**：错误提示只显示后 8 位，完整值留在控制台或请求日志里，方便和后端对日志。

### 7.8 给前端开发人员的六条硬边界

1. **前端永远只认网关一个入口** `/api/v1/*`。
2. **长任务 = 触发 + 轮询**，不要用普通请求的心态等结果。
3. **AI 的产物是“草稿”**：`pending_review` / 转人工是正常业务结果，不是报错。
4. **`trace_id` 是排查一切的钥匙**，错误提示带后 8 位。
5. **前端不复制后端业务规则**：显示什么、能不能操作，以后端字段和错误码为准。
6. **不要自造节点名、状态名、错误码**：全部来自后端枚举。

---

<a id="sec8"></a>
## 8. 技术栈与选型理由

### 8.1 技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| 语言 / 运行时 | Python 3.11 + FastAPI + Uvicorn | 后端统一框架，异步 |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic | 数据访问 + 版本迁移 |
| 数据库 | PostgreSQL 15 | `public` 业务 schema + `dw` 数仓 schema |
| 缓存 / 计数 | Redis 7 | 缓存、限流计数、qc 告警流（XADD）；**跨服务队列不用 Redis** |
| 向量库 | Milvus 2.4 | 稠密向量检索 |
| 全文检索 | Elasticsearch 8 | BM25 稀疏检索 |
| Embedding | BGE-M3（1024 维，本地 :8100） | 文本向量化 |
| Rerank | bge-reranker-large（本地 :8101） | 精排重排 |
| AI 编排 | LangChain + LangGraph + langgraph-checkpoint-postgres | 多 Agent 图 + 断点续跑 |
| 大模型 | 阿里云 DashScope qwen-plus（OpenAI 兼容协议） | 主模型；支持 deepseek 等 fallback |
| 事件总线 | RocketMQ | 跨服务异步通知的唯一模式 |
| Agent 协议 | MCP（Model Context Protocol） | Agent 取证的统一协议 |
| 网关 | Nginx :8080 | 统一入口 |
| 部署 | Docker Compose | 单机编排（内部系统，不上 K8s） |
| 前端 | Vue3 + TS + Vite + Pinia + Element Plus + ECharts | 前端管理台 |
| 可观测 | Prometheus + Grafana + Langfuse / LangSmith（可选开关） | 指标 + LLM 链路追踪 |

### 8.2 关键选型理由

| 决策 | 选择 | 主要理由 | 代价 / 边界 |
|---|---|---|---|
| 多 Agent 编排 | LangGraph | 原生支持状态图、`interrupt`、checkpoint；适合“长任务 + 人工断点” | 学习曲线高；图状态设计需要谨慎 |
| 检索 | Milvus + ES 双系统 | 语义召回与精确 ID 召回互补 | 两套系统运维成本；分数融合需要 RRF |
| 跨服务事件 | RocketMQ | 可靠投递、消费组、重试能力；当前规模足够 | 运维复杂度高于 Redis Pub/Sub |
| 部署 | Docker Compose | 500 人内部单租户，单机足够；无 K8s 运维负担 | 无滚动发布、无多副本高可用 |
| 大模型 | DashScope qwen-plus | 成本与中文效果平衡；OpenAI 兼容协议便于切换 | 外部依赖；需要限流、熔断、fallback |
| Agent 工具协议 | MCP | 工具边界统一、可审计、下游变更透明 | 需要独立网关与协议适配层 |

---

<a id="sec9"></a>
## 9. 工程底座：韧性、安全与可观测

### 9.1 LLM 韧性编排

实现在 [reliability.py](common/llm/reliability.py) 的 `invoke_llm`，一条链：

```text
选 Key → 限流 → 熔断（三态）→ 有界重试 → fallback 换模型 → 降级
```

| 机制 | 行为 |
|---|---|
| 限流 | 模型维度 `{svc}:llm_ratelimit:{model}`，60 次 / 60 秒 |
| 熔断 | 三态机（closed / open / half-open），失败到阈值快速拒绝，避免雪崩 |
| 重试 | 只对业务错重试 3 次（指数退避）；**超时不重试**，因为超时重试只会更堵 |
| fallback | 主模型失败自动换备用模型链（如 qwen-plus → deepseek） |
| 降级 | 全链失败不伪造结果，返回 `degraded`，由节点走规则兜底 + 转人工 |
| 温度 | 评测/回归固定 `temperature=0.1 + seed=42`；生产默认 `0.3`，可配 0.1–0.5 |
| Token | 单次会话累计 token 上限可配，超限转人审（`A5015`） |

**失败路径决策**：

| 失败类型 | 重试 | fallback | 最终结果 |
|---|---|---|---|
| 业务错（可重试错误） | 3 次指数退避 | 是 | 下一模型 |
| 超时 | 否 | 是 | 下一模型；全链失败则 degraded |
| 熔断打开 | 否 | 是 | 下一模型；全链失败则 degraded |
| 限流拒绝 | 否 | 否 | `LLMRateLimitError` → 节点转人审 |
| Schema 校验失败 | 3 次回灌修复 | 否 | `LLMCallError` → 降级 |

**已知缺口**：`complaint-service` 的 `real_transport.py` 仍自写限流与重试，绕过 `invoke_llm`，没有 fallback/统一降级结构，会影响 LLM 用量统计口径（详见附录 B·稳定性）。

### 9.2 安全

| 层面 | 机制 | 现状 |
|---|---|---|
| 接入认证 | Nginx `auth_request` → auth 侧车验 JWT | 已实现；`ENABLE_AUTH` 默认 false，生产必须开启 |
| 服务授权 | qc/rag 已有角色校验；complaint 仍缺角色级授权 | **complaint-service 存在真实越权风险**（详见附录 B·安全性） |
| 数据脱敏 | MCP 出口脱敏 + Agent 侧二次校验 | 双层，已实现 |
| 行级权限 | RAG 检索按角色过滤，异常时 fail-closed | 已实现 |
| 密钥管理 | `.env` 不入库；sops + age 加密 `.env.enc` 入库 | 已实现 |
| CoT 加密 | `common/security/crypto.py` 有真实 AES-256-GCM | 原语真实；投诉路径仍填 base64 占位串，端到端未闭环（详见附录 B·安全性） |
| 合规校验 | RAG 侧前后置校验；complaint 侧 L4 后置校验；qc 侧 AC 适配 | **不同服务范围不同**，不能笼统说“全系统前后置” |
| 审计 | 前端错误审计端点、管理员 CRUD、登录锁定 | 已实现 |

**登录锁定的副作用**：连续 5 次密码错误会锁定账号 15 分钟。攻击者可以用已知用户名故意触发锁定，形成“锁号 DoS”。这是锁定机制的固有 tradeoff，当前没有按 IP/设备维度区分（详见附录 B·安全性）。

### 9.3 可观测性

**链路追踪**

```text
前端 X-Trace-Id
  → Nginx 注入/透传
  → 业务服务
  → MCP 网关
  → LLM 调用
```

- 每个响应都返回 `trace_id`；
- 前端错误提示只显示后 8 位；
- LLM 调用可选接入 Langfuse 或 LangSmith（环境变量开关，互不强制）。

**指标与日志**

- 8 个服务统一暴露 `/metrics`，Prometheus 配置 9 个抓取 job，Grafana 有内置 dashboard；
- LLM 调用有结构化日志与指标族（限流、熔断、fallback、token 用量）；
- 网关有 `stub_status` 与 JSON 访问日志开关。

**排查一次问题的顺序**

1. 拿错误提示里的 `trace_id` 后 8 位，在网关访问日志里找完整值；
2. 用完整 `trace_id` 搜业务服务日志；
3. 看 Langfuse/LangSmith 里对应的 LLM 调用链；
4. 看 MCP 网关的调用记录（哪个 server、哪个工具、耗时、是否降级）；
5. 看投诉会话的 agent logs 与状态时间线；
6. 如果返回 `degraded`，查熔断/限流指标，确认是限流、熔断还是全链失败。

**当前缺口**：日志仍在容器内，容器重建即丢；没有 filebeat/vector → ELK/Loki；Prometheus 告警未接飞书/钉钉；没有 runbook（详见附录 B）。

---

<a id="sec10"></a>
## 10. 部署、容量与演进

### 10.1 部署形态

- 编排方式：Docker Compose，单机部署；
- 编排文件：`docker-compose.yml`（基础）、`docker-compose.dev.yml`（开发）、`docker-compose.gateway.yml`（网关 + auth 侧车）、`docker-compose.complaint.yml`（投诉服务生产形态）、`docker-compose.smoke-services.yml`（冒烟）；
- 网络：共享 `ai-shared-net`，upstream 用容器服务名解析；
- 对外只暴露 Nginx 与少量调试端口，数据库、MCP 网关、auth 侧车均不直接对外。

### 10.2 容量与瓶颈

| 资源 | 关注点 |
|---|---|
| Milvus / Elasticsearch | 内存占用最高；索引规模增长后需要单独规划 |
| embedding / rerank | 本地模型推理，CPU 占用高；并发检索时是瓶颈 |
| PostgreSQL | 业务库 + 数仓 schema + 图 checkpoint；需要备份与连接池管理 |
| Redis | 限流、缓存、图锁、告警流；不能当跨服务消息队列 |
| 大模型 | 外部 API，延迟与配额是主要不确定性 |

**扩容路径**：

1. 先纵向扩容（CPU/内存），单机仍是最简单的形态；
2. 再考虑服务副本，但必须保证图锁、checkpoint、事件消费的共享状态正确；
3. 出现多租户、需要滚动发布或高可用时，再评估 K8s。

### 10.3 环境与发布

| 项 | 现状 | 缺口 |
|---|---|---|
| 环境分离 | 一份 `.env` 跑所有环境 | dev/staging/prod 未分离（详见附录 B） |
| 发布 | 手工 `docker-compose up` | 无 CD、无滚动发布、无审计（详见附录 B） |
| 灰度 | `CANARY_WEIGHT=0` | 开关已备好，单版本无需求 |
| 备份 | PG / Milvus / ES 备份脚本，保留 7 天 | 已有；恢复演练未常态化 |
| 密钥 | sops + age 加密 `.env.enc` | 已实现 |

### 10.4 里程碑与路线图

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 基础底座 | 完成 | common/ + 网关 + 事件总线 + 数据库迁移 |
| M1 质检系统 | 完成 | qc-service 闭环参与 |
| M2 外呼 / 分案 | 未开始 | call-service / case-service 仍是脚手架 |
| M3 投诉研判 | 完成 | complaint-service 深度实现 + 事件闭环 |
| M3.5 投诉 V2 | 完成 | real-llm + 事件化 + 图状态机修复 |
| M4 复盘 | 未开始 | review-service 仍是脚手架 |
| 生产上线硬门槛 | 完成 | 六项：MCP 独立网关、CI 全服务覆盖、密钥管理、备份脚本、指标接入、LLM 用量可观测 |

> 路线图以 [docs/roadmap.md](docs/roadmap.md) 为准，本文只做摘要。路线图是内部进度台账，可能滞后于代码，引用前需核对。

---

<a id="sec11"></a>
## 11. 工程成熟度与已知边界

这一节回答“当前实际做到什么程度”，如实说明。详细清单见附录 B。

### 11.1 成熟度记分卡

| 维度 | 状态 | 依据 |
|---|---|---|
| 核心业务闭环 | 扎实 | 4 个服务深度参与，11/11 最小闭环验收通过 |
| LLM 韧性 | 扎实 | 限流、熔断、重试、fallback、降级全链路 |
| 事件闭环 | 扎实 | RocketMQ 唯一模式 + 信封白名单 + 幂等消费 |
| 多 Agent 编排 | 扎实 | 13 节点、并行扇出、反思循环、人工断点、断点续跑 |
| RAG 混合检索 | 扎实（工程）/ 待补（效果） | 管线完整、降级路径清晰；准确率/召回率尚无实测基线 |
| 安全 | 部分 | PII、密钥、审计做得好；complaint 越权与 CoT 端到端是缺口 |
| 可观测 | 部分 | 指标与 trace 已接入；日志采集、告警、runbook 缺失 |
| 交付工程化 | 部分 | CI 完善；CD、环境分离缺失 |
| 业务完整性 | 部分 | 外呼、分案、复盘三个服务未实现 |
| 效果量化 | 待补 | 没有检索、研判、成本的可复核基线 |

### 11.2 五个优先风险

1. **complaint-service 缺角色级授权（高）**：任意已登录用户都能 review/close 任意投诉工单。这是真实越权风险，应优先补 `require_role`。
2. **CoT 加密端到端未闭环（中）**：加密原语真实，但投诉路径仍是 base64 占位串；“双人授权解密”只在服务层有定义，端到端是否强制生效未验证。
3. **运维三件套缺失（中）**：CD、日志采集、告警通知都没有，出问题只能人工上机器看。
4. **效果指标缺失（中）**：RAG 准确率、研判耗时、人工替代率没有基线，对外介绍只能讲设计目标，不能讲结果。
5. **refresh token 单次使用（中低）**：刷新一次后即失效且不下发新 refresh token，用户第二次需要重新登录；前端 401 刷新链路会遇到这个边界。

### 11.3 已经做扎实的

- 密钥管理：`.env` 不入库，sops + age 加密 `.env.enc` 入库；
- 跨服务事件：RocketMQ 唯一模式 + 信封白名单 + 幂等消费；
- Agent 取证：强制 MCP 协议，下游变更对 Agent 透明；
- LLM 韧性：限流 → 熔断（三态）→ 有界重试 → fallback → 降级；
- PII 脱敏：MCP 出口 + Agent 侧二次校验双层；
- 测试与规范：1402 passed / 0 failed，6 个 CI job，OpenSpec 规范驱动。

**一句话总结**：底座（common/ 韧性编排、事件闭环、MCP、RAG、多 Agent）已经扎实；最该优先补的是 **① complaint 角色授权、② CoT 加密端到端、③ CD + 日志 + 告警的运维三件套**。

---

<a id="secA"></a>
## 附录 A. 术语表

| 术语 | 通俗解释 |
|---|---|
| LLM | 大语言模型，本系统用阿里云 DashScope 的 qwen-plus |
| Prompt | 给模型的指令与上下文 |
| Token | 模型计费和上下文长度的单位；中文约 1 字 ≈ 1–2 token |
| RAG | 检索增强生成：先查资料，再让模型基于资料回答 |
| Embedding | 把文本转成向量（一串数字），用于语义相似度计算 |
| 稠密检索 | 用向量算语义相似度，代表是 Milvus |
| 稀疏检索 / BM25 | 用关键词和词频算相关性，代表是 Elasticsearch |
| 混合检索 | 同时走向量和关键词两路，再融合 |
| RRF | 倒数排名融合：不看两路各自打多少分，只看排名，按 `1/(k+rank)` 折算后相加 |
| Rerank | 对召回结果做更精细的语义重排 |
| Chunk | 文档切片，向量化和检索的基本单位 |
| 多模态（本文口径） | 入库侧支持多种文件格式；不是“图片搜图片”的跨模态对齐 |
| Milvus | 向量数据库 |
| Elasticsearch | 全文检索引擎 |
| Agent | 只做一件认知任务的 AI worker |
| LangGraph | 用图组织多个 Agent、状态和条件分支的编排框架 |
| Node / Edge / State | 节点 / 路由 / 共享状态 |
| Reducer | 多个并行写操作合并同一状态字段的合并函数 |
| Checkpointer | 把图状态持久化，支持暂停、重启、续跑 |
| Interrupt | 在节点处暂停，等待外部输入 |
| Human-in-the-Loop | 人工在环：AI 出草稿，人确认后才生效 |
| MCP | Model Context Protocol，Agent 调用工具的标准化协议 |
| 侧车 | 与主服务伴生、只提供某项横切能力的小服务（如 auth-sidecar） |
| 熔断 | 下游连续失败时快速拒绝请求，避免雪崩；三态：closed / open / half-open |
| 限流 | 限制单位时间内的请求量 |
| Fallback | 主模型失败时切换备用模型 |
| 降级 / degraded | 全链失败时返回明确标记的降级结果，不伪造成功 |
| PII | 个人身份信息，如姓名、手机号、身份证、银行卡 |
| ASR / TTS | 语音识别 / 语音合成，分别用于通话转写与语音播报 |
| OCR | 图片或扫描件的文字识别 |
| P95 | 延迟分位数：95% 的请求耗时不超过该值 |
| CoT | 思维链，模型的推理过程 |
| JWT | JSON Web Token，登录后的身份凭证 |
| RBAC | 基于角色的访问控制 |
| 事件总线 | 服务间异步消息通道，本系统用 RocketMQ |
| 信封 | 统一消息外壳：`{code, message, data, trace_id}` |
| 幂等 | 同一操作重复执行不会产生额外副作用 |
| DLQ | 死信队列，处理失败的消息最终去处 |
| 对账 | 对比上下游数据，发现并修正不一致 |
| 工单 / 投诉工单 | 一次投诉受理单；本文中“工单”均指投诉工单 |
| 研判 | 投诉取证、分析、形成判责草稿的全过程 |
| 判责 | 对责任归属作出结论；本文中 AI 只出判责草稿，最终由人工确认 |
| 回写 / 回流 | 把处理结果写回数据中台；“快照回流”指数据中台回流业务快照 |
| 脚手架服务 | 仅有 `/health` 接口、尚未实现业务逻辑的服务 |
| 扇出 | 一个节点并行触发多个下游节点 |
| 兜底 | 主路径失败时的保底处理，本文指降级、规则兜底或转人工复核 |
| OpenSpec | 项目的规范驱动开发流程：变更先写 spec，再实现，CI 门禁校验 |
| AC 自动机 | Aho-Corasick 多模式匹配算法，用于敏感词毫秒级命中 |
| L4 校验 | 项目 PRD 中的第 4 层后置合规校验：幻觉检测、来源验证、红线拦截 |
| R1–R14 | 质检规则集，项目内部规则编号 |

---

<a id="secB"></a>
## 附录 B. 详细缺陷清单

严重度图例：🔴 高 / 🟡 中 / 🟢 低 / ✅ 已修复。

### B.1 安全性

| 问题 | 严重度 | 现状 |
|---|---|---|
| **complaint-service 接口无角色级授权**：只有认证（网关验 JWT），没有角色校验；任意已登录用户都能 review/close 任意投诉工单 | 🔴 高 | qc/rag 已有角色校验，complaint 缺失；真实越权风险 |
| **CoT 加密端到端未闭环**：真实 AES-256-GCM 原语存在，但投诉路径填的是 base64 占位串；双人授权解密只在服务层有定义 | 🟡 中 | 需补齐“占位串 → 真加密”+ 端到端验证；路线图标为完成，代码尚未闭环 |
| **登录锁定存在锁号 DoS**：攻击者用已知用户名连续错 5 次即可锁住他人账号 | 🟡 中 | 锁定机制的固有 tradeoff，当前无 IP/设备维度区分 |
| **成功码 int vs string 不一致**：auth/complaint 返回 `0`，qc/rag 返回 `"0"` | 🟢 低 | 前端 `isSuccess()` 已兼容；建议后端统一为 int 0 |
| **refresh token 单次使用且不滚动续发**：刷新后吊销当前 refresh token，不返回新 token | 🟡 中 | 第二次刷新需重新登录；前端不应无限重试 refresh |
| ~~无 refresh token / 网关孤儿路由 / 账号锁定缺失 / admin CRUD 缺失~~ | ✅ | 提交 `0f3b9a9` 已修复：refresh token、B0011 锁定、admin 用户 CRUD、审计端点、网关孤儿路由清理 |

### B.2 稳定性 / 可靠性

| 问题 | 严重度 | 说明 |
|---|---|---|
| **real_transport.py 绕过 `invoke_llm`**：自写限流+重试，无 fallback/降级/统一返回结构 | 🟡 中 | 影响 LLM 用量统计口径 |
| **内部队列未升级 Redis Stream**：ACK 不丢任务 / 多 worker 竞争 / 失败重试 / DLQ / 对账补偿未做 | 🟡 中 | 注意：这是**进程内队列**；Redis Stream 目前只用于 qc 告警流，跨服务事件走 RocketMQ |
| 灰度路由未真正启用（`CANARY_WEIGHT=0`） | 🟢 低 | 开关已备好，单版本无需求 |
| 日志采集缺失：日志在容器里，容器重建即丢 | 🟡 中 | 无 filebeat/vector → ELK/Loki |
| 告警通知缺失：Prometheus → 飞书/钉钉未接 | 🟡 中 | 无主动通知渠道 |

### B.3 工程化

| 问题 | 严重度 | 说明 |
|---|---|---|
| CD 流水线缺失：靠手工 `docker-compose up` | 🟡 中 | 无审计、无滚动发布 |
| 环境分离缺失：一份 `.env` 跑所有环境 | 🟡 中 | 密钥管理已做：明文 `.env` 已 gitignore，sops 加密 `.env.enc` 入库 |
| runbook / 应急手册缺失 | 🟡 中 | 故障处置缺少标准流程 |
| LLM 独立网关未做 | 🟢 低 | common 已有完整编排器，独立化暂缓（P2） |

### B.4 业务完整性

| 问题 | 严重度 | 说明 |
|---|---|---|
| call / case / review 三个服务是脚手架（仅 health） | 🟡 中 | M2/M4 里程碑未做，外呼/分案/复盘功能缺失 |
| 敏感词无 CRUD 接口（只有读词典 + 刷新） | 🟢 低 | 前端页面据此收敛为只读 |
| 知识库管理台 / 运营监控台 / 审计合规台 / 通知中心未做 | 🟢 低 | P2 可延后项 |

### B.5 指标与文档

| 问题 | 严重度 | 说明 |
|---|---|---|
| RAG / LLM 效果指标无实测基线 | 🟡 中 | 只有设计目标（top-3 ≥85%、召回 ≥90%），Mock 环境未评测 |
| 路线图与代码存在状态差异 | 🟢 低 | 例如 CoT 加密在路线图标完成，代码仍是占位串；引用前需核对代码 |
| 容量与性能基线缺失 | 🟢 低 | 无生产流量下的 P95、并发、token 成本数据 |

---

<a id="secC"></a>
## 附录 C. 事实校对与代码索引

### C.1 本次修订修正的 5 处口径

| # | 原文档口径 | 实际代码 | 修订结果 |
|---|---|---|---|
| 1 | Redis 7 用于“缓存、限流计数、Stream 队列” | 只有 qc 告警流用 `XADD`；跨服务队列是 RocketMQ；内部队列未升级 Redis Stream | 技术栈表改为“缓存、限流计数、qc 告警流；跨服务队列不用 Redis” |
| 2 | “MCP 网关 :9000”，未区分服务内 client 与独立服务 | `services/mcp-gateway` 是独立容器；`complaint-service/app/mcp/gateway.py` 是连接管理/client；dev 宿主机映射 9009→9000 | §2、§6 明确独立服务、端口映射、Mock/真实双模式 |
| 3 | “MCP 30s < LLM 120s < 全局 30min，逐级放宽” | 网关 judge 也是 120s；LLM 120s 是单次调用；1800s 是后端任务上限；三者作用域不同 | §7.5 改为“各层独立上界，不是严格嵌套”，并解释网关超时不等于任务失败 |
| 4 | “Guardrails 前后置校验”作为全系统能力 | RAG 侧同时有 precheck + postcheck；complaint 目前只接后置 postcheck；qc 走 AC 适配 | §9.2 明确不同服务的范围差异 |
| 5 | §4.1 图中“高置信”分支含义不明，读者可能理解为可跳过人审 | `should_proceed_after_verdict` 两条路径最终都到 `human_review`；高置信只是先生成报告 | §1、§4.3 明确“高置信不跳过人审，只改变顺序” |

### C.2 关键结论与代码位置

| 结论 | 代码位置 |
|---|---|
| 统一信封 `{code, message, data, trace_id}`，成功码 int 0 | [response.py](services/complaint-service/app/api/response.py)、[handlers.py](common/errors/handlers.py) |
| 任务状态枚举 11 个 | [state.py](services/complaint-service/app/models/state.py) |
| 复核动作 6 个 | [state.py](services/complaint-service/app/models/state.py) |
| verdict 后两条路径都到 human_review | [edges.py](services/complaint-service/app/graph/edges.py) |
| 反思上限 3、驳回第 3 次强制升级 | [state.py](services/complaint-service/app/models/state.py)、[review_service.py](services/complaint-service/app/service/review_service.py) |
| 图节点 13 个、3 个研判 Agent 并行 | [builder.py](services/complaint-service/app/graph/builder.py) |
| MCP 4 个合法 server | [gateway.py](services/complaint-service/app/mcp/gateway.py) |
| MCP 独立容器、dev 端口 9009→9000 | [docker-compose.dev.yml](docker-compose.dev.yml) |
| RRF k=60、闸门 0.6 / 0.35 | [rag_settings.py](services/rag-service/app/rag_settings.py)、[search_service.py](services/rag-service/app/services/search_service.py) |
| CoT 加密原语真实，投诉路径为占位串 | [crypto.py](common/security/crypto.py)、[provider.py](services/complaint-service/app/llm/provider.py) |
| refresh token 单次使用 | [deploy/auth/main.py](deploy/auth/main.py) |
| 前端超时 12s / 110s、错误码白名单 | [request.ts](frontend/src/shared/request.ts)、[error-map.ts](frontend/src/shared/utils/error-map.ts) |
| 网关超时分级 3s / 15s / 120s | [server_common.conf.template](deploy/gateway/server_common.conf.template) |
| 事件白名单 5 个字段 | [envelope.py](common/eventbus/envelope.py) |
| Redis Stream 仅 qc 告警流 | [publisher.py](services/qc-service/app/events/publisher.py) |
| Guardrails 使用范围 | [llm_service.py](services/rag-service/app/services/llm_service.py)、[guardrails_postcheck.py](services/complaint-service/app/service/guardrails_postcheck.py) |

### C.3 可进一步深入的主题

- 逐行拆解 `search_service.py` 的 RAG 管线，配每一步的输入输出示例；
- 画一张 complaint-service 的完整时序图，含 MCP 调用与 LLM 调用的详细往返；
- 拆解 `common/llm` 的熔断/重试/降级三态机实现与测试用例；
- 建立 RAG 黄金集与研判耗时基线，把 §5.6 的设计目标变成实测结果；
- 补齐 complaint 角色授权与 CoT 端到端加密的验收用例。
