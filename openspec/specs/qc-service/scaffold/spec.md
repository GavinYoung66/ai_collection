## Purpose

定义 qc-service M1 包结构、`QC_*` 配置、依赖注入与 A2 HTTP 信封，使离线质检服务能在端口 8003 启动，且不含实时 ingest。

## Requirements

### Requirement: 包结构对齐 DD-02 §3

系统 SHALL 在 `services/qc-service` 提供目录 `app/routers/`、`app/services/`、`app/repositories/`、`app/events/`、`app/integrations/`，文件名与 DD-02 §3 一致。现有 `app/api/health.py` MAY 保留为服务根 `GET /health` 实现。服务 MUST NOT 再平行一套业务路由 `app/api/`。服务 MUST NOT 包含 WebSocket 模块。

#### Scenario: 目录树存在

- **WHEN** 实现者检查 `services/qc-service/app/`
- **THEN** 上述五个目录存在，业务 HTTP 在 `app/routers/`，不存在第二套业务 `app/api/`

#### Scenario: 无 WebSocket 模块

- **WHEN** 服务启动
- **THEN** 未注册 `WS /qc/check/stream` 路由或 websocket 包

### Requirement: QC_* 配置含 M1 冻结开关

系统 SHALL 加载 DD-02 §12 全部 `QC_*` 键。`QC_RAG_SENSITIVE_BASE` 与 `QC_DP_BASE` MUST NOT 含 `/api/v1`。M1 下 `QC_R14_RAG_ENABLED` MUST 为 `false`。`QC_TEXT_READY_RETRY_SEC` MUST 默认 `30`。`QC_TEXT_READY_RETRY_MAX` MUST 默认 `20`。`QC_DEFAULT_ACTIVE_RULES_VERSION` MUST 默认 `v1.0`。系统 MUST NOT 定义 `QC_RESPONSE_TIMEOUT=3` 或 `QC_EVENT_ESCALATE_ENABLED`。

#### Scenario: R14 RAG 开关为 false

- **WHEN** qc-service 以默认环境启动
- **THEN** `QC_R14_RAG_ENABLED` 为 false，且未配置 `/kb/search` 客户端

#### Scenario: host 不含 api 前缀

- **WHEN** 加载 settings
- **THEN** `QC_RAG_SENSITIVE_BASE` 与 `QC_DP_BASE` 为不含 `/api/v1` 的 host

### Requirement: 依赖注入复用 common

系统 SHALL 通过 FastAPI deps 注入 DB session、Redis DB 3、settings、当前 active `qc_rules` 快照。服务 MUST 复用 `common/` 的 LLM 传输、脱敏与错误信封。服务 MUST NOT 在 qc-service 内再实现私有 LLM 客户端、脱敏器或错误封装。

#### Scenario: Redis 使用 DB 3

- **WHEN** 请求或任务访问 Redis
- **THEN** 使用 Redis database 3

### Requirement: 统一信封与 A2 HTTP 映射

每条业务 HTTP 响应 SHALL 使用信封 `{"code","message","data","trace_id"}`。业务成功 SHALL 为 `code==="0"`。HTTP 200 且 `code=A2003` SHALL 视为业务失败。A2 映射 MUST 为：

| code | HTTP |
| :--- | :--- |
| A2001 | 200 |
| A2002 | 422 |
| A2003 | 200 |
| A2004 | 400 |
| A2005 | 404 |
| A2006 | 409 |
| A2007 | 404 |
| A2008 | 400 |
| A2009 | 200 |
| A2010 | 200 |
| A2011 | 200 |
| B0002 | 401 |
| B0003 | 403 |

A2002 MUST NOT 叠 B0001。服务 MUST NOT 把全部业务 A 码映射为 HTTP 200。

#### Scenario: 非法入参仅为 422 A2002

- **WHEN** 客户端发送非法 turns、speaker 或 `call_ended≠true`
- **THEN** 响应为 HTTP 422、`code=A2002`，且无 B0001

#### Scenario: HTTP 200 + A2003 仍为业务失败

- **WHEN** 客户端收到 HTTP 200 且 `code=A2003`
- **THEN** `code!=="0"`，MUST 视为业务失败，而不是质检开工成功

### Requirement: 仅离线表面（无 ingest、无 WS、无 escalate）

服务 SHALL NOT 暴露 `POST /qc/check` ingest。服务 SHALL NOT 暴露 `WS /qc/check/stream`。服务 SHALL NOT 发布 `qc.escalate.human`。服务 MUST NOT HTTP 调用 call-service，MUST NOT 实现 call-service 或 data-platform ETL/宽表。

#### Scenario: ingest 路径不存在

- **WHEN** 客户端调用 `POST /api/v1/qc/check`
- **THEN** 该路由未注册（404/405），不是 ingest 处理器

#### Scenario: 不使用 escalate 频道

- **WHEN** 订阅方在任意质检过程中监听 `qc.escalate.human`
- **THEN** 无消息发布

#### Scenario: 无 call-service 客户端

- **WHEN** 构建 M1 qc-service
- **THEN** 无指向 call-service 的 HTTP 客户端，且无 ingest/WS 质检路由
