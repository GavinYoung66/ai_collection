# RAG Smart Retrieval Pipeline Specification

## Purpose

在 V1 混合检索之前插入智能检索管道：对用户 Query 执行 LLM 改写（纠错/术语归一/扩展），并按意图规则分类动态调整召回类别权重；所有增强以独立开关接入，关闭时检索行为与 V1 完全一致，保障可回退与灰度。

## Requirements

### Requirement: Query 改写链（LLM 纠错/术语归一/扩展）
当 `KB_QUERY_REWRITE_ENABLED=true` 时，系统 SHALL 在 embed 前调用 DashScope LLM 对 query 执行改写：返回 `rewritten_query`（str，可与原 query 相同）和 `confidence`（float，[0,1]）；**当 confidence<0.6 时降级使用原 query**，标 `degraded=true`，不把低置信度改写结果送入召回。改写超时 10s、3 次重试、熔断 `rag:rewrite:degraded`（TTL 60s）。

#### Scenario: 改写成功且置信度高
- **WHEN** 用户查询"逾其协商"（别字），KB_QUERY_REWRITE_ENABLED=true，LLM 返回 rewritten_query="逾期协商还款" confidence=0.92
- **THEN** Milvus+ES 双路召回均使用改写后 query 执行 embed/BM25，检索结果命中对应 regulation 类文档，响应 degraded=false，kb_search_logs.query_rewritten 字段记录改写后 query

#### Scenario: 低置信度降级原 query
- **WHEN** 用户查询"请问最近有什么活动"，LLM 返回 rewritten_query="催收话术参考" confidence=0.4
- **THEN** 系统丢弃改写结果，使用原 query 走 V1 混合检索流程，响应 degraded=true，kb_search_logs.query_rewritten=null 或空

#### Scenario: LLM 服务熔断时 bypass
- **WHEN** LLM 连续 3 次失败后 rag:rewrite:degraded=1
- **THEN** 所有检索直接 bypass 改写，使用原 query 检索，degraded=true（不抛异常，不阻断主流程）

#### Scenario: 开关关闭时行为与 V1 一致
- **WHEN** KB_QUERY_REWRITE_ENABLED=false（默认值），用户执行任何 query
- **THEN** 检索流程完全不引入 LLM 调用，代码执行路径等同于 V1（含日志、缓存、降级矩阵全部 1:1）

### Requirement: 动态路由（意图规则版 + 权重配置）
当 `KB_ROUTING_ENABLED=true` 时，系统 SHALL 在召回前执行规则意图分类（关键词匹配表从 YAML 配置读取，热加载），命中意图后对 category 的召回权重进行调整（例如 "negotiate" 意图 → regulation×1.8 script×1.2，其余类 category×0.6）；调整在 RRF 融合前的 rank 分数上施加，不修改原始召回条数 top20。未命中意图时 bypass，行为与 V1 完全一致。

#### Scenario: 命中"协商还款"意图
- **WHEN** 用户 query="逾期协商还款"命中路由意图表 negotiate，KB_ROUTING_ENABLED=true
- **THEN** BM25 和 Milvus 的 category=regulation 得分 ×1.8、script ×1.2、case ×0.6 重算 rank，再进入 RRF 融合；检索排序优先展示制度类文档

#### Scenario: 未命中意图 bypass
- **WHEN** 用户 query="怎么开始"不匹配任何路由意图表关键词
- **THEN** 检索流程与 V1 完全一致（4 个 category 等权召回），响应 degraded=false

#### Scenario: 路由配置文件为空
- **WHEN** 管理员删除了路由 YAML 配置文件并重启服务，KB_ROUTING_ENABLED=true
- **THEN** 系统在启动时加载空配置，所有检索均 bypass 路由步骤（等同于 V1），不抛异常，degraded=true
