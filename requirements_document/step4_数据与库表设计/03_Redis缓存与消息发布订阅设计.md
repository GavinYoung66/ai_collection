# Redis 缓存与消息发布订阅设计

> 文档类型：数据设计（规范层）
> 版本：v1.10
> 状态：定稿
> 更新日期：2026-09-09
> 约束声明：本文件为 Redis 缓存与 Pub-Sub 单一事实源；遵循 step1/01 全局口径与 openspec/specs/03、06；Redis 7（redis:7-alpine，端口 6379）；7 服务按 DB 0-7 隔离；demand/ 旧文档仅作参考。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅 DB 分配与基础键 | — |
| v1.0 | 2026-08-19 | 整合 7 个 demand 设计文档全部 Redis 键模式、Pub-Sub 频道、TTL 策略、熔断标识、分布式锁、连接配置、安全合规 | 规范定稿 |
| v1.1 | 2026-09-09 | P0-2 修复 §3.1/§3.2 标题 DB 编号与 §1.2 分配表矛盾（rag-service DB1→DB2、data-platform DB2→DB1，以 §1.2 分配表为准） | Ellis Guo |
| v1.2 | 2026-09-09 | P1-13 补 call+qc LLM 熔断标识：§3.3 加 qc:llm_degraded、§3.4 加 call:llm_degraded、§6.1 加两行（决策A执行） | Ellis Guo |
| v1.3 | 2026-09-09 | P1-16 §5.2 词库热刷新敏感词端点统一 /sensitive-words/current.dict → /api/v1/sensitive-words/current.dict（决策A执行） | Ellis Guo |
| v1.4 | 2026-09-09 | P2-22 §4.2 频道矩阵补登记 system.alert.raised / system.alert.resolved 两个全局告警频道（对齐 step6/02 §3.2），§十验收"21 个频道"→"23 个频道" | Ellis Guo |
| v1.7 | 2026-09-09 | 对齐 DD-02 V1.5：qc:session→qc:job；删 call:qc_stream 与 qc.escalate.human；call.session.ended 增订 qc；qc.alert 仅事后主管 | Ellis Guo |
| v1.8 | 2026-09-09 | §3.3 qc:llm_degraded TTL 注释「永不过期」改 SET/DEL 自管理（熔断 OPEN 时 SET，60s 后半开探测成功 DEL，无固定 EXPIRE，对齐 DD-02 §6.2 熔断状态机）；§9.2 频道归属补 system.* 全局告警频道豁免（允许各服务异常处理器含 qc 发布） | 规范复审 |
| v1.9 | 2026-09-09 | 对齐 DD-02 V1.9：`qc.complaint.suspected` 追加 `qc_record_id`（投诉 SETNX 幂等）；发布条件改为 rule_id∈{R11,R12,R13}（agent+high），`redline_tags[]` 仅审计；`qc.review.done.sample_type`∈{positive,negative,corrected}；`call_id` 无则 JSON null | Ellis Guo |
| v1.10 | 2026-09-09 | `qc:job:{session_id}` JSON 增订 `skipped_rules[]` + `qc_record_id`（对齐 DD-02 V1.11，不加 qc_records 列） | Ellis Guo |

## 一、Redis 实例与 DB 分配

### 1.1 实例基线

| 属性 | 值 |
|---|---|
| 版本 | Redis 7（redis:7-alpine） |
| 端口 | 6379 |
| 持久化 | AOF（appendonly yes，每秒 fsync）+ RDB 快照（每小时 bgsave） |
| 最大内存 | 2GB（maxmemory 2gb，maxmemory-policy allkeys-lru） |
| 卷 | `redis_data`（docker volume，命名规范 `<service>_data`） |
| 部署 | 单实例（MVP 不做集群），docker-compose 统一编排 |

### 1.2 DB 分配（服务隔离）

| DB | 服务 | 前缀 | 说明 |
|---|---|---|---|
| 0 | 保留 | — | Redis 内部使用，业务不写入 |
| 1 | data-platform | `dp:` | ETL 状态、特征缓存、报表缓存、策略版本 |
| 2 | rag-service | `rag:` | BM25 索引、敏感词版本、文档缓存 |
| 3 | qc-service | `qc:` | 词库版本、规则缓存、告警计数 |
| 4 | call-service | `call:` | 会话状态机、批次进度、限流令牌桶、转人工锁 |
| 5 | case-service | `cs:` | 打分缓存、坐席负载、派单锁、技能标签 |
| 6 | complaint-service | `csmp:` | 取证包、判责、方案、统计、熔断标识 |
| 7 | review-service | `rv:` | 分类缓存、画像缓存、报告缓存、培训状态、熔断标识 |

> 各服务通过 `REDIS_DB` 环境变量注入 DB 编号，禁止跨 DB 访问；Pub-Sub 频道为全局通道（不按 DB 隔离），但发布/订阅方须遵守频道归属约定（见第六节）。

## 二、键命名规范

### 2.1 命名格式

```
<service_prefix>:<entity>:<identifier>[:<sub_entity>]
```

- 前缀严格按 DB 分配表（rag/dp/qc/call/cs/csmp/rv），禁止跨服务前缀；
- 分隔符统一冒号 `:`，禁止下划线、点号；
- 标识符用业务 ID（session_id、case_id、agent_id 等），禁止用 UUID 全拼（太长），截取前 8 位即可；
- 日期格式 `YYYYMMDD`，时间戳用 Unix epoch 秒。

### 2.2 数据结构选型

| 场景 | 结构 | 示例 |
|---|---|---|
| 状态机/JSON 对象 | STRING（JSON 序列化） | `call:session:{session_id}` |
| 计数器 | STRING（INCR/INCRBY） | `call:user_daily:{user_id}:{date}` |
| 令牌桶 | STRING（DECR） | `call:throttle:global` |
| 分布式锁 | STRING（SET NX EX） | `cs:pick_lock:{work_item_id}` |
| 列表队列 | LIST（LPUSH/RPOP） | `rv:training:feedback:{sid}` |
| 热点集合 | SET / ZSET | （MVP 暂不使用，预留） |
| BM25 索引 | STRING（pickle 序列化） | `rag:bm25_index:<category>` |
| Pub-Sub | 不落键，直接 PUBLISH | channel 字符串 |

### 2.3 TTL 原则

| TTL 策略 | 适用场景 | 说明 |
|---|---|---|
| 永不过期 | 策略版本、技能标签 | 须显式 DEL 才能清除，用于全局唯一配置 |
| 10s–60s | 令牌桶、负载探测、分布式锁 | 高频变更，短 TTL 防止脏读 |
| 1h–2h | 特征缓存、画像缓存、文档缓存 | 中频变更，容忍短时不一致 |
| 6h | 统计缓存、周报缓存 | 低频变更，批量任务产出 |
| 24h | 会话状态、ETL 运行、分类缓存 | 覆盖一个自然日，跨日自动失效 |
| 7d | 幂等锁、冲突标记 | 长周期防重，人工或超时清除 |

## 三、各服务 Key 清单

### 3.1 rag-service（DB 2）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `rag:bm25_index:<category>` | STRING（pickle） | 永不过期 | BM25 索引序列化，按 category 分组；kb_chunks 变更时 dump |
| `rag:sensitive_words:version` | STRING | 永不过期 | 当前敏感词库版本号，自增 |
| `rag:documents` | STRING（JSON） | 1h | 热点文档内容缓存 |

### 3.2 data-platform（DB 1）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `dp:etl:run:{run_id}` | STRING（JSON） | 24h | 单次 ETL 运行状态 |
| `dp:features:user:{user_id}:{as_of}` | STRING（JSON） | 1h | 单用户特征缓存，避免重复查 ADS 表 |
| `dp:report:daily:{date}` | STRING（JSON） | 2h | 日报 JSON，对接 DailyReportResponse API |
| `dp:report:weekly:{week_start}` | STRING（JSON） | 6h | 周报 JSON |
| `dp:strategy:active` | STRING | 永不过期 | 当前生效策略版本号 |
| `dp:scheduler:health` | STRING（JSON） | 10s | 调度器健康状态，外部告警探测用 |

### 3.3 qc-service（DB 3）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `qc:words:version` | STRING | 永不过期 | 敏感词当前版本号（与 rag:sensitive_words:version 对齐） |
| `qc:rules:active` | STRING（JSON） | 6h | qc_rules active 版本 ID + payload，DB 为权威源 |
| `qc:alerts:new_count` | STRING（INT） | 永不过期 | SUPERVISOR 前端未读告警计数 |
| `qc:llm_degraded` | STRING（"1"） | SET/DEL 自管理（熔断 OPEN 时 SET，60s 后半开探测成功 DEL；无固定 EXPIRE，非常驻业务键，对齐 DD-02 §6.2） | LLM 熔断标识，1=熔断中（P1-13 补，DD-02 已申请） |
| `qc:job:{session_id}` | STRING（JSON） | 24h | 离线质检任务态。JSON：`status`（WAIT_TEXT/RUNNING/DONE）+ 终态时 `qc_record_id` + `skipped_rules[]`（对齐 DD-02 V1.11；耐久副本在 audit_logs） |
| `qc:replay:run:{session_id}` | STRING | 7d | 回放/重跑幂等 |

### 3.4 call-service（DB 4）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `call:session:{session_id}` | STRING（JSON） | 24h | CallSession 状态机，含当前轮次/意图/转人工状态 |
| `call:campaign:{id}:progress` | STRING（INT） | 24h | 批次进度计数器（已呼/总数） |
| `call:user_daily:{user_id}:{date}` | STRING（INT） | 23:59 expire | 当日拨打次数（INCR），防超 3 次 |
| `call:throttle:global` | STRING（INT） | 永不过期 | 并发 50 路令牌桶，DECR 出呼 INCR 结束 |
| `call:simulate:run:{idempotency}` | STRING | 7d | 模拟器幂等锁，返回已有 call_id |
| `call:rag_hits:{session_id}:{turn}` | STRING（JSON） | 24h | RAG 命中缓存，排错用 |
| `call:llm_degraded` | STRING（"1"） | 永不过期 | LLM 熔断标识，1=熔断中（P1-13 补，call-service 是 LLM 最大使用方） |
| `call:transfer:{session_id}` | STRING | 10min | 转人工状态 IN_PROGRESS/DONE，防重复触发 |

### 3.5 case-service（DB 5）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `cs:score:{case_id}:{as_of}` | STRING（JSON） | 24h | 三维打分缓存，避免重复查 DP |
| `cs:capacity:ai_remaining` | STRING（INT） | 10s | AI 令牌桶剩余（capacity_service 每 10s 探 call-service） |
| `cs:agent:load:{agent_id}` | STRING（JSON） | 60s | 坐席负载缓存 |
| `cs:pick_lock:{work_item_id}` | STRING | 15s | 派单 pick 幂等分布式锁（SET NX EX） |
| `cs:reassign:review:{case_id}` | STRING | 7d | 冲突待人工确认标记 |
| `cs:skill_tags:active` | STRING（JSON） | 永不过期 | 技能标签缓存（6 条种子 + 可扩展） |

### 3.6 complaint-service（DB 6）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `csmp:ticket:{id}:pkg` | STRING（JSON） | 24h | 取证包缓存 |
| `csmp:verdict:{id}:latest` | STRING（JSON） | 24h | 最新判责缓存 |
| `csmp:rslv:{id}:latest` | STRING（JSON） | 24h | 处置方案缓存 |
| `csmp:hotspots:{start}:{end}` | STRING（JSON） | 6h | 统计热点缓存 |
| `csmp:gather:lock:{id}` | STRING | 60s | 取证幂等锁（SET NX EX） |
| `csmp:llm_degraded` | STRING（"1"） | 永不过期 | LLM 熔断标识，1=熔断中 |

### 3.7 review-service（DB 7）

| Key | 结构 | TTL | 用途 |
|---|---|---|---|
| `rv:review:{call_id}:latest` | STRING（JSON） | 24h | 分类缓存 |
| `rv:profile:{agent_id}:{as_of}` | STRING（JSON） | 2h | 坐席画像缓存 |
| `rv:report:{period}:{start}` | STRING（JSON） | 6h | 报告缓存（daily/weekly） |
| `rv:training:session:{id}` | STRING（JSON） | 24h | 培训会话状态 |
| `rv:training:feedback:{sid}` | LIST | 24h | WS 代理队列（LPUSH/RPOP） |
| `rv:llm_degraded` | STRING（"1"） | 永不过期 | LLM 熔断标识，1=熔断中 |

## 四、Pub-Sub 频道总览

### 4.1 频道命名规范

```
<service>.<action>[.<sub_action>]
```

- 前缀与 DB 分配表一致；
- 全局频道，不按 DB 隔离；
- Payload 统一 JSON，必须含 `ts`（Unix epoch 秒）和 `trace_id`（X-Trace-Id 透传）。

### 4.2 发布→订阅矩阵

| 频道 | 发布者 | Payload | 订阅者 | 目的 |
|---|---|---|---|---|
| `kb.sensitive_updated` | rag-service | `{version, trace_id, ts}` | qc-service / complaint-service / review-service | 词库热刷新，60s 内生效 |
| `kb.version.switched` | rag-service | `{doc_id, old_version, new_version, trace_id, ts}` | rag-service 内部 | 检索版本切换 |
| `etl.task.done` | data-platform | `{run_id, stage, backfill_date?, trace_id, ts}` | qc-service / case-service / review-service | 触发补检/重打分/批量复盘 |
| `strategy.updated` | data-platform | `{version, strategy_type, trace_id, ts}` | call-service / case-service / qc-service | 策略版本热更新 |
| `call.session.ended` | call-service | `{session_id, case_id, result, intent_tags[], trace_id, ts}` | data-platform / case-service / review-service / **qc-service** | ODS 追加 / 二次分案 / 单通复盘 / **离线质检开工（文本未齐则等 ETL）** |
| `system.alert.raised` | 各服务异常处理器 | `{alert_id, level, event, service, description, error_code, trace_id, ts}` | 前端 / supervisor / 运维 | 全局告警推送（对齐 step6/02 §3.2） |
| `system.alert.resolved` | 各服务恢复探测 | `{alert_id, service, ts}` | 前端 / supervisor / 运维 | 全局告警恢复（对齐 step6/02 §3.2） |
| `qc.alert.raised` | qc-service | `{alert_id, session_id, call_id, level, evidence, trace_id, ts}` | 事后主管 / 运营前端 | 高危告警（**不**弹通话页）。`call_id` 无则 JSON null |
| `qc.check.completed` | qc-service | `{qc_record_id, session_id, call_id, case_id, score, risk, grade, trace_id, ts}` | data-platform / review-service | 宽表 / 复盘；**不**自动建投诉工单。`call_id`=`call_records.id` UUID，无则 JSON null，禁止用 session_id 冒充 |
| `qc.complaint.suspected` | qc-service | `{qc_record_id, session_id, case_id, redline_tags[], trace_id, ts}` | complaint-service | **发布条件**：agent + high + `rule_id∈{R11,R12,R13}`（不是 tag 集合；R05 即使 tag=threat 也不发）。`redline_tags[]` 仅审计。complaint 决定是否建单 |
| `qc.stop_call.detected` | qc-service | `{session_id, case_id, evidence, trace_id, ts}` | case-service（call 可旁听） | 写 cases.stop_call_status（通话已结束，非事中强拆） |
| `qc.review.done` | qc-service | `{qc_record_id, action, revised_score, redline_tags, evidence_masked, sample_type, trace_id, ts}` | rag-service / data-platform | 优质/违规沉淀 / 人工结果回流。`sample_type`∈{positive,negative,corrected}（CONFIRM/DISMISS/CORRECT） |
| `case.assigned` | case-service | `{assignment_id, case_id, type:AI\|AGENT, agent_id?, trace_id, ts}` | call-service / data-platform | AI 入队 / 宽表 |
| `case.reassigned` | case-service | `{assignment_id, case_id, action, needs_review, trace_id, ts}` | 前端 / data-platform | 运营通知 / 宽表 |
| `case.risk.upgraded` | case-service | `{case_id, from_level, to_level, trace_id, ts}` | call-service / review-service | 转人工/安抚 / 风险复盘 |
| `case.service.degraded` | case-service | `{reason, trace_id, ts}` | 告警系统 | DP 熔断后降级通知 |
| `complaint.judged` | complaint-service | `{ticket_id, verdict, confidence, case_id, trace_id, ts}` | review-service / case-service / data-platform | 复盘投诉 / 升级 / 宽表 |
| `complaint.closed` | complaint-service | `{ticket_id, resolution_version, trace_id, ts}` | data-platform / review-service | 宽表 / 报告合并 |
| `complaint.insight.weekly` | complaint-service | `{week_start, summary, trace_id, ts}` | review-service / rag-service | 周报合并 / 整改文档归档 |
| `review.scored` | review-service | `{review_id, call_id, category, score, trace_id, ts}` | complaint-service / case-service / rag-service / data-platform | 证据更新 / 二次分案 / 话术关联 / 宽表 |
| `review.scored.batch` | review-service | `{run_id, total, counts[], trace_id, ts}` | data-platform | DWS 构建 |
| `review.script.published` | review-service | `{snippet_id, rag_document_id, trace_id, ts}` | rag-service | 优质话术入库回执 |
| `training.completed` | review-service | `{session_id, trainee_agent_id, scenario_id, final_score, trace_id, ts}` | data-platform / supervisor | 培训成绩宽表 / 通知 |
| `report.weekly.generated` | review-service | `{report_id, week_start, trace_id, ts}` | data-platform / 邮件告警 | ADS 同步 / 外部通知 |

### 4.3 频道拓扑图

```
┌──────────┐  kb.sensitive_updated   ┌──────────┐
│   rag    │─────────────────────────▶│   qc     │
│ service  │──┐                        │ service  │
└────┬─────┘  │ kb.version.switched   └────┬─────┘
     │        │ (内部)                      │
     │        ▼                             │ qc.check.completed
     │   ┌─────────┐                       │ qc.alert.raised（事后主管）
     │   │  call   │──call.session.ended──┬─▶ qc（离线开工）
     │   │ service │                      ├─▶(事后主管)
     │   └────┬────┘                      │
     │        │ call.session.ended        │
     │        ├───────────────────────────┼─▶┌──────────┐
     │        │                           │  │   data   │
     │        │              etl.task.done│  │ platform │
     │        │              ◀───────────┼──┤          │
     │        │                           │  └────┬─────┘
     │        │              strategy.updated     │
     │        │              ────────────────────▶│
     │        ▼                                ▲   │
     │   ┌──────────┐  case.assigned          │   │
     │   │  case    │─────────────────────────┼───┤
     │   │ service  │──case.risk.upgraded──────┼─▶│
     │   └──────────┘                         │   │
     │                                        │   │
     │   ┌──────────┐  complaint.judged       │   │
     │   │complaint │─────────────────────────┼───┤
     │   │ service  │──complaint.closed──────┬─┘   │
     │   └──────────┘                        │     │
     │                                       │     │
     │   ┌──────────┐  review.scored         │     │
     │   │  review  │─────────────────────────┼────┘
     │   │ service  │──review.scored.batch──▶│
     │   │          │──training.completed───▶│
     │   └──────────┘                       DP
```

## 五、缓存失效与一致性策略

### 5.1 失效模式

| 模式 | 适用 Key | 说明 |
|---|---|---|
| TTL 自然过期 | 会话状态、ETL 缓存、报告缓存 | 超时自动清除，适合容忍短时不一致的场景 |
| 主动 DEL | 规则切换、词库更新、策略变更 | 写 DB 后立即 DEL 缓存，下次读触发回填 |
| Pub-Sub 通知刷新 | 敏感词库、质检规则 | 发布者 DEL 本地缓存 → 发布事件 → 订阅者 60s 内拉取最新 |
| INCR/DECR 原子操作 | 拨打次数、令牌桶、告警计数 | 无需 DEL，原子自增/自减，TTL 到期自动归零 |

### 5.2 一致性原则

1. **DB 为权威源**：所有缓存数据以 PostgreSQL 为准，Redis 仅作加速层；缓存未命中时回源 DB 查询并回填；
2. **先写 DB 后 DEL 缓存**：更新操作先落 PostgreSQL，成功后 DEL Redis key（Cache-Aside 模式），禁止先 DEL 后写 DB（防并发回填脏数据）；
3. **词库热刷新**：rag-service 更新 sensitive_words 后，DEL `rag:sensitive_words:version` → INCR 版本号 → PUBLISH `kb.sensitive_updated`；qc-service 订阅后 60s 内拉取 `/api/v1/sensitive-words/current.dict` 重建 AC 自动机；
4. **规则热切换**：qc-service 更新 qc_rules 后，DEL `qc:rules:active` → 下次读取时从 DB 回填新版本；
5. **策略版本变更**：data-platform 更新 strategy_versions 后，PUBLISH `strategy.updated` → 各服务订阅后刷新本地策略缓存。

### 5.3 缓存击穿与雪崩防护

| 风险 | 防护措施 |
|---|---|
| 缓存击穿（热点 key 过期瞬时穿透） | 分布式锁 `SET NX EX 5s` 限制回源并发；仅一个请求查 DB 回填，其余等待 |
| 缓存雪崩（大量 key 同时过期） | TTL 加随机抖动 ±10%（如 24h → 23h~25h），避免集中过期 |
| 缓存穿透（查询不存在的 key） | 空结果也缓存（TTL=60s），标记 `{"empty": true}` |

## 六、熔断标识与降级状态

### 6.1 LLM 熔断标识

| Key | 服务 | 值 | 语义 |
|---|---|---|---|
| `csmp:llm_degraded` | complaint-service | "1" | 投诉判责 LLM 熔断，走规则兜底 |
| `rv:llm_degraded` | review-service | "1" | 复盘分类 LLM 熔断，走规则兜底 |
| `call:llm_degraded` | call-service | "1" | 外呼对话 LLM 熔断，走 RuleBasedScriptEngine 兜底（P1-13 补） |
| `qc:llm_degraded` | qc-service | "1" | 质检 LLM 审查熔断，走规则引擎审查（P1-13 补，DD-02 已申请） |

> 熔断窗口期内（连续 3 次失败触发），停止调用 LLM，走固定兜底话术/规则决策；窗口期（60s）过后自动清除标识（DEL），恢复 LLM 调用。

### 6.2 服务健康探测

| Key | 探测方 | 被探测方 | TTL | 用途 |
|---|---|---|---|---|
| `cs:capacity:ai_remaining` | case-service | call-service | 10s | AI 剩余并发容量，每 10s HTTP 探 `/health` |
| `dp:scheduler:health` | 告警系统 | data-platform | 10s | 调度器健康，`scheduler_ok` 字段 |

### 6.3 降级链路

```
LLM 连续 3 次失败 → SET {service}:llm_degraded "1"
  → 窗口期内停止 LLM 调用
  → 走 RuleBasedScriptEngine / 规则兜底
  → 60s 后 DEL {service}:llm_degraded（自动恢复）
  → 恢复 LLM 调用
```

## 七、分布式锁设计

### 7.1 锁清单

| 锁 Key | 服务 | TTL | 持锁场景 | 释放方式 |
|---|---|---|---|---|
| `cs:pick_lock:{work_item_id}` | case-service | 15s | 派单 pick 防并发抢同一工单 | DEL / TTL 自然过期 |
| `csmp:gather:lock:{id}` | complaint-service | 60s | 取证幂等，防重复 gather | DEL / TTL 自然过期 |
| `call:simulate:run:{idempotency}` | call-service | 7d | 模拟器幂等，返回已有 call_id | DEL（人工）/ TTL 自然过期 |
| `call:transfer:{session_id}` | call-service | 10min | 转人工防重复触发 | DEL（完成后）/ TTL 自然过期 |

### 7.2 锁实现规范

- 统一用 `SET key value NX EX <seconds>` 原子加锁，禁止 `SETNX` + `EXPIRE` 两步操作（非原子）；
- value 存唯一标识（如 `trace_id` 或随机 UUID），释放时用 Lua 脚本比对后 DEL，防误释放；
- 超时自动释放：TTL 到期自动过期，不阻塞业务流程；
- 禁止续期（MVP 不实现 watchdog），持锁操作须在 TTL 内完成。

```lua
-- 释放锁 Lua 脚本（value 比对后 DEL）
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
```

## 八、连接池与配置规范

### 8.1 连接池参数

| 参数 | 值 | 说明 |
|---|---|---|
| max_connections | 50 | 单服务最大连接数 |
| max_connections_per_db | 50 | 按 DB 隔离，不共享连接池 |
| socket_timeout | 3s | 读写超时 |
| socket_connect_timeout | 2s | 连接超时 |
| retry_on_timeout | 1 | 超时自动重试 1 次 |
| health_check_interval | 30s | 连接健康检查 |

### 8.2 配置注入

各服务通过环境变量注入 Redis 配置：

```env
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=1                    # 按 DB 分配表
REDIS_PASSWORD=               # dev/staging 可空（内网隔离）；prod 必须经 docker secrets 注入
REDIS_MAX_CONNECTIONS=50
REDIS_SOCKET_TIMEOUT=3
REDIS_SOCKET_CONNECT_TIMEOUT=2
```

### 8.3 客户端选型

- 统一使用 `redis.asyncio`（redis-py 异步版），与 FastAPI async 对齐；
- 禁止同步 redis-py 阻塞事件循环；
- Pub-Sub 用独立连接（不占用连接池），`redis.asyncio.Client.pubsub()` 创建订阅对象。

## 九、安全与合规约束

### 9.1 数据脱敏

| 约束 | 说明 |
|---|---|
| 禁止明文缓存 PII | 手机号、身份证号、姓名禁止直接写入 Redis key 或 value；须先经 mask.py 脱敏（打码/哈希） |
| 禁止缓存加密密钥 | AES-256-GCM 密钥经 docker secrets 注入，禁止入 Redis |
| 取证包缓存脱敏 | `csmp:ticket:{id}:pkg` 中的证据片段须脱敏后缓存，原始内容仅在 PostgreSQL |

### 9.2 访问控制

| 约束 | 说明 |
|---|---|
| 服务间 DB 隔离 | 各服务仅访问自己分配的 DB，禁止跨 DB 读写 |
| Pub-Sub 频道归属 | 发布者只能发布自己归属的频道前缀（如 qc-service 只能 publish `qc.*`）；全局告警频道 `system.*`（system.alert.raised / system.alert.resolved）属例外，允许各服务异常处理器（含 qc-service 在 LLM 熔断时）发布，对齐 DD-02 §9.3、step6/02 §3.2 |
| 管理命令限制 | 生产环境禁止 FLUSHALL / FLUSHDB / KEYS，用 SCAN 替代 KEYS |
| 测试环境清理 | 独立测试后 `FLUSHDB` 仅限测试 DB，生产 DB 禁止 |
| 生产认证 | prod 必须开启 ACL/密码（requirepass + 最小权限用户），经 docker secrets 注入；dev/staging 可空（内网隔离） |

### 9.3 X-Trace-Id 透传

- 所有 Redis 写操作（SET/DEL/INCR/PUBLISH）须从 contextvars 读取 `trace_id`；
- Pub-Sub Payload 强制含 `trace_id` 字段，订阅方落库时写入对应 trace_id 列；
- Redis 慢查询日志（slowlog-log-slower-than 10ms）须关联 trace_id 排查。

## 十、验收标准

| 验收项 | 标准 |
|---|---|
| DB 隔离 | 7 服务各用独立 DB，`redis-cli INFO keyspace` 确认无跨 DB 写入 |
| 键命名 | 全部 key 符合 `<prefix>:<entity>:<id>` 格式，无裸 key |
| TTL 覆盖 | 所有缓存 key 均设 TTL（永不过期的除外），无裸 key 泄漏 |
| Pub-Sub 联通 | `redis-cli PSUBSCRIBE` 能收到频道矩阵内测试消息（**不含**已删除的 `qc.escalate.human`） |
| 熔断恢复 | SET llm_degraded "1" 后 60s 自动 DEL，LLM 恢复调用 |
| 分布式锁 | `SET NX EX` 原子加锁，Lua 脚本释放，并发 pick 同一工单只有一个成功 |
| 脱敏验证 | `redis-cli GET` 任意缓存 key，不含明文手机号/身份证/姓名 |
| 连接池 | 单服务 50 并发不超限，`redis-cli INFO clients` 确认连接数正常 |
| 慢查询 | slowlog 无 >10ms 的查询 |
| 数据一致性 | 更新 DB 后 DEL 缓存，下次读取命中新值；词库更新 60s 内 QC 侧生效 |
