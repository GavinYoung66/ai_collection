# RAG 多模态知识库 V3 遗留问题修复 — 技术设计

## Context

RAG 多模态知识库已完成 V1（rag-service-v1）与 V2（rag-multimodal-kb-v2）两个阶段开发并归档，核心检索链路稳定（双路召回 → RRF → Rerank → 双闸门兜底，8 个能力主 spec 已同步至 `openspec/specs/rag-service/`）。V2 封版校验与归档评审登记了 19 项遗留项（偏差说明 B2/M1/L1-L15 + 归档 tasks §19 风险登记），本设计只针对其中**代码级遗留问题**做增量修复，**不改动 V1/V2 已确认的核心业务逻辑**（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10 保持原样）。详见 proposal.md Why。

### 既有代码约束（本次设计必须遵守）

- **分层**：Controller → Service → Repository 单向依赖（openspec/specs/01_arch_constraint），新增逻辑必须落在 Service 层，Controller 不做业务判断。
- **错误码**：禁止新增错误码，复用既有 A3001/A3002/A3003/A3005/B000x（openspec/specs/06_exception_alarm）。
- **开关接入**：沿用 V2 开关体系（`KB_QUERY_REWRITE_ENABLED` 等），新增开关默认 `false`，关闭时行为与 V2 完全一致（openspec/specs/rag-service/hybrid-search）。
- **LLM 兜底**：所有 LLM/外部服务调用必须带超时/重试/熔断/降级（openspec/specs/04_llm_guardrail）。
- **响应格式**：统一 `{"code":"0",message,data,trace_id}`（openspec/specs/02_api_stream_spec）。
- **测试**：核心模块覆盖率 ≥85%，配套 LLM 回归用例，合规红线拦截率 100%（openspec/specs/07_test_constraint、00_biz_redline）。

## Goals / Non-Goals

**Goals：**

- 修复 V2 遗留的 3 项安全/稳定性缺陷（Redis 连锁 500、权限过滤 fail-open 越权、ES 入库失败无降级）
- 补齐 3 项功能缺口（seed 初始化 PUBLISH、ASR VAD 分片、Whisper Provider）
- 修复 3 项行为偏差（缓存命中日志字段、请求体 session_id 移除、OCR/ASR 失败降级）
- 补齐测试基建（async_db_session fixture）并修正 12 条文档措辞偏差

**Non-Goals：**

- 不引入 LangSmith/评估模块/版本灰度/Prompt 动态配置/敏感词 CRUD（DD-03 §11.4 V3-T1~T5，另行排期治理变更）
- 不实现 LLM 摘要生成模块及其降级链（19.5 仅记录为设计约束，无实施对象）
- 不修改 V1/V2 对外接口路径、统一响应包结构、错误码全集
- 不做任何无关重构（如不改动 query_cache、rate_limit、require_role 等无遗留问题的模块）

## Decisions

### D1. 权限过滤异常 fail-open → fail-closed（19.3）

- **现状**：`search_service.py` L316-322 中 `row_access_svc.filter` 抛异常时仅 `logger.warning` 后跳过过滤（fail-open，可能返回越权数据）。
- **改动**：仅在 `search_service.py` L321 的 `except` 分支内调整处理策略：① 记录 error 级日志（含 trace_id）；② 调用新增的 `RowAccessService.get_default_matrix()`（新增方法，返回 `(default,default) → {regulation,script,case,other}`）重放过滤；③ 若保底过滤仍抛异常，则置空 `reranked=[]` 走下游兜底闸门。**不改变 filter 方法本身签名**。
- **触碰旧代码**：`app/services/search_service.py` L321-322（except 分支，仅 2 行内改）。改动原因：消除越权风险。影响范围：仅 `KB_ROW_LEVEL_ENABLED=true` 且权限矩阵异常时；开关关闭时零影响。
- **备选**：让 filter 内部吞异常返回全量——被否决（治标不治本，违背 fail-closed 语义）。

### D2. Redis 故障连锁 500 容错（19.2）

- **现状**：`search_service.py` L288 `rerank.is_degraded()` 未包 try/except；L392-393 `session_svc.record` 未包 try/except。Redis 不可用时抛异常 → 检索接口 500。
- **改动**：① L288 熔断探测包 `try/except Exception`，异常按"未熔断"处理（`rerank_pre_degraded=False`），warning 日志；② L392-393 会话写入包 `try/except Exception`，失败仅 warning 日志，不阻断检索返回。session 读取路径 L96-98/L116 已有 try/except（V2 已容错），不动。
- **触碰旧代码**：`app/services/search_service.py` L288、L392-393（仅包裹 try/except）。改动原因：消除 Redis 故障连锁 500。影响范围：Redis 故障时检索仍正常返回（degraded 按 V1 规则标记）。
- **备选**：在 Redis 客户端层统一容错——被否决（改动面大，且 V2 已按调用点容错）。

### D3. seed 初始化后 PUBLISH kb.sensitive_updated（B2）

- **现状**：`main.py` `_ensure_sensitive_words_baseline`（L100-101 seed INSERT、L122-132 version 初始化）seed 成功后未 PUBLISH；`sensitive_words_sync.py` refresh 路径已实现 PUBLISH（L120-137，payload `{"version":int,"trace_id":str,"ts":int}`）。
- **改动**：在 `main.py` seed 分支（自检执行了 INSERT 使 count<50→=50）成功后，复用 `SensitiveWordsSyncService.publish_version()`（新增方法，抽取 refresh 中已有的 PUBLISH 逻辑，避免复制）发布版本事件。若该方法已存在同名能力则直接复用。
- **触碰旧代码**：`app/main.py` L100-139 段内 seed 成功分支追加发布调用（不删除任何既有逻辑）。改动原因：满足 spec「含 seed 初始化、手动 refresh 两种触发」。影响范围：仅首次 seed 上线触发一次发布，qc-service 60s 内热刷新；发布失败仅 warning（V2 既有降级兜底）。
- **备选**：在 refresh 方法内统一处理——被否决（startup 与 refresh 是两条路径，耦合会让 startup 依赖 refresh 语义）。

### D4. ASR VAD 分片真实实现（M1）

- **现状**：`asr_client.py` L69-75 `_trim_silence` 直接透传、L78-84 `_split_segments` 返回单元素列表（桩）。
- **改动**：在 `asr_client.py` 内实现两个静态方法：
  - `_trim_silence`：仅对 **WAV（PCM）** 格式做首尾静音裁剪（基于 RMS 能量阈值，如 <0.01 判静音，裁剪窗口 0.1s）；非 WAV 格式透传（MP3/M4A 无解包能力，依赖 Provider 侧处理）。
  - `_split_segments`：对 WAV 按 ≤60s 硬分片（如 60s×16000Hz×2bytes 边界），非 WAV 透传单元素。
  - `transcribe`：拼接逻辑——若分片 >1 且 Provider 支持分段（DashScope），逐片转写后按序拼接；否则整段透传 Provider。
- **触碰旧代码**：`app/services/asr_client.py` L69-84（两个静态方法体重写，方法签名 `(audio_bytes, fmt)` 不变）。改动原因：实现 spec 要求的分片转写。影响范围：仅 V2 音频上传路径；对 ≤60s 音频行为与 V2 一致（单分片）。
- **备选**：引入 webrtcvad 库做真实 VAD——被否决（新增重型依赖，且需求文档仅要求"静音裁剪 + 60s 分片"，RMS 能量阈值足够）。

### D5. Whisper Provider 真实实现（L5）

- **现状**：`whisper_provider.py` L11-17 `raise NotImplementedError`。
- **改动**：实现 WhisperProvider 的 `transcribe`：调用本地 Whisper（通过 `openai-whisper` 或 `faster-whisper`），返回与 DashScope Provider 一致的 `{text, ...}` 契约；模型加载失败/转写异常 → 按 Provider 抽象既有异常路径抛出（由 asr_client 统一处理为 A3001 或降级）。**该依赖（faster-whisper）需加入全局 `requirements.in/lock`**（对齐项目单一依赖文件约束）。
- **触碰旧代码**：`app/services/asr_providers/whisper_provider.py` L11-17（骨架实现为可用实现，类签名不变）。改动原因：完成 L5 移交项。影响范围：仅 `ASR_PROVIDER=whisper` 时生效，默认 DashScope 路径零影响。
- **风险**：本地 Whisper 模型体积/CPU 性能。→ 缓解：使用 `tiny/base` 尺寸默认、加载失败降级日志；spec 场景标注"V3 已实现，性能以实际环境为准"。

### D6. OCR/ASR 解析失败降级（19.1）

- **现状**：`ingest_service.py` L61-68 OCR/ASR 解析失败直接 A3001 + status=failed。
- **改动**：新增开关 `KB_OCR_ASR_DEGRADED_KEEP_TEXT`（默认 false）。开关开启时：解析失败不再直接 A3001，改走降级分支——若可取得纯文本（如请求附带文本）则跳过多模态解析直接入库；否则 status=failed + 解析失败日志 + 返回 data 标记 degraded=true（不抛 A3001 语义）。开关关闭时与 V2 完全一致。
- **触碰旧代码**：`app/services/ingest_service.py` L61-68（解析失败分支加开关判断分支）。改动原因：消除 OCR/ASR 无降级缺陷。影响范围：开关默认关闭零影响。
- **开关登记**：需加入 `settings`、Dockerfile ENV、`.env.example`（对齐 V2 7 开关做法）。

### D7. ES 入库失败降级（19.4）

- **现状**：`ingest_service.py` L142-145 ES 写入失败直接 A3003 + 回滚。
- **改动**：新增开关 `KB_ES_DEGRADED_KEEP_MILVUS`（默认 false）。开关开启时：ES index_docs 失败 → 保留 Milvus + kb_chunks，status=active，记录 degraded + 异步补索引任务（`asyncio.create_task`，指数退避 1s/2s/4s…，30min 窗口）。开关关闭时与 V2 完全一致（A3003 + 回滚）。
- **触碰旧代码**：`app/services/ingest_service.py` L142-145（ES 写入失败分支加开关判断）。改动原因：消除 ES 入库失败无降级缺陷。影响范围：开关默认关闭零影响。

### D8. 缓存命中日志补齐三字段（L8）

- **现状**：`api/search.py` L81-97 缓存命中分支落 kb_search_logs 未传 query_original/query_rewritten/intent_tag（落 NULL）。
- **改动**：在缓存命中分支组装日志时，从请求上下文补充：`query_original=req.query`；`query_rewritten`/`intent_tag` 取该请求改写/路由结果（若请求未启用改写/路由则落 None）。若请求上下文拿不到改写结果，则记录 query_original 即可，其余允许 NULL。
- **触碰旧代码**：`app/api/search.py` L81-97（日志组装行补字段）。改动原因：满足 search-logging spec 全字段记录。影响范围：仅缓存命中日志内容，不改变响应。

### D9. 移除请求体 session_id 字段（L15）

- **现状**：`models/search.py` L11 `session_id: str | None` 定义但代码只读 `X-Session-Id` 请求头（api/search.py L67），字段从未生效。
- **改动**：从 `SearchRequest` 移除 `session_id` 字段（Pydantic 模型删行）；`api/search.py` 不引用 `req.session_id`（已确认无引用，仅删模型字段）。对外契约以 `X-Session-Id` 请求头为准（与 conversational-search spec 一致）。
- **触碰旧代码**：`app/models/search.py` L11（删除一行）。改动原因：消除无效字段误导。影响范围：请求体携带 session_id 会被 Pydantic 忽略（默认忽略未知字段），不破坏调用方。

### D10. 测试基建 fixture 补齐（19.6）

- **现状**：`tests/conftest.py` 无 `async_db_session` fixture，`test_0004_seed_consistency.py` 自创建即 setup ERROR，已加 `skipif(RAG_DB_TESTS!=1)`。
- **改动**：在 `tests/conftest.py` 新增 DB-backed `async_db_session` fixture（受 `RAG_DB_TESTS` 环境变量控制，未启用时保持 skip）；`test_0004_seed_consistency.py` 移除 skipif 标记、恢复断言。注意：**seed 测试 skip 标记（V2 归档约定）本次按 19.6 任务明确解除**，属 V3 既有排期项，非擅自改动。
- **新增文件**：无；修改 `tests/conftest.py`、`tests/test_0004_seed_consistency.py`。

### D11. 文档措辞修正（L1-L4、L6、L7、L9-L14，共 12 条）

- 全部为文档/注释文字对齐，**不涉及行为改动**，逐条按偏差说明第四节修正：L1（source="asr"）、L2/L3（seed description/日志键）、L4（intent_routes case=1.0）、L6（白名单 .jpeg）、L7（兜底文案）、L9（milvus_client 注释）、L10（design expired 字段）、L11（README alias 描述）、L12（迁移编号 0004/0005/0006）、L13（OCR lang="ch"）、L14（role upper 归一）。
- **触碰文件**：`openspec/specs/rag-service/` 8 个主 spec（归档后经 /opsx-sync 同步）、V2 归档 design/proposal/README、`milvus_client.py` L5 注释、`intent_routes.yaml` 注释。

### D12. rerank 加权主路径生效（回溯修复，审计 D2/D-1）

- **现状**：业务加权（`KB_WEIGHTING_ENABLED`）仅在 RRF 融合前通过 `WeightingService.apply` 放大融合分 `score`；Rerank 正常路径最终按纯 `rerank_score` 重排，导致加权被覆盖不生效。
- **改动**：Rerank 正常路径排序分 = `rerank_score × weight`（`weight` 缺省 1.0，由 `apply` 写入 `item["weight"]`）；`max_rerank_score` 取**全局最大**原始 `rerank_score`（不乘权重），保持闸门 2 的 0.35 兜底阈值语义不被加权排序污染（D-1 副作用闭环）；Rerank 降级（RRF score 排序）路径因 `score` 已放大加权同样生效；开关关闭时 `weight` 缺省 1.0，退化 V2 行为。
- **触碰旧代码**：`app/services/search_service.py` Rerank 排序与 `max_rerank_score` 计算段（约 L304-319）。改动原因：消除加权被精排覆盖的缺陷（对齐 smart-retrieval-pipeline spec「权重作用范围说明」）。
- **测试**：`tests/test_gate_threshold.py` 新增 `TestGate2ThresholdNotPollutedByWeighting` 2 用例（防误兜底 / 防误放行）。
- **备选**：Rerank 后按加权分二次重排——被否决（引入二次排序，语义与 RRF 加权不一致）。

### D13. include_answer 废弃字段清理（回溯修复，审计 D5）

- **现状**：`models/search.py` `SearchRequest.include_answer: bool = False` 为 V1-era 遗留死字段（PRD §1.4 明确"不生成话术、不接 LLM（include_answer 已移除）"），从未被业务代码引用。
- **改动**：从 `SearchRequest` 删除 `include_answer` 字段（Pydantic 模型删行）；无其他代码引用（已 grep 确认）；请求体携带该字段会被 Pydantic 默认忽略，不破坏调用方。
- **触碰旧代码**：`app/models/search.py` 删除一行。改动原因：对齐 PRD §1.4 与 hybrid-search spec 归档标记，消除无效字段误导。
- **备选**：保留字段并标注 deprecated——被否决（死字段应删除而非保留）。

## Risks / Trade-offs

- [权限过滤 fail-closed 可能误伤] → 仅当矩阵异常时触发，保底矩阵 `(default,default)` 全量放行 = V1 兼容；即使空结果也由下游兜底闸门接管，不抛 403。
- [ASR VAD 仅支持 WAV 硬分片] → MP3/M4A 仍整段透传 Provider（V2 现状），仅 WAV 增强；spec 已按"≤60s 分片"收敛，超长非 WAV 录音性能依赖 Provider。
- [Whisper 本地模型性能] → 默认 tiny/base 尺寸 + 加载失败降级日志；不阻塞默认 DashScope 路径。
- [ES 补索引异步任务生命周期] → 用 `asyncio.create_task` + 30min 窗口 + 指数退避，任务失败仅日志；进程重启后未补索引文档由后续 reindex 兜底，不保证最终一致性（document-ingest spec 已注明）。
- [seed PUBLISH 重复发布] → 仅当自检实际执行 INSERT（count<50→=50）时发布一次；已存在 50 词时跳过不发布，保证幂等。

## Migration Plan

1. **代码**：按 tasks.md 顺序增量实现，全程保持 7 开关默认 false（新增 `KB_OCR_ASR_DEGRADED_KEEP_TEXT`、`KB_ES_DEGRADED_KEEP_MILVUS`）。
2. **配置**：新增 2 个开关 ENV 加入 Dockerfile、`.env.example`、settings。
3. **依赖**：Whisper 依赖加入全局 `requirements.in` → `pip-compile` 更新 `requirements.lock`。
4. **测试**：新增/调整单测（VAD 分片、Whisper mock、fail-closed、Redis 容错、ES 降级、缓存日志字段、seed PUBLISH），全量回归 150+ passed；冒烟脚本保持不变（Step10 不动）。
5. **文档**：归档时经 /opsx-sync 将 8 个 delta 合入主 spec；12 条措辞修正随本变更落盘。
6. **回滚**：所有行为改动均以开关隔离，回滚 = 关闭新增开关即可恢复 V2 行为；无数据库 schema 变更、无迁移脚本，回滚零成本。

## Open Questions

- 无阻塞性问题。Whisper 模型默认尺寸（tiny/base/small）与部署形态（本地装模型 vs 独立 sidecar）留待 apply 阶段按实际环境资源决策，不影响 spec/任务拆分。
