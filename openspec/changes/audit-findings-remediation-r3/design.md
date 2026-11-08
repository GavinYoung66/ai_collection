# Design

> 源清单：`问题清单-2026-11-05-r3.md`（60 项，T87–T146）。本文件记录编号体系、批次重排、60 项全覆盖映射、关键技术决策、规格支撑与豁免裁决、验证与风险。任务内容以源清单为准，零改写迁移。

## Context

第三轮审计为只读审计，未修改任何文件、未执行 pytest，结论来自 5 路分区逐行审查 + 主审计员对 P0/P1 亲读复核 + 8 类模式横切 grep。前两期（T01–T86）整改已落地并完成仓库侧闭环，但第二期变更 `audit-findings-remediation-r2` 尚未归档（41/41 任务已完成）。本轮 60 项发现中：

- 47 项具有可观察行为变化或有明确对外契约含义，按 capability 补充 32 条需求（16 份 delta）；
- 13 项为当前不可达的防御纵深、纯性能优化或规范债，无对外可观察行为变化，只进本文件与 `tasks.md`（见 D4）。

本次抽检报告中 59 个代码路径引用，**0 缺失**；抽样亲读 `demo.py`、`mode.py`、`stats.py`、`documents.py`、`llm_reviewer.py`、`asgi_util.py`、`trace/middleware.py` 现场，确认报告描述与代码一致（无鉴权路由组、`BaseHTTPMiddleware`、`read_body` 无上限、demo 签发 token）。

## Goals / Non-Goals

**Goals:**

1. 以 12 个可独立验证的批次覆盖 r3 全部 60 项，逐项可追溯到 `N.M [Txx]` 与验收口径，杜绝清单第五节未显式归批条目的失联。
2. 鉴权末梢收口形成"路由组级 + 端点级"双层守护，优先消除 P0 特权升级路径。
3. 事件循环纪律、事件幂等、有界内存、单源收敛四条主线以公共原语/统一口径一次性落地，避免逐点打补丁。
4. 每项修复附守护/回归单测；结构性缺口补 AST/矩阵式守护测试，防止下一轮再犯。

**Non-Goals:**

- 不重构既有架构分层、不引入新的 Registry/框架抽象、不新增第三方依赖。
- 不修改 r3 报告的任何发现内容、等级或编号；不把"待验证线索 V1–V11"当作已确认缺陷直接改协议行为。
- 不做功能迭代、不改对外 API 路径与字段契约（仅收紧权限/幂等/数据源标识等语义）。
- 不处理基线既有失败（lint_baseline、eventbus 缺 rocketmq、rag test_main、complaint test_mcp_real_path/test_t05、gateway pydantic INTERERROR）。

## Decisions

### D1. 编号体系与 12 批重排

沿用前两期 `N.M [Txx]` 双编号：`N.M` 全局唯一（`1.1`–`12.3` 共 60 个），`[Txx]` 为 r3 原始编号，行内标注 P 级。重排原则：主题同批、严重程度降序、每批 3–7 项可独立验收；跨等级但同主题的条目允许合并（如 T117 P2 并入鉴权批、T142 P3 并入事件循环批），合并关系在下表标注。

| 批次 | 主题                   | 任务（`N.M [Txx]` P级）                                                                                        | 规格支撑                                                                                                                                   |
| ---- | ---------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 1    | 🔴 P0 鉴权末梢收口     | 1.1 [T87] P0、1.2 [T88] P0、1.3 [T89] P0                                                                       | security/authn、complaint-service/mode                                                                                                     |
| 2    | 🔴 P1 鉴权与数据面     | 2.1 [T90] P1、2.2 [T91] P1、2.3 [T92] P1、2.4 [T110] P2、2.5 [T117] P2                                         | security/authn、rag-service/sensitive-words-sync                                                                                           |
| 3    | 🟠 P1 事件循环与流式   | 3.1 [T93] P1、3.2 [T94] P1、3.3 [T99] P1、3.4 [T142] P3                                                        | platform/runtime-correctness                                                                                                               |
| 4    | 🟠 P1 正确性与幂等     | 4.1 [T95] P1、4.2 [T96] P1、4.3 [T97] P1、4.4 [T100] P2、4.5 [T98] P1                                          | complaint-service/llm-layer、platform/observability、platform/runtime-correctness                                                          |
| 5    | 🟡 P2 资源边界与协议   | 5.1 [T101] P2、5.2 [T102] P2、5.3 [T103] P2、5.4 [T104] P2、5.5 [T105] P2                                      | rag-service/hybrid-search、platform/runtime-correctness、platform/mcp-gateway、platform/resilience-primitives                              |
| 6    | 🟡 P2 注入面与单源     | 6.1 [T108] P2、6.2 [T109] P2、6.3 [T111] P2、6.4 [T115] P2、6.5 [T116] P2、6.6 [T118] P2                       | platform/mcp-gateway、platform/runtime-correctness、qc-service/api-layer；6.6 豁免                                                         |
| 7    | 🟣 P2 容错/配置/一致性 | 7.1 [T106] P2、7.2 [T107] P2、7.3 [T112] P2、7.4 [T113] P2、7.5 [T114] P2、7.6 [T119] P2                       | platform/resilience-primitives、complaint-service/mode、platform/observability、complaint-service/graph-nodes、rag-service/document-ingest |
| 8    | 🟣 P2 网关授权与质量   | 8.1 [T120] P2、8.2 [T121] P2、8.3 [T122] P2、8.4 [T127] P3                                                     | security/authn、complaint-service/stats-service、platform/runtime-correctness；8.2 豁免                                                    |
| 9    | 🔵 P3 性能与缓存       | 9.1 [T128] P3、9.2 [T129] P3、9.3 [T133] P3、9.4 [T124] P3、9.5 [T125] P3                                      | rag-service/hybrid-search、qc-service/api-layer、data-platform/min-loop；9.4/9.5 豁免                                                      |
| 10   | ⚫ P3 规范债与防御     | 10.1 [T123] P3、10.2 [T126] P3、10.3 [T130] P3、10.4 [T131] P3、10.5 [T132] P3、10.6 [T134] P3、10.7 [T143] P3 | qc-service/api-layer、qc-service/engine、platform/event-bus；10.1/10.2 豁免                                                                |
| 11   | ⚫ P3 规范债收尾       | 11.1 [T135] P3、11.2 [T136] P3、11.3 [T137] P3、11.4 [T138] P3、11.5 [T139] P3、11.6 [T140] P3、11.7 [T141] P3 | platform/resilience-primitives、rag-service/sensitive-words-sync；其余豁免                                                                 |
| 12   | ⚫ P3 收尾             | 12.1 [T144] P3、12.2 [T145] P3、12.3 [T146] P3                                                                 | security/authn；12.1/12.3 豁免                                                                                                             |

**与 r3 报告第五节的关系**：报告给出批次 A–D 的粗排（约 30 项）并建议批次 D 与 mcp-gateway 授权模型、多副本一致性"另立变更"。本 design 将其重排进 12 批并**全量纳入本变更**，原因：①用户要求"不遗漏、不缺失"；②另立变更需要独立的 proposal/design/tasks 与归档流程，条目失联风险高于批次隔离收益；③T110/T120/T112 仍以独立批次与"先取证"约束隔离实施风险（见 D6）。**未改变报告建议的实施顺序语义**，只把"变更边界"从 4 个变为 1 个。

### D2. 关键技术决策

- **D2.1 鉴权收口（批 1–2）**：优先在 `APIRouter` 声明级挂 `dependencies=[Depends(require_roles(...))]`，端点级已有依赖的保持不动；qc demo 组在生产（`auth_required=true`）**禁用整组**（404/418）或要求 admin，token 签发仅在显式 `DEMO_MODE` 开关下执行；complaint `signers` 改从 `request.state.user`（JWT payload）读取并写独立审批记录，**禁止信任请求体**。data-platform 采用"内部 JWT + 网络隔离声明"最小组合，不引入 mTLS 基础设施（避免超范围）。rag documents list/get 复用 `/search` 的 `KB_ANONYMOUS_CATEGORIES` 与行级矩阵，与 T21 的 fail-closed 语义对齐。T110 先按 V1 取证：若 `GET /mcp` 实际 405 且无匿名路径，则只补显式 405 与矩阵守护；若建立 SSE 流则纳入鉴权中间件。
- **D2.2 事件循环（批 3）**：`ffprobe`/`ffmpeg` 统一 `await asyncio.to_thread(self._probe_duration, ...)`/`to_thread(self._segment_audio, ...)`，保留 `subprocess.run(timeout=...)` 的超时语义（`create_subprocess_exec` 作为备选，需额外处理超时 kill）；`_trim_silence`/`_split_segments`/`_parse_wav` 同法。`TraceMiddleware` 改纯 ASGI：`__call__(scope, receive, send)` 内写 contextvar，包装 `send` 注入 `X-Trace-Id`，**不读取/缓冲响应体**；复用 `metrics.py` 纯 ASGI 形态。T142 当前无调用点，接入时 MUST 用 `to_thread` 或异步探活，否则删除。
- **D2.3 幂等基线（批 4）**：replay 先查 `qc_status`，DONE 直接回读并短路；明细写入用 `pg_insert(...).on_conflict_do_nothing(index_elements=[qc_record_id, rule_id, turn_id])`；幂等登记用 `SET NX`（Redis）或 `INSERT ... ON CONFLICT` 原子占坑，`IntegrityError` 转读回。与 r2 的 `aensure_persisted` 形态一致，不引入分布式事务。T98 的 `_call_log` 只保留 `prompt_preview`（截断 200 字符）与耗时/状态字段，不驻留全文。
- **D2.4 有界内存（批 5）**：统一用 `collections.deque(maxlen=N)`（容量常量集中定义，MUST 可配置或显式常量）；`_SESSION_OWNERS` 改 `OrderedDict`+TTL 清扫（或 LRU 上限），攻击者随机 `X-Session-Id` 不再线性增长；`audit._call_log`、`qc._events`、`complaint._seen_event_ids`、`rag.resilience.emitted` 同法。**不引入通用缓存框架**，避免为 5 处小对象增加抽象。
- **D2.5 单源收敛（批 5–6）**：`eventbus._parse_ts` 改调 `common.utils.time_parse.parse_naive_or_aware`（broker 需 UTC 时以 `default_tz=timezone.utc` 参数化）；dp→qc 的 `session_id`/`complaint_id` 白名单上移或复制 `^[A-Za-z0-9*-]{1,64}$`（与 T79 口径一致）；`pg_dsn` 三个 property 统一 `quote_plus`；qc raw 限流键改 `uid:{sub}`/`ip:{addr}` 并接 `TRUST_PROXY_HEADERS`（对齐 T68）。
- **D2.6 熔断收敛（批 7、11）**：`common/llm/provider.py` 复用 `orchestrator._llm_breakers` 同一实例；qc `rag_client` 改用 `common/resilience/breaker.py` 的 `CircuitBreaker`（半开恢复），`probe_dict_reachable` 在 open 时如实返回 False；LLM 超时统一由 `settings` 单源读取，删除 10s/120s 双口径。**不新建第三套熔断实现**。
- **D2.7 网关（批 5–8）**：`top_k` 在网关前置 `min(max(int(x), 1), 20)`，bridge 层将下游 4xx 标记为不可重试；`read_body` 累计上限 1MB（超限 413）并处理 `http.disconnect`；`qc_mcp_client` 连接前补 `validate_outbound_url`；dispatch 前按 role 做工具白名单，拒绝产生 `MCPPermissionDeniedError`。
- **D2.8 数据正确性（批 4、7）**：judge stub 增加 `USE_MOCK_MCP and USE_MOCK_LLM` 门控与生产拒绝，`run_graph` 生产默认 True；human_review 恢复值非法 → `pending_review` + `exception_info`；同名文档在 DB 层加"active 同名部分唯一索引"+ `IntegrityError` 转 `B0001`；prompt 脱敏在 `ComplaintLLMProvider.invoke` 入口单点调用 `mask_before_persist`。
- **D2.9 性能与缓存（批 9）**：AC 自动机按词库版本缓存实例（`words=` 路径复用模块级单例），prompt loader 加进程级 mtime+size 缓存并在失效时重建；query_cache key 增 `rewrite` 位与长度前缀；dp 出站 client 改 lifespan 单例并在 shutdown `aclose`。
- **D2.10 规范债批清（批 10–12）**：低风险、形态级修正（类型注解、死代码、重复字面量、`__import__`、`os.environ` 副作用、恒等表达式）按"一处一改 + 形态守护单测"处理，**不扩大改动面、不做顺手重构**。

### D3. 路径核验与漂移处置

- 本次对报告引用的 59 个生产路径做存在性核验，**0 缺失**；P0/P1 现场抽样与报告一致。
- 但仍存在同一文件内行号漂移与"任务描述的路径≠实际路径"的历史先例（r2 的 parsers 实际在 `app/parsers/`、T66 消费点在规则引擎）。因此每个任务执行前 MUST 先读目标文件确认现场，路径漂移以实际代码为准并在任务状态行留痕，**不得按报告行号盲改**。
- T118（milvus expr）当前被 pydantic pattern 单点阻断、T142 当前无调用点、T136 全仓无调用点：按"不可达防御纵深/死代码"处理，修复以形态守护与注释固化为主。

### D4. 规格支撑与显式豁免（60 项全覆盖口径）

**规格支撑 47 项**（32 条需求 / 16 份 delta，逐项映射见 D1 表与各 delta 需求内的 `Txx` 标注）：

| capability                       | 需求数 | 支撑编号                                                          |
| -------------------------------- | ------ | ----------------------------------------------------------------- |
| security/authn                   | 4      | T87、T88、T90、T91、T92、T110、T120、T145                         |
| platform/runtime-correctness     | 5      | T93、T94、T97、T98、T99、T100、T102、T105、T111、T115、T127、T142 |
| platform/event-bus               | 1      | T134、T143                                                        |
| platform/observability           | 2      | T96、T113                                                         |
| platform/mcp-gateway             | 3      | T103、T108、T109                                                  |
| platform/resilience-primitives   | 3      | T104、T106、T107、T137                                            |
| complaint-service/mode           | 2      | T89、T112                                                         |
| complaint-service/stats-service  | 1      | T122                                                              |
| complaint-service/llm-layer      | 1      | T95                                                               |
| complaint-service/graph-nodes    | 1      | T114                                                              |
| rag-service/document-ingest      | 1      | T119                                                              |
| rag-service/sensitive-words-sync | 1      | T117、T139                                                        |
| rag-service/hybrid-search        | 1      | T101、T128                                                        |
| qc-service/api-layer             | 4      | T116、T129、T130、T131                                            |
| qc-service/engine                | 1      | T132                                                              |
| data-platform/min-loop           | 1      | T133                                                              |

**显式豁免 13 项**（只进 design 与 tasks，不立规格；理由必须逐项可核）：

| 编号 | 等级 | 豁免理由                                                                                                                                                                            |
| ---- | ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T118 | P2   | milvus expr 为 f-string 拼接，但当前所有入参被 pydantic pattern 单点阻断、无第二条调用路径；属"新增调用点即可达"的防御纵深。修复以入口白名单断言 + 注释固化，无当前可观察行为变化。 |
| T121 | P2   | 生产代码 `import tests.*` 是打包/瘦身健壮性问题，当前 Dockerfile 刻意拷贝 `tests/mock` 兜底、默认启动路径可用；属部署形态债，验收标准是镜像构建而非接口行为。                       |
| T123 | P3   | `Optional` 注解未导入被 `from __future__ import annotations` 掩盖，运行时无行为影响；仅类型检查器/`get_type_hints` 可见，属规范债。                                                 |
| T124 | P3   | AC 自动机每次重建与 `words=` 路径异常是热路径性能问题；输出结果不变，无可观察行为契约（loop-lag/延迟断言另在 T93 覆盖）。                                                           |
| T125 | P3   | prompt loader 每次全量解析属性能问题；输出内容不变，缓存失效策略在 tasks 内固定，无需规格化。                                                                                       |
| T126 | P3   | `time_guard` 不支持跨午夜、非法配置抛 IndexError 属输入健壮性；当前配置均为同日窗口，无既有行为回归，修复附单测即可。                                                               |
| T135 | P3   | `Mapped[dict]` 与默认 `list` 不符是 ORM 注解债，运行时由 SQLAlchemy 默认值兜底，无行为差异。                                                                                        |
| T136 | P3   | `semaphore.py` 全仓零调用点，属死代码；删除/加锁都不改变现有可观察行为。                                                                                                            |
| T138 | P3   | reindex 三副本是代码重复/漂移风险，三个入口当前行为一致；收敛为单源属重构，验收为形态守护而非行为契约。                                                                             |
| T140 | P3   | `verdict if verdict_ok else verdict` 恒等死代码，输出恒为 `verdict`，无行为变化。                                                                                                   |
| T141 | P3   | `__import__("re")` 内联导入属代码风格/可维护性，无行为变化。                                                                                                                        |
| T144 | P3   | `deepseek_api_key_resolved` 的函数内 import 与大小写 hack 属配置解析风格；解析结果与现状一致，规格化收益为零。                                                                      |
| T146 | P3   | `factory.py` 空值回退未 strip 属边界一致性；仅影响空白 API key 的极小概率路径，附单测即可。                                                                                         |

> 豁免不等于不修：13 项全部在 `tasks.md` 中有独立 `N.M [Txx]` 任务与验收口径，仅"不写入 capability 需求"。

### D5. 验证策略

- **单测为主**：每任务附守护/回归单测；重点缺口补结构性守护：
  - 鉴权矩阵扫描：遍历 `APIRouter`，未挂 `require_roles`/鉴权依赖且不在豁免白名单的路由即失败（对应 T87–T90、T110、T117）；
  - 事件循环纪律：AST 扫描 async 函数内直接 `subprocess.run`/`time.sleep`/同步 httpx；loop-lag 断言（T93/T94/T99/T142）；
  - 幂等与有界：重投两次明细行数不变、10 万次调用后缓冲长度 ≤ maxlen（T97/T100/T102）；
  - 单源扫描：`fromisoformat`、未编码 DSN、裸 session_id 拼接、运行期 `os.environ[...] =` 写入（T105/T111/T115/T134/T143）。
- **取证优先**：T110（V1）、T112（V7/V11）、T116（V4 的 nginx 清洗）、T120（V1）MUST 先取得实测/部署证据再定实现；报告中"待验证线索 V1–V11"不作为已确认缺陷直接改协议。
- **环境口径**：本地使用 `~/my_software/miniconda3/envs/ai_collection/bin/python -m pytest`，需注入 `JWT_SECRET`/`AES_KEY`；最终验收以 CI 门禁（static-analysis + 各服务 test + dependency-audit）push 实跑为证据，本地以聚焦单测结果留痕（沿用 R-001 降级口径）。基线既有失败零报告、零修复。
- **防复发机制**：`docs/三项矩阵守护测试方案建议-2026-11-05.md` 的端点×鉴权、生命周期×释放、事件×幂等三项矩阵为**并行方案、不含待修项**，可先以警告模式观察再转 CI 阻断，不作为本变更的验收前置。

### D6. 风险 / Trade-offs

- **[灰度行为收紧]** 批 1–2 的鉴权与批 4 的 stub 门控、批 7 的 signers 来源在生产立即生效 → 提前周知 demo/模式切换/手工 replay/文档读接口/敏感词库/dp 数据面的调用方；错误码沿用既有 401/403/A2002/A5014，不新增对外错误语义。
- **[取证不足即改协议]** T110/T120 在缺少 V1 实测时可能把"理论面"当"实际绕过" → 批次内先写取证任务（curl/MCP 行为记录），未取证只做显式 405/权限拒绝的保守形态，不改 SSE 协议路径。
- **[多副本语义]** T112 依赖 Redis 装配与副本数；T102 的 `_SESSION_OWNERS`、T97 的幂等在单副本下可验证、多副本需 Redis/DB 约束兜底 → 单测覆盖单副本，集成验证登记为运维冒烟项（V7/V11）。
- **[路径漂移]** 历史出现过报告行号/路径与实际不一致 → 执行前必读现场，漂移写状态行；不按行号盲改。
- **[回归面]** 批 2 触及 data-platform 全数据面、批 3 触及 rag ASR、批 5 触及 eventbus/gateway 中间件 → 单服务改动不跨批合并提交，每批 CI 绿后再进下一批；优先跑受影响服务的聚焦用例，不全量重复跑。
- **[归档顺序]** `platform/runtime-correctness` 与 `security/authn` 在 r2 中已有未归档 delta → 归档 MUST 先 r2 后 r3，避免同一 capability 双 ADDED 冲突；需求标题已逐条比对无重名。

### D7. 待验证线索处置归属与 Open Questions（不改变规格与任务拆分，可实施中取证）

r3 清单第三节 11 条线索"不计入问题统计"，均不直接进入 60 项任务范围；处置归属逐条如下。取证结论留痕于对应任务状态行或 `tasks.md` Workflow follow-up；若确认为缺陷，按变更修订流程追加任务并同步 proposal/design/specs，MUST NOT 擅自扩大既定范围：

| 线索 | 内容摘要                                                                                                  | 处置归属                                                                                                        |
| ---- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| V1   | `GET /mcp` 实际返回 405 还是建立 SSE 流                                                                   | 挂任务 2.4/8.1 内取证——决定"显式 405"还是"鉴权中间件覆盖"，两种实现均满足 security/authn 需求                   |
| V2   | rocketmq `FilterExpression` 双 tag 表达式默认 expression_type 是否 SQL92（错则消费组收不到消息）          | follow-up 取证：核对 rocketmq-client 签名 + proxy 实测双 tag 订阅；确认缺陷后追加任务                           |
| V3   | complaint publisher 临时 loop 与单例 bus 的跨 loop 残留                                                   | 二期 T73 已对 fallback 分支加注释警示（仅限脚本/单测）；挂起实测保留在 follow-up，确认后追加任务                |
| V4   | deploy nginx 是否清洗/覆写 `X-User-Id`                                                                    | 挂任务 6.5 内取证（含 complaint `resolve_user_key` 同型点核查）；规格已要求 `uid:/ip:` 命名空间，实现可独立完成 |
| V5   | `audit._call_log` params 快照消费方是否仅 tests                                                           | 挂任务 5.2 内取证（决定有界化形态：直接 deque 还是保留测试视图）                                                |
| V6   | dp `qc_source` 的 `AT TIME ZONE 'Asia/Shanghai'` 会话时区依赖（end_time 列类型 + 会话 TZ 非上海时偏一天） | follow-up 取证：`\d call_records` + `SHOW timezone`；确认偏一天后追加数据正确性任务                             |
| V7   | dp `_pending_retries` 补偿队列多副本易失（docstring 自述秒级窗口取舍）                                    | 并入任务 7.3 与运维冒烟的部署形态输入（见 D6 多副本风险）                                                       |
| V8   | rag `_resolve_ingest_category` other→regulation 启发式可能提升匿名可见类别                                | 策略风险非缺陷：follow-up 与产品确认预期，不改代码                                                              |
| V9   | BoundedQueue `wait_for(get(), timeout)` 在 Python <3.12 的唤醒-取消丢 item 竞态                           | follow-up 取证：确认运行时镜像 Python 版本；≥3.12 则消解                                                        |
| V10  | `_maybe_sim_auto_review` 未列入生产禁用校验                                                               | follow-up 取证：随任务 4.1 同批评估启动校验 `is_production` 断言（与 judge stub 门控同族）；确认后追加任务      |
| V11  | complaint 多副本 create_session 双行（InMemoryRedis setnx 跨进程无效）                                    | 并入任务 7.3 的多副本验证（V7/V11 同批运维输入）；规格"多副本一致"要求不变                                      |

## Migration Plan

1. **批 1 先行**：P0 鉴权收口单独提交、单独灰度；同步发出调用方周知（demo/check/mode/stats）。
2. **批 2–4**：按服务分组提交（qc / complaint / rag / dp），每批 CI 绿后进入下一批；批 4 的 replay 压测依赖批 1 的 T88 完成。
3. **批 5–8**：防护纵深按"common 公共件 → gateway → 服务"顺序；gateway 改动先跑单文件收集（规避 pydantic 2.5 全量 INTERNALERROR 基线失败）。
4. **批 9–12**：P3 批清可并行，但每项独立提交，禁止跨服务合并。
5. **回滚**：全部改动为代码级开关/依赖注入，可按服务回滚镜像；鉴权批回滚需同时回滚 router 依赖，不涉及数据迁移（仅 T119 新增索引，回滚保留索引无副作用）。
6. **归档**：先归档 `audit-findings-remediation-r2`，再归档本变更；归档前校验 60/60 任务勾选、validate 通过、16 份 delta 与主 specs 合并无冲突。
