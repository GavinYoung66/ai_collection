# RAG 多模态知识库 V3 遗留问题修复（rag-multimodal-v3）

## Why

RAG 多模态知识库已完成 V1（rag-service-v1）与 V2（rag-multimodal-kb-v2）两个阶段开发并归档，核心检索链路（双路召回 → RRF → Rerank → 双闸门兜底）、多模态入库（OCR/ASR）、智能检索管道（Query 改写/动态路由/加权/行级权限）均稳定可用。但 V2 封版校验与归档评审遗留了一批非阻塞缺陷与偏差：3 项安全/稳定性风险（Redis 故障连锁 500、权限过滤 fail-open 越权、ES 入库失败无降级）、1 项能力缺口（ASR VAD 分片为桩实现）、3 项功能未落地（seed 初始化 PUBLISH 事件、Whisper Provider、缓存命中日志字段）、1 项测试基建缺口（fixture 缺失）以及 15 条低危文档偏差。这些遗留问题不修复将影响生产稳定性与下游联动（qc-service 热刷新），本次变更集中解决 V1/V2 遗留问题，**不改动 V1/V2 已确认的成熟功能与对外契约**。

## What Changes

**本次变更只处理 V1/V2 遗留问题，全部为增量式修复，不做任何无关重构、不新增遗留清单之外的新能力。**

- **修复安全/稳定性缺陷**：
  - 权限过滤异常由 fail-open（静默放行越权数据）改为 **fail-closed**（权限服务异常时保守放行需显式判定，缺省走默认矩阵，不再跳过过滤）
  - Redis 故障时多轮会话读写（`session_svc.get_recent_queries/record`）与熔断探测（`rerank.is_degraded()`）纳入容错，不再导致检索接口 500
  - ES 入库失败提供降级开关（仅 Milvus 入库 + 补索引重试），避免入库全量失败
- **补齐能力缺口**：
  - startup 敏感词 seed 初始化后 PUBLISH `kb.sensitive_updated` 事件（payload 复用 `{"version":int,"trace_id":str,"ts":int}`）
  - ASR VAD 分片真实实现（静音裁剪 + ≤60s 分片转写 + 结果拼接）
  - Whisper Provider 真实实现（替换 `NotImplementedError` 骨架）
  - OCR/ASR 解析失败降级策略（非直接 A3001 失败）
- **修复行为偏差**：
  - 缓存命中分支补传 `query_original/query_rewritten/intent_tag` 冗余字段（缓存命中场景 `query_rewritten`/`intent_tag` 允许 NULL，见 search-logging spec）
  - 移除请求体 `session_id` 字段（对外仅以 `X-Session-Id` 请求头为准，对齐 conversational-search spec；该字段已从 `SearchRequest` 模型移除，不再从请求体读取）
  - 清理 `SearchRequest.include_answer` 废弃死字段（PRD §1.4 明确 V1 不接 LLM、不生成话术，该字段从未生效，本次删除并对齐 PRD）
  - **D2 回溯修复（rerank 加权主路径生效）**：业务加权（`KB_WEIGHTING_ENABLED`）此前仅作用于 RRF 融合分（`score`），Rerank 正常路径按纯 `rerank_score` 重排导致加权被覆盖；本次修复为 Rerank 正常路径排序分 = `rerank_score × weight`（weight 缺省 1.0），使加权在主路径正确生效（含 D-1 闭环：`max_rerank_score` 取全局最大原始 rerank_score 不乘权重，闸门 2 阈值不被加权排序污染；详见 tasks §10 D2 条目与 smart-retrieval-pipeline spec）
- **测试基建**：补齐 `async_db_session` fixture，移除 `test_0004_seed_consistency.py` 的 skipif 标记并恢复断言
- **文档修正**：L1-L4、L6、L7、L9-L14 共 12 条 spec/design/proposal/README/代码注释措辞偏差修正（与代码实际行为对齐）

**明确不做（Non-Goals，移交后续阶段）**：

- LangSmith 追踪接入、评估模块、文档版本管理+灰度、Prompt 动态配置、敏感词 CRUD（DD-03 §11.4 V3-T1~T5，属治理能力，另行排期）
- LLM 摘要生成模块及其降级链（19.5：本次无实施对象，仅作为设计约束记录——若未来引入摘要生成必须同步设计降级链路）

## Capabilities

### New Capabilities

无新增能力。本次全部为对既有能力的遗留问题修复。

### Modified Capabilities

- `rag-service/multimodal-ingest`：ASR VAD 分片真实实现（静音裁剪 + 60s 分片转写拼接）；Whisper Provider 由骨架实现为可用 Provider；OCR/ASR 解析失败降级策略；`source` 字段记录值修正为 `"asr"`（L1）
- `rag-service/document-ingest`：ES 入库失败降级开关（仅 Milvus 入库 + 补索引重试）；格式白名单补录 `.jpeg`（L6）
- `rag-service/sensitive-words-sync`：startup seed 初始化后 PUBLISH `kb.sensitive_updated` 事件；seed description 与日志键修正（L2/L3）
- `rag-service/search-logging`：缓存命中分支补传 `query_original/query_rewritten/intent_tag` 字段（L8）
- `rag-service/conversational-search`：移除请求体 `session_id` 字段（仅 `X-Session-Id` 生效）；会话 Redis 故障容错
- `rag-service/search-enhancements`：行级权限过滤异常由 fail-open 改为 fail-closed（缺省矩阵兜底）
- `rag-service/hybrid-search`：兜底文案修正（L7）；熔断探测 `rerank.is_degraded()` 容错
- `rag-service/smart-retrieval-pipeline`：动态路由权重配置与代码对齐（case:1.0 / other:0.6，L4）

## Impact

- **代码（增量修复，不改动既有逻辑分支）**：`services/rag-service/app/services/`（asr_client、asr_providers/whisper_provider、ingest_service、search_service、row_access_service、sensitive_words_sync、session_service）、`app/api/search.py`、`app/main.py`（startup seed 分支）、`app/models/search.py`（移除 session_id 字段）、`app/vectorstore/milvus_client.py`（注释修正）
- **测试**：`tests/test_0004_seed_consistency.py`（移除 skipif、恢复断言）、新增 VAD/Whisper/降级/容错用例、`tests/conftest.py`（补 `async_db_session` fixture）
- **配置**：新增 ES 入库降级开关（默认 false=V1 行为，对齐既有开关体系）、ASR_PROVIDER 已有配置复用
- **文档**：`openspec/specs/rag-service/` 8 个能力主 spec、V2 归档 artifacts（design/proposal/README 措辞）、代码注释
- **兼容性**：全部修复以开关或向后兼容方式接入，关闭开关/缺省路径行为与 V1/V2 完全一致；对外接口路径、统一响应包、错误码不变
