## Purpose

把敏感词数据源从 rag-service 内置 `.dict` 静态文件升级为 PostgreSQL `sensitive_words` 表（word/category/severity/status 五规范字段），引入 Redis 版本号 + Pub-Sub 发布订阅热刷新机制，保证 qc-service 60s 内拉取重建自动机，对齐 step4/01、step4/03 全局规范。对外只读接口契约保持不变（text/plain，响应内容从 50 词 seed 基线等价迁移）。

## ADDED Requirements

### Requirement: 敏感词数据源切换到 PG sensitive_words 表（status=active）
系统 SHALL 将 `/sensitive-words/current.dict` 接口的数据源由内置 `data/sensitive_words.dict` 切换为 PostgreSQL `sensitive_words` 表中 `status='active'` 的记录；**结果按 word 字母序拼接为每行一词的 text/plain 输出**，与 V1 文件格式完全兼容；启动时执行 seed 迁移（若表为空则将 V1 50 词字典一次性写入表 status=active category=threat severity=medium，基线一致不漂移）。

#### Scenario: 接口 text/plain 返回 50 词基线
- **WHEN** V2 首次上线后，sensitive_words 表为空，管理员调用 GET /sensitive-words/current.dict
- **THEN** 系统在 startup 迁移 V1 50 词到表，接口返回 50 行词，内容与 V1 data/sensitive_words.dict 字节级一致，qc-service 原有解析无需改动

#### Scenario: 管理员新增敏感词后生效
- **WHEN** 通过外部管理后台新增一条敏感词 word="测试违规项" status=active 到 DB，并 PUBLISH kb.sensitive_updated
- **THEN** rag-service 下次 GET /sensitive-words/current.dict 响应包含新的 "测试违规项"，仍按字母序输出，共 51 行

#### Scenario: disabled 词不过滤输出
- **WHEN** DB 中 1 条 status=disabled 的词"旧词停用"，其余 50 active
- **THEN** 接口返回 50 行词（不含 disabled），与 V1 50 词字节级一致

### Requirement: 启动自检幂等（对齐 design Decision 8 Step B+，deploy 顺序容错）
系统 SHALL 在 main.py startup 事件中（milvus + es 初始化完成后，接收请求前）额外执行一次"敏感词表完整性自检"，即使 Alembic 迁移尚未执行完成或 deploy 顺序颠倒也能补齐基线：① DB 查询 `SELECT COUNT(*) FROM sensitive_words WHERE status='active' AND is_delete='0'` 取 count；② 若 count < 50 且 `data/sensitive_words.dict` 可读，则对 .dict 逐行执行 `INSERT INTO sensitive_words(id, word, category, severity, status, description, create_time, update_time, is_delete) VALUES (uuid_generate_v4(), :w, 'threat', 'medium', 'active', 'startup_seed_v2_fallback', NOW(), NOW(), '0') ON CONFLICT (word) DO NOTHING`（与 migration 0004 seed 完全相同的 SQL 模板，保障幂等、不重复、与 V1 50 词 baseline 语义一致）；③ 初始化 Redis key `rag:sensitive_words:version`：若不存在则 SET 为 1（无 TTL，作为后续 INCR 单调递增的起点）；④ 最终若 count < 50，输出 **WARNING 级日志**包含 actual_count、expected_count=50、"请检查敏感词表迁移状态" 提示信息，但**不抛异常、不阻塞服务启动**（运行期 get_current_dict 仍走 file fallback，保障 qc-service 不中断）。该自检为 startup 一次性执行，后续 refresh 不重复触发。

#### Scenario: DB为空但.dict存在，启动自检补上50词
- **WHEN** 极端 deploy 顺序下（先启动容器，再跑 Alembic 迁移），新容器启动时 sensitive_words 表仍为空，而 data/sensitive_words.dict 存在
- **THEN** startup 自检检测到 count < 50，逐行 INSERT ON CONFLICT，最终 DB active count=50，version key 被初始化为 1，服务正常启动不报错，stdout 可见 INFO 日志"startup_sensitive_seed_success count=50"

#### Scenario: DB连接失败仍不阻塞启动
- **WHEN** startup 自检期间 PG 连接池短暂不可用（容器 DNS 未就绪 1s）
- **THEN** 自检捕获异常 → logger.warning "sensitive_startup_self_check_failed" + 异常堆栈摘要 → 服务继续启动；运行期 get_current_dict 仍由 DB 失败→.dict file fallback 双读链覆盖，不影响敏感词响应

#### Scenario: DB已达到50词则跳过 INSERT，版本号只初始化
- **WHEN** 正常启动（Alembic 0004 seed 已跑），DB active count=50，Redis 尚未设置 version
- **THEN** 自检跳过 INSERT，仅执行 `SET rag:sensitive_words:version 1`（若存在则 NX），并在 info log 记录"sensitive_startup_seed_skip count=50 version=1"

### Requirement: 版本号 `rag:sensitive_words:version` 自增
系统 SHALL 在敏感词变更（由外部管理后台写入 DB 后通知 rag-service PUBLISH，或 rag-service 内部 seed 初始化时），对 Redis key `rag:sensitive_words:version` 执行 INCR 自增，版本号为单调递增 INT，永不回绕。该 key 对齐 step4/03 规范清单。

#### Scenario: 启动初始化写入版本 1
- **WHEN** rag-service 启动时发现 sensitive_words 表为空，seed 写入 50 词
- **THEN** Redis 执行 SET rag:sensitive_words:version 1（首次创建），qc-service 通过 GET version 与自身缓存比对识别变更

#### Scenario: 外部变更后版本号递增
- **WHEN** 外部管理后台向 DB 写入 1 条新 active 词并调用 rag-service 内部 refresh API（由管理后台私有）
- **THEN** Redis INCR version 由 1→2，并返回新版本号给调用方

### Requirement: Pub-Sub `kb.sensitive_updated` 事件发布
系统 SHALL 在敏感词版本号变更（含 seed 初始化、手动 refresh 两种触发）后，向 Redis Pub-Sub channel `kb.sensitive_updated` PUBLISH 消息，**payload 严格为 JSON `{"version":int,"trace_id":str,"ts":int}`**（对齐 step4/03 §4.1 强制要求：Payload 统一 JSON，必须含 `ts` Unix epoch 秒和 `trace_id` X-Trace-Id 透传）。qc-service/complaint-service/review-service 订阅该 channel 并在 60s 内拉取重建 AC 自动机（消费侧实现）。发布失败不影响 rag-service 主流程（warning 日志 + 降级为 version GET 轮询兜底）。

#### Scenario: seed 初始化后发布 JSON 事件
- **WHEN** rag-service 首次启动 seed 写入 50 词并 version=1
- **THEN** 订阅者收到 channel=kb.sensitive_updated 的消息 payload=`{"version":1,"trace_id":"trace_xxx","ts":1724054400}` 格式完整，可解析 version 字段，并于 60s 内触发自身热刷新

#### Scenario: 手动 refresh 后 JSON 事件递增
- **WHEN** ADMIN 通过内部 refresh 接口新增 1 条 active 词，当前 trace_id=trace_new01
- **THEN** Redis PUBLISH 发送 `{"version":2,"trace_id":"trace_new01","ts":<当前epoch秒>}`，qc-service 端 JSON 解析后取 version=2 与自身缓存比对

#### Scenario: Redis Pub-Sub 故障降级
- **WHEN** PUBLISH 命令因 Redis 故障失败
- **THEN** rag-service 记录 warning 日志（warning 级别），不阻断对外服务接口，不返回错误给管理后台调用方（后台依赖 `rag:sensitive_words:version` GET 轮询兜底：qc-service 对比版本号差异）
