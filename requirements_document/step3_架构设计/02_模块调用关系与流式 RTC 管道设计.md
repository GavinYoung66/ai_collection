# 模块调用关系与流式 RTC 管道设计

> 文档类型：架构设计（规范层）
> 版本：v1.10
> 状态：定稿
> 更新日期：2026-09-09
> 约束声明：遵循 step1/01 全局统一口径与 openspec/specs/01、02；服务名/端口/响应包/trace_id 以 step1/01 为准。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅调用关系与 RTC 管道 | — |
| v1.0 | 2026-08-19 | 补跨模块调用矩阵、通信协议、实时/离线数据流图、X-Trace-Id 全链路追踪、通话状态机 | 规范定稿 |
| v1.1 | 2026-09-08 | 集成分层：complaint Agent 取证改为 MCP；协议表补 Agent/MCP（platform-integration-boundaries） | Ellis Guo |
| v1.2 | 2026-09-08 | 澄清 call→rag 为 HTTP 调 rag-service；补充 2 深切片实现深度口径 | Ellis Guo |
| v1.3 | 2026-09-09 | P1-16 §二跨模块调用矩阵 qc-service→rag-service 接口由 /kb/documents?category=regulation 修正为 /api/v1/sensitive-words/current.dict（法规文档检索与敏感词库分离，决策A执行） | Ellis Guo |
| v1.4 | 2026-09-09 | P1-17 §二 complaint→qc MCP call_qc 方法名统一为 canonical 三方法清单 query_call_sessions / query_call_asr / query_qc_violations（对齐 services/complaint-service 实际交付代码，消除"等"歧义；数据面归属：query_call_asr / query_call_sessions 由 call-service 提供，qc-service 仅实现 query_qc_violations 数据面） | Ellis Guo |
| v1.5 | 2026-09-09 | P2-25 §4.3 强制埋点表 dws_sentiment_trend → dwd_sentiment_trend（对齐 step4/01 v1.9 迁移 DWD 明细层） | Ellis Guo |
| v1.6 | 2026-09-09 | 续批次（批次2 残留收口）：§二跨模块调用矩阵 RAG score<0.6 补精排后最终融合分数口径回链（对齐 step4/02 §四.1）；§4.1 时序图“生成应答(temperature=0.1)”改为温度分层口径（生产默认 0.3/0.1~0.5，评测 0.1 仅离线，对齐 step5/03 §6.2） | Ellis Guo |
| v1.9 | 2026-09-09 | 对齐 DD-02 V1.5：删除 call→qc 实时 ingest/WS；质检仅 ended 后离线 | Ellis Guo |
| v1.10 | 2026-09-09 | §六 trace 贯穿链去实时质检（改 ASR→意图→LLM→Guardrails→TTS，质检为通话结束离线环节，以同 session_id/trace_id 串 qc_records）；§二 MCP call_qc 补 query_qc_alerts 调 qc-service 并澄清 query_call_asr/query_call_sessions 数据面属 call-service；§三 异步通知 channel 例 qc.alert.{agent_id} 改广播 qc.alert.raised（仅事后下发主管） | 规范复审 |

## 一、模块调用关系

1. case-service 输出策略配置 → call-service；
2. call-service 经 **HTTP** 调 **rag-service** `/kb/search` 检索话术（非 `common` 内嵌 RAG 库）；
3. call-service 会话结束发布 `call.session.ended`；qc-service **订阅后**拉取全文做离线质检（文本未齐则等待 `etl.task.done`）；同时事件喂入 review-service、data-platform；
4. review-service 输出画像更新 → data-platform，反向供给 case-service；
5. complaint-service 接收投诉/通话文本，输出研判结果落库 + 推送 data-platform。

> 禁止反向强依赖：qc-service 不得直接调用 call-service；review-service 不得直接修改案件状态；跨模块通信走事件/中台接口。

## 二、跨模块调用矩阵

| 调用方 | 被调方 | 方式 | 接口 | 说明 |
|---|---|---|---|---|
| call-service | rag-service | HTTP | /kb/search | 话术检索，score<0.6 走兜底（score 口径 = 混合检索经 bge-reranker 精排后的最终融合分数，见 step4/02 §四.1） |
| call-service | qc-service | 事件（qc 订阅） | `call.session.ended`；qc 再 HTTP 拉 DP ODS | **无**通话中 HTTP/WS。全文齐后离线质检 |
| call-service | data-platform | 事件 | 通话结束事件 | 喂入通话数据 |
| qc-service | rag-service | HTTP | /api/v1/sensitive-words/current.dict | 加载敏感词库（AC 自动机字典下载） |
| case-service | data-platform | HTTP | /data/features/{case_id} | 取用户特征 |
| complaint-service | rag_kb（MCP）→ rag-service | MCP（内部可 HTTP） | rag_kb.search_regulations 等 | Agent 取证/合规知识；节点不直连 rag HTTP；MCP Server 可再调 /kb/* |
| complaint-service | call_qc（MCP）→ qc-service / call-service | MCP（内部可 HTTP） | `query_qc_violations`、`query_qc_alerts(case_id, session_id)`（可选）调 qc-service；`query_call_asr` / `query_call_sessions` 数据面由 **call-service** 提供（qc 不落 ASR、不实现这两个 tool 的数据面，避免归属歧义） | Agent 取证；节点不直连下游 HTTP |
| review-service | data-platform | HTTP | /reports/*、宽表 | 复盘与画像 |
| data-platform | 业务库 | LISTEN-NOTIFY | — | 消费业务库变更 |

所有跨服务调用透传 `X-Trace-Id` 请求头。

## 三、通信协议

| 场景 | 协议 | 说明 |
|---|---|---|
| 前端 ↔ 网关 ↔ 服务 | HTTP/JSON | 统一响应包 `{"code":"0","message","data","trace_id"}`，详见 step5/02 |
| 服务间同步调用（管理面/CRUD） | HTTP/JSON（内部网络） | 5s 超时 + 3 次指数退避（1s/2s/4s） |
| Agent / 工作流工具取证 | MCP | LangGraph 等节点经 MCP Client 调 tool；Server 可内部再 HTTP 调下游服务 |
| 实时语音流 / 实时字幕 | WebSocket | /calls/{id}/stream、/calls/{id}/captions（**不含**质检） |
| 异步跨服务通知 | 事件（Redis Pub-Sub / 等价） | 如通话结束、质检完成；channel 例 `qc.alert.raised`（广播订阅，告警仅事后下发主管，非通话页/按 agent 定向推送） |
| 数据同步触发 | PostgreSQL LISTEN-NOTIFY | 监听 call_records/dialog_turns 变更 |
| 离线任务调度 | APScheduler（进程内） | 定时 + 事件驱动 |

## 四、数据流

### 4.1 实时链路（低延迟，不经数据中台）

```mermaid
sequenceDiagram
    participant C as 呼叫中心
    participant CALL as call-service
    participant ASR as ASR
    participant LLM as LLMProvider
    participant RAG as rag-service
    participant TTS as TTS
    C->>CALL: 通话接通(含X-Trace-Id)
    loop 多轮对话(≤15轮)
        C->>ASR: 用户语音流
        ASR->>CALL: 文本+置信度
        CALL->>RAG: 检索合规话术
        CALL->>LLM: 生成应答(温度分层：生产默认 0.3、可配置 0.1~0.5；评测/回归固定 0.1 仅离线，见 step5/03 §6.2)
        LLM->>CALL: JSON应答+confidence+reasoning
        CALL->>TTS: 合成语音
        TTS->>C: 播报
    end
    CALL->>CALL: 摘要+回填工单
    CALL->>DP: 通话结束事件→数据中台
    Note over CALL,DP: qc-service 不在本环；订阅 ended 后离线质检
```

### 4.2 离线链路（经数据中台）

```mermaid
flowchart LR
    BL[(业务库 public)] -->|LISTEN-NOTIFY/API推送/批量| SYNC[data-sync]
    SYNC --> ODS[(ODS 原始层)]
    ODS --> CLN[data-clean] --> DWD[(DWD 明细层)]
    DWD --> WH[data-warehouse] --> DWS[(DWS 汇总层)]
    DWS --> FEAT[feature-engine] --> ADS[(ADS 应用层)]
    ADS --> RPT[/reports API/]
    ADS --> STR[策略迭代]
    STR -->|回流| BL
```

### 4.3 数据分层

| 层 | schema | 命名 | 用途 |
|---|---|---|---|
| ODS 原始层 | dw | ods_<表名> | 业务库 1:1 镜像 |
| DWD 明细层 | dw | dwd_<主题>_<表名> | 清洗去重后明细 |
| DWS 汇总层 | dw | dws_<维度>_<周期> | 按 case/agent/date 汇总 |
| ADS 应用层 | dw | ads_<场景> | 报表与模型直接消费 |

强制埋点表：`dws_call_result`、`dwd_sentiment_trend`、`dws_strategy_effectiveness` 必须产出，缺失视为未完成。**（一期边界（批次5 O4/D4-4）：强制产出仅适用于 call/qc 相关里程碑（M2 起）；M1 阶段 data-platform/rag/qc 小闭环不强制三表产出，dw 表落地边界见 step4/01 dw 区块头注。）**

## 五、RTC 流式管道

语音流输入 → VAD 检测 → ASR 转写 → 业务上下文组装 → LLM 推理 → 结构化解析 → Guardrails 校验 → TTS 合成语音输出。

- VAD 静音检测、语音打断、会话状态机独立工具类封装，严禁混入业务代码；
- 业务 API 全部 SSE 流式分片输出，固定四标识 start/content/end/error，字段不可缺失；
- 流式埋点：会话 ID、用户 ID、Prompt 入参、模型返回、耗时、模型版本、拦截状态。

## 六、全链路追踪（X-Trace-Id）

落地位置：`common/trace/middleware.py`。

1. 生成：每个外部请求进入 gateway 时，未携带 `X-Trace-Id` 则生成 `trace_<ulid>`；
2. 透传：FastAPI 中间件写入 contextvars；所有出站 HTTP、WebSocket、Redis、LLM、ASR/TTS 调用均从 contextvars 读取并透传；
3. 贯穿：ASR → 意图 → LLM → Guardrails → TTS，同一 trace_id 贯穿；质检为**通话结束后**离线环节，以同一 session_id/trace_id 事后串接 qc_records（对齐 DD-02 V1.5 / step5/04 §Guardrails 追溯）；
4. 落库：call_records.session_id、dialog_turns、qc_records、llm_call_logs、audit_logs 均记录 trace_id；
5. 日志：结构化 JSON 日志强制含 trace_id；
6. 查询：/audit-logs?trace_id= 支持单案件全流程串联查询。

## 七、通话生命周期状态机

催收会话流转由独立状态机驱动，禁止由大模型决定业务流程跳转；LLM 只负责生成会话文本。

```mermaid
stateDiagram-v2
    [*] --> 待调度: 案件入池
    待调度 --> 已分案: 分案系统完成分流
    已分案 --> 待AI外呼: AI队列
    已分案 --> 待人工: 人工队列
    待AI外呼 --> 振铃中: 发起外呼(08:00-21:00)
    待AI外呼 --> [*]: 非工作时段拦截(A1001)
    振铃中 --> 已接通: 客户接听
    振铃中 --> 无人接听: 振铃超时60s
    振铃中 --> 无效号码: 空号/停机
    无效号码 --> [*]: 标记失联,生成工单
    已接通 --> 对话中: ASR/TTS/LLM链路建立
    对话中 --> 转人工: 投诉关键词/置信度<0.7/15轮
    对话中 --> 结束通话: PTP/拒绝/3轮未平复
    无人接听 --> 待AI外呼: 重试<3次(间隔30min)
    无人接听 --> [*]: 重试>=3次,标记失联
    转人工 --> 人工接通: 坐席接听
    转人工 --> 待跟进工单: 无空闲坐席
    结束通话 --> 数据中台: 同步全量数据
    人工接通 --> 结束通话
    数据中台 --> 策略迭代: 离线分析
    策略迭代 --> [*]: 规则回流业务库
```

退出条件：

- 无效号码：标记失联并生成工单，不再重试；
- 非工作时段：外呼 API 入口拦截，返回 A1001；
- 转人工无空闲坐席：生成"待跟进工单"（高优先级），结束通话；
- 重试：未接通重试间隔 30 分钟，当日最多 3 次（可配置）。
