# RAG 多模态知识库 V2 封版偏差说明

> 文档属性：封版校验配套文档（只读，不参与代码变更）
> 变更：openspec/changes/rag-multimodal-kb-v2
> 生成日期：2026-08-22
> 处理原则：不改动 V2 已确认核心业务逻辑——Embedding ES-only 降级、alembic 迁移（0004/0005/0006 双写过渡链）、冒烟 Step10、seed 测试 skip 标记全部保持原样。
> 处置口径：仅三类——① 代码修复（落盘）；② 归档后文档修正（不写代码）；③ V3 迭代修复（移交 rag-multimodal-kb-v3）。

---

## 一、处理结论总览

| 编号   | 等级 | 条目                                                     | 处置        | 落地方式                                       |
| ------ | ---- | -------------------------------------------------------- | ----------- | ---------------------------------------------- |
| B1     | 阻断 | ruff F401 ×5 未使用导入                                  | 已修复      | 代码落盘（本说明随附 diff）                    |
| B2     | 阻断 | startup 敏感词初始化未 PUBLISH kb.sensitive_updated 事件 | V2 不实现   | 写入本变更归档备注，移交 V3                    |
| M1     | 中危 | ASR VAD 分片桩实现（静音裁剪/60s 分片透传）              | V3 已知限制 | 仅文档记录，V2 完全不动                        |
| L1-L15 | 低危 | spec 文字/代码注释/json 字段/文档描述不一致（15 条）     | 不修改代码  | 本说明逐条记录（归档后文档修正 / V3 迭代修复） |

---

## 二、阻断项

### B1. ruff F401 未使用导入（5 处）→ 已修复落盘

- 位置：`tests/test_0004_seed_consistency.py:7`（asyncio）、`tests/test_logs_v2.py:2`（uuid）、`tests/test_sensitive_words_api.py:6`（AsyncMock/patch）、`:23`（Path）
- 修复：`ruff check --fix --select F401`，5 fixed / 0 remaining，无业务逻辑改动
- 验证：受影响 3 个测试文件 8 passed / 1 skipped（skip 标记保持原样）

### B2. startup 敏感词初始化未 PUBLISH kb.sensitive_updated 事件 → V2 不实现，移交 V3

- 规格要求：sensitive-words-sync spec「Pub-Sub kb.sensitive_updated 事件发布」——系统 SHALL 在敏感词版本号变更（**含 seed 初始化、手动 refresh 两种触发**）后 PUBLISH `{"version":int,"trace_id":str,"ts":int}`
- 实际实现：`refresh()` 已实现 PUBLISH（[sensitive_words_sync.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/sensitive_words_sync.py#L120-L137)）；但 startup `_ensure_sensitive_words_baseline` 仅 `SET rag:sensitive_words:version 1 NX`（[main.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/main.py#L122-L132)），**未对 seed 初始化触发 PUBLISH**
- 影响：qc-service 侧"seed 首次上线的 60s 热刷新"只能靠 version GET 轮询兜底，无法收到主动事件
- 处理：**本变更 V2 不实现该能力，不改代码**。写入 V2 归档备注：移交 **rag-multimodal-kb-v3** 作为 V3 待实现需求（在 seed 初始化分支补齐 PUBLISH，payload 复用现有 refresh 的 JSON 契约）。

---

## 三、中危项

### M1. ASR VAD 分片桩实现 → V3 已知限制，V2 完全不动

- 规格要求：multimodal-ingest spec「前置裁剪静音段和通话开场白，每段 ≤ 60s 分片转写，结果拼接」
- 实际实现：[asr_client.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/asr_client.py#L68-L84) `_trim_silence` 直接透传、`_split_segments` 返回单元素列表（代码注释自认"实际 VAD 推迟到后续迭代"），整段音频一次性交 Provider
- 影响：90s 录音场景实际为单次整段转写；功能可用（DashScope 侧仍能转写），仅规格描述超前
- 处理：V3 已知限制，V2 完全不动，仅文档记录

---

## 四、低危偏差清单（15 条，不修改代码）

### A. spec 文字不一致（8 条）

| #   | 偏差点（规格原文 → 代码实际）                                                                                                                                                                                                                                                                                                                                                                                         | 影响等级 | 处理方案                                                            |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------- |
| L1  | multimodal-ingest spec:27 `source 字段记录为 "asr:dashscope"` → 代码 [ingest_service.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/ingest_service.py#L23-L34) 写入 `"asr"`（hybrid-search spec 值域 v1_legacy/ocr/asr 与代码一致，属 spec 单侧过时）                                                                                                                | 低       | 归档后文档修正（spec 文字改为 `"asr"`）                             |
| L2  | sensitive-words-sync spec:23 seed SQL 模板 description=`'startup_seed_v2_fallback'` 且声明"与 migration 0004 seed 完全相同" → 代码 [main.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/main.py#L100-L101) 为 `'seed_baseline_from_dict'`、0004 迁移为 `'seed_migrated_from_v1_dict'`，三处各不相同                                                                           | 低       | 归档后文档修正（spec 删"完全相同"表述，注明三处 description 语义）  |
| L3  | sensitive-words-sync spec:27/31/35 日志键 `startup_sensitive_seed_success` / `sensitive_startup_self_check_failed` / `sensitive_startup_seed_skip` → 代码 [main.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/main.py#L72-L139) 为 `sensitive_baseline_*` 系列                                                                                                               | 低       | 归档后文档修正（spec 日志键与代码对齐；运维按 spec 检索日志会落空） |
| L4  | smart-retrieval-pipeline spec:27/31 "其余类 category×0.6 / case ×0.6 重算 rank" → 配置 [intent_routes.yaml](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/conf/intent_routes.yaml#L10-L14) `case: 1.0`、`other: 0.6`（test_routing.py 断言 case==1.0，配置自洽）                                                                                                                     | 低       | 归档后文档修正（spec 场景 0.6→1.0 与 YAML 对齐）                    |
| L5  | multimodal-ingest spec:29-31 "切换 ASR_PROVIDER=whisper 后上传 WAV → 转写正常" → 代码 [whisper_provider.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/asr_providers/whisper_provider.py#L11-L17) `raise NotImplementedError("scheduled for V2.x")`（design.md:40 已注明仅接口签名）                                                                                 | 低       | V3 迭代修复（实现 Whisper Provider；spec 场景标注"V3 待实现"）      |
| L6  | document-ingest spec:4 白名单 `PNG/JPG/MP3/WAV/M4A` → 代码 [parsers/**init**.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/parsers/__init__.py#L12) 额外含 `.jpeg`（超集，行为无害）                                                                                                                                                                                         | 低       | 归档后文档修正（spec 白名单补 .jpeg）                               |
| L7  | hybrid-search spec:14 兜底 `message="知识库暂无匹配内容，请联系坐席核实"` → 代码 [search_service.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/search_service.py#L35-L37) `"知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"`                                                                                                                          | 低       | 归档后文档修正（记录差异；最终文案统一留 V3 产品决策）              |
| L8  | search-logging spec:4 "每次检索 MUST 记录 query_original/query_rewritten/intent_tag" → 缓存命中分支 [api/search.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/api/search.py#L81-L97) 未传这三字段（落库 NULL；非缓存路径 [search_service.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/search_service.py#L385-L388) 合规） | 低       | V3 迭代修复（缓存命中分支补传冗余字段）                             |

### B. 代码注释不一致（1 条）

| #   | 偏差点                                                                                                                                                                                                                                                                                                                                                                                                                                             | 影响等级 | 处理方案                                              |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------------------------------------------------- |
| L9  | [milvus_client.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/vectorstore/milvus_client.py#L5) 注释"v2 collection 由 rebuild_milvus_v2.py 创建并 **alias 切换**" → 实际无 create_alias/alter_alias/drop_alias 调用，"切换"靠 [L60-L65](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/vectorstore/milvus_client.py#L60-L65) `has_collection` 探测 + 集合名直连隐式完成 | 低       | 归档后文档修正（注释改为"collection 名探测自动切换"） |

### C. json 字段不一致（1 条）

| #   | 偏差点                                                                                                                                                                                                                    | 影响等级 | 处理方案                                                                               |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------- |
| L10 | design.md:86 字段清单含 `expired` → [es_v2_mapping.json](file:///Users/gavinyoung/my_project/my_work/ai_collection/scripts/rag/es_v2_mapping.json#L13-L27) properties 无 `expired`（es_client.py:106-116 双写亦无该字段） | 低       | 归档后文档修正（design 与 mapping 对齐）；若下游需要 expired 字段，V3 补字段 + reindex |

### D. 文档描述不一致（5 条）

| #   | 偏差点                                                                                                                                                                                                                                                                                                             | 影响等级 | 处理方案                                                                                       |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- | ---------------------------------------------------------------------------------------------- |
| L11 | design.md:84⑤ / README:81-83 "Milvus alias kb_vectors 原子切到 v2 / 回滚切回旧 collection" → 代码无 alias API 操作（rebuild_milvus_v2.py 仅打日志，切换为探测式）                                                                                                                                                  | 低       | 归档后文档修正（README/design 改为"collection 名探测自动切换"）；如需原子 alias 切换 → V3 增强 |
| L12 | design.md:91/118/149、proposal.md:38 迁移编号错位（"Alembic 0003 元数据列+seed / 0004 search_logs / 0005 清理"）→ 实际 0004=元数据+seed、0005=search_logs、0006=清理（README 与代码一致）                                                                                                                          | 低       | 归档后文档修正（design/proposal 编号右移对齐 0004/0005/0006）                                  |
| L13 | design.md:36 OCR 入参 `lang (chinese_cht+en 默认)` → 代码 [ocr_client.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/ocr_client.py#L70) `data={"lang": "ch"}`                                                                                                     | 低       | 归档后文档修正（design 改为 `lang="ch"`）                                                      |
| L14 | design.md:63 "role（小写）/ 缺省 UNKNOWN" → 代码 [row_access_service.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/services/row_access_service.py#L155-L161) `role.upper()` 归一、缺省 `"default"`（与矩阵大写键匹配；search-enhancements spec 的"大小写归一"与代码一致） | 低       | 归档后文档修正（design 改为"upper 归一 + default 缺省"）                                       |
| L15 | proposal.md:40 "POST /kb/search 请求体新增可选 session_id（str）" → 代码 [api/search.py](file:///Users/gavinyoung/my_project/my_work/ai_collection/services/rag-service/app/api/search.py#L67-L70) 仅读请求头 X-Session-Id，`req.session_id` 未引用（conversational-search spec 请求头方案与代码一致）             | 低       | V3 迭代修复（决定：实现请求体 session_id 生效或移除该字段；文档先注明"以 X-Session-Id 为准"）  |

---

## 五、V2 归档备注（供 /OPSX:Archive 使用）

1. **移交 V3（rag-multimodal-kb-v3 待实现需求）**：
   - 【B2】startup 敏感词 seed 初始化后未向 `kb.sensitive_updated` PUBLISH 事件（仅 refresh 手动触发发布）；V3 需在 `_ensure_sensitive_words_baseline` seed 分支补齐 PUBLISH，payload 复用 `{"version":int,"trace_id":str,"ts":int}` JSON 契约。
   - 【M1】ASR VAD 分片（静音裁剪 + 60s 分片转写拼接）为桩实现（透传）；【L5】Whisper Provider 为 NotImplementedError 骨架——均属 V3 迭代范围。
   - 【L8/L15】缓存命中日志冗余字段补传、请求体 session_id 字段去留，V3 决定。
2. **归档后文档修正项**：L1-L4、L6、L7、L9-L14 共 12 条 spec/design/proposal/README/注释 措辞偏差，归档后按第四节逐条修订。
3. **本说明的 15 条低危清单为按"规格↔代码"双向核验重建的完整清单**（校验报告原文在上下文压缩中不可恢复）；如与原始报告 9 条口径有出入，以人工确认为准后落盘。

---

## 六、人工确认清单（封版前置）

1. [ ] B1 修复 diff 已确认（不影响业务逻辑）
2. [ ] B2/M1 不移交代码改动，仅归档备注
3. [ ] L1-L15 低危项全部不写代码，仅文档记录
4. [ ] tasks.md 已完成标记未改动；未执行 /OPSX:Archive
5. [ ] 确认后将本说明落盘，并在归档时写入备注
