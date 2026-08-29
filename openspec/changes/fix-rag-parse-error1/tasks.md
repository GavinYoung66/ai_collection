# Tasks

实现清单，按依赖顺序拆分。每个任务绑定对应 delta spec 场景，完成后可独立验证。本变更仅修正既有行为，不引入新业务逻辑、不做大范围重构。

## 1. 混合检索去重与排序优化（缺陷 1）

绑定 spec：`rag-service/hybrid-search`（「RRF 融合」内容级去重扩展 + 「检索结果排序区分度」）

- [x] 1.1 检索去重增强：在既有 RRF 融合输出去重阶段，参考 design.md 决策 1 实现「内容级去重」。先按 doc_id 去重（保留最高分），再对 content 做规范化（去空白、统一大小写/全半角标点）后比较，相同或高度相似（归一化后相等/仅格式差异）只保留排序靠前的一条。验证：`different doc_id 但内容相同 → 仅保留一条`（spec Scenario），同内容不再输出 5–6 条。
- [x] 1.2 中文重排区分度：参考 design.md 决策 2，改造 Rerank 输入侧相似度计算，覆盖中文 query/chunk，避免仅依赖 `\w+` 切词导致全候选恒等（如恒 0.8）。验证：`中文查询 rerank_score 具有区分度`（spec Scenario），不同候选分数出现差值，最终排序 ≠ 融合分原始顺序。
- [x] 1.3 排序收敛与 Top1 命中：确认最终 top_k 按具备区分度的相关性分（rerank_score 与既有 weight 组合）降序，Top1 content 包含答案关键语义上下文。验证：`Top1 命中包含答案上下文` 与 `去重后 top_k 不出现同内容多条`（spec Scenario）。
- [x] 1.4 回归约束校验：确认改动不破坏既有 RRF 融合公式、doc_id 去重、双闸门兜底（RAG_SCORE_THRESHOLD / KB_RERANK_THRESHOLD）、溯源字段与缓存契约；若涉 dev 环境 mock 重排（`deploy/rerank/mock_app.py`）同步适配以保证本地可复现。

## 2. Excel（.xlsx）解析 A3002 修复（缺陷 2）

绑定 spec：`rag-service/document-ingest`（「Excel 解析数据读取兼容（公式单元格）」）

- [x] 2.1 公式单元格回退读取：参考 design.md 决策 3，在 `.xlsx` 单元格读取时，对 `data_only=True` 读到 `None` 的公式单元格，回退用 `data_only=False` 读取公式字符串/原始值作为可入库文本，不得据此丢弃整行。验证：`含公式单元格的表格正常入库（不再 A3002）` 与 `公式单元格读到 None 时回退读取`（spec Scenario）。
- [x] 2.2 判空标准修正：将「整表为空」的判定标准明确为"全部行列均无任何可提取文本"，而非因存在公式单元格导致 data_only 读到 None。验证：`真正空表仍返回 A3002`（spec Scenario）不回归。
- [x] 2.3 既有行为防回归：确认不改变既有 sheet 遍历、首行表头、行切块、sheet 名前缀冗余、空行跳过、损坏/加密文件 A3001 等行为（`source=excel`，多 sheet 入库）。验证：`既有 sheet 与表头行为不回归`（spec Scenario）。

## 3. 音频 ASR A3001 修复（缺陷 3）

绑定 spec：`rag-service/multimodal-ingest`（「ASR Key 可用性判定与错误定位」）

- [x] 3.1 ASR Key 可用性判定：参考 design.md 决策 4，DashScope ASR Provider 在转写前读取 `DASHSCOPE_API_KEY`（含等价千问 DashScope Key），非空即视为可用并正常调用转写，不得抛「DASHSCOPE_API_KEY not configured」；仅当确认无可用 Key 才返回 A3001。验证：`已配置 DashScope Key 时 mp3 转写成功（不再 A3001)`（spec Scenario）。
- [x] 3.2 错误信息可定位：当确无可用 Key 抛 A3001 时，错误信息明确指明缺失的配置项（如 `DASHSCOPE_API_KEY`），不模糊为笼统文档解析失败。验证：`未配置任何可用 Key 时返回可定位的 A3001`（spec Scenario）。
- [x] 3.3 降级与切换兼容：确认 Key 缺失行为遵循既有「OCR/ASR 解析失败降级」契约（`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时占位入库、degraded=true，默认关闭时返回 A3001 + status=failed）；确认 `ASR_PROVIDER=whisper` 且模型可用时转写正常。验证：`降级开关开启时 Key 缺失走占位入库` 与 `切换 Whisper 且模型可用时转写正常`（spec Scenario）。
- [x] 3.4 配置校准：在 apply 阶段按容器与 `.env` 实际环境校准 Key 变量名（如 `DASHSCOPE_API_KEY` vs `QWEN_API_KEY`），必要时在 `.env` 统一映射，避免命名不一致导致的误判（对应 design.md Open Questions）。
