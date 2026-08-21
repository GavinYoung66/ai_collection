# API 接口与 Streaming 流式协议规范

> 文档类型：技术规范（规范层）
> 版本：v1.0
> 状态：定稿
> 更新日期：2026-08-19
> 约束声明：本文件为 API 接口与流式协议单一事实源；遵循 step1/01 全局统一口径与 openspec/specs/02；统一响应包 {"code":"0","message","data","trace_id"}，code 为字符串、"0" 表示成功；错误码引用 step6/01 错误码全集（84 条），禁止私自编号；demand/step3/API_CONTRACT_EXPANSION.md 仅作参考。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅旧格式 `{code:200,msg}` | — |
| v1.0 | 2026-08-19 | 修正为 {"code":"0"} 字符串格式；补 SSE 四标识、WS 协议、分页、X-Trace-Id、校验规则、AI 埋点、API 路径约定 | 规范定稿 |

## 一、统一响应包

### 1.1 格式定义

所有 HTTP 接口（除文件下载与 WebSocket 流式）必须返回如下结构：

```json
{
  "code": "0",
  "message": "success",
  "data": {},
  "trace_id": "trace_01H8XK7P9M2Q3R4S5T6U7V8W9X"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| code | String | "0" 表示成功；非 "0" 为错误码（见第二节） |
| message | String | 面向调用方的简短描述；成功为 `success`，失败为可读中文原因 |
| data | Object/Array/null | 业务数据；失败时为 `null` |
| trace_id | String | 全链路追踪 ID，格式 `trace_<ulid>`，由 gateway 生成并透传 |

> 禁止自定义返回格式；禁止用 HTTP 状态码区分业务结果（成功与业务错误统一 200，系统异常 5xx，均封装为统一包）；文件下载返回二进制流（Content-Type 正确），WebSocket 遵循第五节。

### 1.2 成功响应示例

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "session_id": "sess_abc123",
    "status": "connected"
  },
  "trace_id": "trace_01H8XK7P9M2Q3R4S5T6U7V8W9X"
}
```

### 1.3 失败响应示例

非工作时间禁止外呼（A1001）：

```json
{
  "code": "A1001",
  "message": "非工作时间禁止外呼",
  "data": null,
  "trace_id": "trace_01H8XK7P9M2Q3R4S5T6U7V8W9X"
}
```

未认证（B0002）：

```json
{
  "code": "B0002",
  "message": "未认证，请重新登录",
  "data": null,
  "trace_id": "trace_01H8XK7P9M2Q3R4S5T6U7V8W9X"
}
```

### 1.4 分页响应

分页查询的 `data` 统一结构：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

- 分页参数：`page`（从 1 开始）、`page_size`（默认 20，最大 100）；
- 禁止无条件全表查询，所有列表接口必须分页/limit。

## 二、错误码引用

### 2.1 错误码全集基线

错误码格式：`<域><4位序号>`。域分配：

| 域 | 服务 | 码段 |
|---|---|---|
| A1 | call-service（外呼） | A1001~A1013 |
| A2 | qc-service（质检） | A2001~A2006 |
| A3 | rag-service（知识库） | A3001~A3006 |
| A4 | case-service（分案） | A4001~A4015 |
| A5 | complaint-service（投诉） | A5001~A5013 |
| A6 | review-service（复盘） | A6001~A6015 |
| A7 | data-platform（数据中台） | A7001~A7006 |
| B | 系统级（通用） | B0001~B0010 |

> 共 84 条（含 DD-04/05/06 扩展码），详见 step6/01 错误码全集。子系统新增错误码须经评审收录，禁止私自编号。

### 2.2 HTTP 状态码对应

| 场景 | HTTP 状态码 | 说明 |
|---|---|---|
| 成功 | 200 | code="0" |
| 业务错误 | 200 | code=A1xxx~A7xxx，统一封装为响应包 |
| 系统异常 | 500/502/503/504 | code=B0007/B0008/B0009 等 |
| 认证/权限 | 401/403/422/429 | code=B0002/B0003/B0001/B0006 |

> 所有异常禁止原生堆栈外抛，必须全局捕获、封装业务提示、隐藏技术细节。

## 三、API 路径约定

### 3.1 路径规范

- 复数名词小写，kebab-case：`/qc/records`、`/kb/documents`、`/call/campaigns`；
- 版本前缀：`/api/v1/`；
- 服务前缀由 gateway 路由，内部路径不含服务名；
- 操作动词用 HTTP Method 表达：GET 查询、POST 创建、PUT 更新、DELETE 软删。

### 3.2 RESTful 资源路径模板

| 操作 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 列表 | GET | `/api/v1/<resource>` | 分页查询，返回 PagedData |
| 详情 | GET | `/api/v1/<resource>/{id}` | 单条详情 |
| 创建 | POST | `/api/v1/<resource>` | 创建，返回创建后对象 |
| 更新 | PUT | `/api/v1/<resource>/{id}` | 全量/部分更新 |
| 删除 | DELETE | `/api/v1/<resource>/{id}` | 软删（is_delete='1'） |
| 自定义动作 | POST | `/api/v1/<resource>/{id}/<action>` | 如 `/cases/{id}/assign` |

### 3.3 服务端口与接口归属

| 服务 | 端口 | 接口前缀 |
|---|---|---|
| data-platform | 8001 | /api/v1/data/* |
| rag-service | 8002 | /api/v1/kb/*, /api/v1/sensitive-words/* |
| qc-service | 8003 | /api/v1/qc/* |
| call-service | 8004 | /api/v1/call/* |
| case-service | 8005 | /api/v1/cases/*, /api/v1/agents/* |
| complaint-service | 8006 | /api/v1/complaints/* |
| review-service | 8007 | /api/v1/reviews/*, /api/v1/training/*, /api/v1/reports/* |

## 四、SSE 流式协议

### 4.1 适用场景

所有 AI 对话、实时外呼、流式问答、动态文本输出必须 SSE 分片流式输出，禁止一次性大包返回。

### 4.2 流式四标识

流式输出固定四标识，字段不可缺失：

| 标识 | 事件类型 | 说明 | data 示例 |
|---|---|---|---|
| start | 分片开始 | 通知客户端流开始 | `{"session_id":"sess_xxx","trace_id":"trace_xxx"}` |
| content | 内容分片 | 逐片段输出文本/JSON | `{"text":"您好"}` 或 `{"chunk":"..."}` |
| end | 结束 | 通知客户端流结束 | `{"session_id":"sess_xxx","summary":"..."}` |
| error | 异常终止 | 通知客户端流异常中断 | `{"code":"A1010","message":"置信度不足转人工"}` |

### 4.3 SSE 响应格式

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

event: start
data: {"session_id":"sess_abc","trace_id":"trace_xxx"}

event: content
data: {"text":"您好"}

event: content
data: {"text":"请问您方便沟通一下您的账单吗"}

event: end
data: {"session_id":"sess_abc","result":"ptp","turns":5}
```

### 4.4 错误中断

```
event: error
data: {"code":"A1007","message":"对话轮次超限熔断","trace_id":"trace_xxx"}
```

错误中断后不再发送任何事件，客户端须关闭连接。

## 五、WebSocket 协议

### 5.1 适用场景

| 场景 | 路径 | 说明 |
|---|---|---|
| 实时语音流 | /calls/{session_id}/stream | 双向音频流 + ASR/TTS |
| 实时字幕 | /calls/{session_id}/captions | ASR 转写实时推送 |
| 实时质检 | /qc/check/stream | 旁路质检结果实时推送 |
| 培训反馈 | /training/{session_id}/feedback | 培训代理 WS 队列 |

### 5.2 连接规范

- WebSocket 连接 URL 须携带 `?trace_id=trace_<ulid>` 透传追踪 ID；
- 连接建立后先发 `{"type":"connected","trace_id":"..."}` 确认帧；
- 断线自动重连（最多 3 次，间隔 1s/2s/4s），重连后恢复 trace_id 上下文；
- 心跳：30s 发送 `{"type":"ping"}`，收到 `{"type":"pong"}` 为存活；超 60s 无响应判定断线。

### 5.3 消息格式

WebSocket 消息统一 JSON：

```json
{
  "type": "asr_result",
  "session_id": "sess_abc",
  "trace_id": "trace_xxx",
  "data": {
    "text": "我下周可以还",
    "confidence": 0.92
  },
  "ts": 1724054400
}
```

| type | 方向 | 说明 |
|---|---|---|
| connected | S→C | 连接确认 |
| asr_result | S→C | ASR 转写结果 |
| llm_response | S→C | LLM 结构化应答 |
| tts_chunk | S→C | TTS 音频分片 |
| qc_alert | S→C | 实时质检告警 |
| vad_silence | S→C | 静默检测 |
| ping/pong | 双向 | 心跳 |
| error | S→C | 异常通知 |

## 六、X-Trace-Id 全链路追踪

### 6.1 生成与透传

| 环节 | 规则 |
|---|---|
| 生成 | 每个外部请求进入 gateway 时，未携带 X-Trace-Id 则生成 `trace_<ulid>` |
| HTTP 透传 | 请求头 `X-Trace-Id`；所有出站 HTTP 调用从 contextvars 读取并设置请求头 |
| WebSocket 透传 | 连接 URL query 参数 `trace_id` |
| Redis 透传 | Pub-Sub Payload 含 trace_id 字段 |
| LLM 透传 | LLM 调用入参带 trace_id，写入 llm_call_logs |
| 日志透传 | 结构化 JSON 日志强制含 trace_id |

### 6.2 落库

以下表必须记录 trace_id：
- `call_records.trace_id`
- `dialog_turns`（经 session_id 关联）
- `qc_records`（经 session_id 关联）
- `llm_call_logs.trace_id`
- `audit_logs.trace_id`

### 6.3 查询

`GET /api/v1/audit-logs?trace_id=trace_xxx` 支持单案件全流程串联查询。

## 七、请求校验规则

所有接口必须做以下校验，缺一不可：

| 校验项 | 说明 | 落地方式 |
|---|---|---|
| 非空校验 | 必填字段不得为空/null | Pydantic Field(required=True) |
| 长度校验 | 字符串/数组长度限制 | Pydantic max_length/min_length |
| 非法字符校验 | 禁止 SQL 注入/XSS 字符 | Pydantic validator + 正则白名单 |
| 参数类型校验 | 类型严格匹配 | Pydantic 类型注解 |
| 业务规则校验 | 如外呼时段、重试次数、并发上限 | Service 层校验，返回 A 系列错误码 |

校验失败统一返回 B0001（参数校验失败），HTTP 422。

## 八、AI 接口全量埋点

所有 AI 相关接口必须记录以下埋点信息：

| 埋点字段 | 说明 | 落库表 |
|---|---|---|
| session_id | 会话 ID | call_records / dialog_turns |
| user_id / case_id | 用户/案件标识 | call_records |
| prompt 入参 | LLM 输入（脱敏后） | llm_call_logs.prompt |
| 模型返回 | LLM 输出（脱敏后） | llm_call_logs.response |
| 耗时 | 调用延迟（ms） | llm_call_logs.latency_ms |
| 模型版本 | provider + model | llm_call_logs.provider/model |
| 拦截状态 | 是否被 Guardrails 拦截 | llm_call_logs.status |
| trace_id | 全链路追踪 ID | llm_call_logs.trace_id |
| token_usage | token 消耗 | llm_call_logs.token_usage |
| confidence | 置信度 | llm_call_logs.confidence |
| temperature | 推理温度 | llm_call_logs.temperature |

> 缺失任意埋点项视为接口不通过。所有 prompt/response 落库前须经 mask.py 脱敏（手机号/身份证/金额打码）。
