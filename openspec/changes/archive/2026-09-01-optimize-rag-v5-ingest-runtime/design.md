## Context

See proposal.md Why / What Changes，以及 delta specs。当前同步入库在正文侧已用批量 embedding 与批量双写；资产侧对每个表格行 / 每张图 OCR 文本走单条索引往返。图/表抽取入口用「任一开关开启」短路，抽取实现不按类型过滤。DOCX 曾只走嵌入式图，工作区已补 `word/media` 扫描（Apply 对齐、禁止回退）。parse 已有开始/结束耗时日志，正文索引与资产索引缺少同结构阶段日志。Gradio 上传读超时文案已在工作区拆分，本变更以 spec 为准核对。

冒烟（含图+多表 DOCX）暴露：`process_assets` 在单张 OCR 失败的 except 中对共享 Session 调用全量 `rollback()`，导致同 Session 内尚未可被 FK 稳定引用的 `kb_documents` 行被滚掉，随后批量插入表格 `kb_chunks` 触发 `kb_chunks_doc_id_fkey`，整单 B0007。本变更须在决策 2 的批量路径上补齐 Session 边界。

约束：不改 `POST /kb/documents` 同步语义；不改表结构；检索仍「一行一 chunk」；不起 OCR 服务作为前置；不回退 Gradio ReadTimeout 文案。

## Goals / Non-Goals

**Goals:**
- 抽取与落库按图片/表格开关分别过滤，避免只开图仍产表资产。
- 同一文档资产阶段一次批量 embedding + 批量双写，去掉按行完整索引 RPC。
- 单资产失败仅 skip + warning，禁止共享 Session 全量 rollback；文档行在资产阶段前可被 FK 引用。
- DOCX 浮动/粘贴图与嵌入图一并抽出并去重。
- ingest 对 parse / 正文索引 / 资产索引打可关联 doc_id 的结构化耗时。
- Gradio 连接失败与读超时提示与 spec 一致（不改等待秒数）。

**Non-Goals:**
- 异步 202、端到端 SLA、扩大 `KB_PARSE_TIMEOUT` 覆盖范围。
- 批量 OCR、改检索粒度、新表/新环境变量、强制部署 ocr-service。
- 回写或修改 `openspec/changes/archive/**`。
- 削弱批量写入失败时的 A3003 回滚契约。

## Decisions

### 决策 1：开关在抽取层按类型过滤，落库再兜一层
- **方案**：保留「两开关全关 → 不走 extract_with_assets」短路。抽取时图片逻辑只看图片开关、表格逻辑只看表格开关；`process_assets` 丢弃与开关不符的类型。
- **理由**：只在落库过滤仍会做表解析/图片缩放，浪费且容易漏记 `kb_doc_assets`。双层过滤与主 spec 独立门控一致。
- **替代**：只改 `process_assets`。实现小，但关表时仍抽表，不符合「不产生 table 资产」的验收（抽取副作用与磁盘）。不选。

### 决策 2：资产索引复用正文批量写入，OCR 仍可按张
- **方案**：先完成各资产的文件落盘与图片 OCR，收集全部待索引文本（表格每行一行、图片 OCR 各一条），再一次 `batch_embed` + 批量 Milvus/ES/`kb_chunks`。chunk_index 连续分配。单条 `_index_chunk` 不再作为热路径。
- **理由**：瓶颈是每行一次 embedding HTTP，不是「一行一 chunk」语义。正文流水线已有批量原语。OCR 仍按张调用既有 OCR 客户端（外部服务、失败跳过单张），不纳入本次批量范围。
- **替代**：按表分别 batch（一张表一次）。多表文档仍多次 embedding 往返。不如按文档资产阶段一次 batch。不作为默认。

### 决策 3：DOCX 保留 inline + 媒体包补漏，按内容去重
- **方案**：继续抽嵌入式图；对文档包媒体部件补扫浮动/粘贴图；与已抽出 blob 去重后只入一份。Apply 以工作区实现与现有单测为准，缺测补测，禁止删掉 media 补漏。
- **理由**：仅 `inline_shapes` 漏 `wp:anchor`；只扫媒体包可能与嵌入图重复。双通道 + 去重覆盖面最大、改动局部。
- **替代**：解析 OOXML 锚点树。更精确但实现重，本增量不选。

### 决策 4：阶段日志包在 ingest 三段，不改超时语义
- **方案**：parse 沿用现有开始/结束日志；在正文双写前后、`process_assets` 前后打同结构字段（阶段名、耗时、doc_id）。无资产时资产阶段记跳过或耗时 0。
- **理由**：定位「解析很快、资产很慢」无需改 API。`KB_PARSE_TIMEOUT` 仍只包文本/资产抽取等待，不含 embedding。
- **替代**：把资产索引计入解析超时。会把慢 embed 误判为 A3001，违反 proposal。不选。

### 决策 5：Gradio 只对齐 spec，不改超时与布局
- **方案**：核对连接失败 vs 读超时分支与 delta Scenario；已符合则不动秒数与 UI。
- **理由**：工作区已有 600s 与 ReadTimeout 文案；本变更契约是提示语义，不是前端重构。
- **替代**：改为异步轮询。属 P2，非本设计。

### 决策 6：单资产失败禁止共享 Session 全量 rollback；文档行在资产阶段前可 FK 引用
- **方案**：
  1. `process_assets`（或等价编排）在单张 OCR / 单表准备失败时 MUST 仅记录 warning 并 skip 该资产；MUST NOT 调用会回滚整 Session（含 `kb_documents`）的 `db.rollback()`。
  2. 进入资产批量写 `kb_chunks` / `kb_doc_assets` 之前，`kb_documents` 行 MUST 已对后续 FK 可见（推荐：正文索引成功后对本 Session 显式 `commit`，或与文档创建使用独立、已提交边界；具体以实现最小改动为准）。
  3. **保留**：资产 chunk **批量** embedding / 双写失败时，仍按既有契约回滚索引（Milvus/ES/kb_chunks/本地资产等）并 status=failed + A3003；不得因本决策削弱该路径。
- **理由**：冒烟复现路径为「OCR 不可达 → except rollback → doc 行消失 → 表格批量 insert FK 失败 → B0007」。单资产容错本意是不阻断正文与其余资产；全量 rollback 与该意图矛盾。不起 OCR 也可验收「正文 + 表格入库、图片 skip」。
- **替代**：OCR 失败时整单失败。违背既有「单张 OCR 失败不影响正文」契约。不选。仅靠「不要 rollback」而不保证 doc 已提交，在未 flush/commit 的 Session 下仍可能 FK 不稳定；故与「资产前可引用」一并要求。

## Risks / Trade-offs

- **[批量写入失败回滚范围大于单行]** → 资产阶段批量失败仍按既有 A3003 回滚索引；不把正文已成功写入改成「部分资产成功」。单张 OCR 失败继续 skip + warning，且不得误滚 doc 行。
- **[去掉危险 rollback 后脏 Session]** → Apply 时若单资产异常曾污染 Session，须用 savepoint、或仅清理失败资产副作用、或确保异常路径不留下待 flush 的坏对象；以单测锁住「OCR 抛错后表格 batch 仍成功」。
- **[媒体补漏抽到页眉图标等噪音图]** → 去重只去重复 blob；过小/损坏图沿用既有跳过。不在本变更做内容审核。
- **[只开图时正文仍可能含表格文字]** → 与主 spec「关表则表格文本化混入正文」一致，不是回归。
- **[工作区未提交 media/ReadTimeout 与 delta 不一致]** → Apply 以 spec 为准对齐代码与测试，禁止回退。

## Migration Plan

- 部署：重建 rag-service；若改了 Gradio 文案则重启 gradio。无需迁移库表、无需重灌存量（已错抽的表资产如需清理由运维按文档 reindex，非自动任务）。OCR 不可达环境可正常入库正文与表格。
- 回滚：恢复资产抽取过滤与批量写入前的代码并重建；契约未变，前端提示回滚不影响后端。
- 存量：开关分流只影响新入库与 reindex。

## Open Questions

无。OCR 是否纳入批量不改变 spec（写入路径批量、OCR 按张失败可跳过已写在资产容错条款中）；Session 边界由决策 6 收口。
