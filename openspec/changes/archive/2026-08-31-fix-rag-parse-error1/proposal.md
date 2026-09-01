## Why

RAG-Service V5 当前存在三处会影响用户可用性的缺陷：(1) 混合检索结果精度差，同一内容重复出现 5–6 条，无法准确定位命中答案；(2) 上传 `.xlsx` 表格文件稳定抛出 A3002「文档分块后无有效内容」；(3) 上传音频（`.mp3` 等）稳定抛出 A3001「文档解析失败: DASHSCOPE_API_KEY not configured」，即使环境中已配置了可用的阿里千问 / DeepSeek Key。这些缺陷已破坏「上传 → 入库 → 检索」的核心链路，需要修复并固化回归验收标准。

## What Changes

- **混合检索去重与排序优化**：修复「同一内容重复出现 5–6 条」问题。将去重逻辑从「仅按 doc_id」增强为「按内容语义去重」（内容相同/高度相似时只保留一条），并修复重排模型对中文查询无区分度（rerank_score 恒为 0.8）导致排序退化为融合分原始顺序的问题。保留既有 RRF 融合、doc_id 去重与双闸门兜底契约。
- **`.xlsx` 解析 A3002 修复**：修复 Excel 解析器在 `data_only=True` 下将「公式单元格」读成 `None`、进而将整表判定为空、分块数归零的问题；确保含公式单元格的表格仍能提取出有效文本并正常分块入库。
- **音频 ASR A3001 修复**：修复 ASR 在已配置可用 Key 时仍因 `DASHSCOPE_API_KEY` 判空而抛错的问题；确保配置了有效 DashScope Key 时音频能成功转写入库，仅在确实未配置任何可用 ASR Key 时才返回 A3001（且错误信息可定位）。
- 以上均以修正既有行为为目标，**不引入新业务逻辑、不做大范围重构**。

## Capabilities

### New Capabilities

无。（本变更仅修正既有能力的行为缺陷，不新增能力。）

### Modified Capabilities

- `rag-service/hybrid-search`: 检索结果去重由「仅按 doc_id」增强为「内容语义去重」，并修复重排对中文查询无区分度导致的排序退化；RRF 融合、doc_id 去重、双闸门兜底、溯源字段等既有契约不变。
- `rag-service/document-ingest`: Excel（`.xlsx`）解析在 `data_only=True` 读到公式单元格为 `None` 时能回退/兼容，避免「整表被判空 → 分块数归零 → A3002」。
- `rag-service/multimodal-ingest`: 音频 ASR 在已配置可用 DashScope Key 时正常转写入库，不再误报「DASHSCOPE_API_KEY not configured」；仅在确实无可用 Key 时才返回 A3001。

## Impact

- **代码**：`services/rag-service/app/services/search_service.py`（去重/排序）；`services/rag-service/app/parsers/xlsx_parser.py`（Excel 解析）；`services/rag-service/app/services/asr_providers/dashscope_provider.py`（ASR Key 判空）；可能涉及 `services/rag-service/app/services/asr_client.py`、`deploy/rerank/mock_app.py`（中文重排区分度）。
- **配置**：`.env` 中 `DASHSCOPE_API_KEY`、`ASR_PROVIDER`、`KB_ASR_*`、`KB_CHUNK_*` 等；不新增新配置项（复用既有开关，避免引入新逻辑）。
- **接口契约**：`POST /kb/documents`、`POST /kb/search` 的入参/出参、错误码（A3001/A3002）语义不变；受影响的是「成功/失败」行为本身与检索结果去重效果。
- **兼容性**：主库 `openspec/specs/**` 与既有归档变更（V1–V5）不修改；本变更仅新增 delta spec。
