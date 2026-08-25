# RAG Hybrid Search — Delta Spec（rag-service-final-fix）

## ADDED Requirements

### Requirement: top_k 入参边界

系统 MUST 约束检索接口 `top_k` 入参范围为 1~20（闭区间）：`top_k<1` 或 `top_k>20` 时返回 B0001（参数错误，HTTP 422）。边界 [1,20] 为本版本定稿契约（对齐 DD-03 §5.2 修订后表述与代码 `models/search.py` 校验）。

#### Scenario: 合法 top_k 正常检索

- **WHEN** 用户执行检索且 top_k=10
- **THEN** 系统正常检索并返回最多 10 条结果

#### Scenario: 超出上限返回 B0001

- **WHEN** 用户执行检索且 top_k=50
- **THEN** 系统返回 B0001（参数错误），不执行检索

#### Scenario: 低于下限返回 B0001

- **WHEN** 用户执行检索且 top_k=0
- **THEN** 系统返回 B0001（参数错误），不执行检索

### Requirement: 检索入参 UUID 校验

系统 MUST 在检索入口校验 doc_id/chunk 类 UUID 入参格式：非法 UUID 返回 B0001（参数错误），不得在召回结果处理阶段抛出未捕获 ValueError 导致 HTTP 500。召回结果中出现无法解析的 UUID 时 MUST 按脏数据处理（跳过该条并记录 warning），不得中断整个检索。

#### Scenario: 非法 UUID 入参

- **WHEN** 检索请求携带非法 UUID 格式的参数
- **THEN** 系统返回 B0001（参数错误），不执行检索

#### Scenario: 召回结果含脏 UUID 不中断

- **WHEN** 存储侧返回的 chunk_id 无法解析为 UUID
- **THEN** 系统跳过该条结果并记录 warning，检索正常返回其余结果，不抛 500
