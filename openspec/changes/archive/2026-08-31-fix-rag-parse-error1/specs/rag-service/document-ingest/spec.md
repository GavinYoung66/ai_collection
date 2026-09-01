## ADDED Requirements

### Requirement: Excel 解析数据读取兼容（公式单元格）

系统 SHALL 在解析 `.xlsx` 读取单元格值时兼容**公式单元格**：当以 `data_only=True`（仅读取公式缓存结果）读取某单元格为 `None` 时，不得立即判定该单元格无值而丢弃整行；MUST 对公式单元格回退读取其**公式字符串或原始值**作为可入库文本，确保含公式的表格仍能提取出有效文本。判定"整表为空"的标准 MUST 为**全部行列均无任何可提取文本**，而非"存在公式单元格导致 data_only 读到 None"。空表/全空文档仍按既有契约返回 A3002「文档分块后无有效内容」+ status=failed；含有效数据（即使含公式）的表格 MUST 正常分块入库，不返回 A3002。

本 Requirement 为对既有「Excel 表格文档解析（.xlsx）」的兼容性增强，不改变既有 sheet 遍历、表头、行切块、sheet 名前缀、空行跳过、损坏/加密 A3001 等既有行为。

#### Scenario: 含公式单元格的表格正常入库（不再 A3002）

- **GIVEN** 管理员上传一个 `.xlsx`，其中多数数据单元格为公式（如 `=RANDBETWEEN(...)`、`=VLOOKUP(...)`、`=1+1`），且该文件未被 Excel/WPS 缓存过计算结果
- **WHEN** 管理员上传该表格
- **THEN** 系统读取到可提取文本并成功分块、向量化、写入双索引，返回 doc_id + status=active，不再返回 A3002「文档分块后无有效内容」

#### Scenario: 公式单元格读到 None 时回退读取

- **GIVEN** `.xlsx` 某单元格为公式且 `data_only=True` 读到 `None`
- **WHEN** 解析该行
- **THEN** 系统按公式字符串/原始值回退读取该单元格（不判为缺失），该行其他有效列照常序列化，该行不因单列 None 被整体丢弃

#### Scenario: 真正空表仍返回 A3002

- **GIVEN** 管理员上传一个没有任何有效数据（全部行列均为空）的 `.xlsx`
- **WHEN** 解析、清洗、分块后有效 chunk 为 0
- **THEN** 系统按既有契约返回 A3002「文档分块后无有效内容」+ status=failed，不产生索引残留

#### Scenario: 既有 sheet 与表头行为不回归

- **GIVEN** 管理员上传一个含多 sheet、首行为表头的普通 `.xlsx`（无公式）
- **WHEN** 上传入库并检索命中某行
- **THEN** 行为与既有「Excel 表格文档解析（.xlsx）」一致：多 sheet 入库、行切块、sheet 名前缀冗余、来源 source=excel，无回归
