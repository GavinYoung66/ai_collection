# RAG Smart Retrieval Pipeline Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

### Requirement: 动态路由（意图规则版 + 权重配置）

当 `KB_ROUTING_ENABLED=true` 时，系统 SHALL 在召回前执行规则意图分类（关键词匹配表从 YAML 配置读取，热加载），命中意图后对 category 的召回权重进行调整；**权重取值以配置 `conf/intent_routes.yaml` 为唯一事实源**：当前实现为命中意图时 `case: 1.0`、`other: 0.6`（regulation/script 由配置其余项决定，实际以 YAML 为准），调整在 RRF 融合前的 rank 分数上施加，不修改原始召回条数 top20。未命中意图时 bypass，行为与 V1 完全一致。**权重作用范围说明（审计 D2）**：加权在 RRF 融合前通过 `WeightingService.apply` 将权重系数写入 `item["weight"]` 并放大 RRF 分数；Rerank 正常路径最终排序分 = `rerank_score × weight`（`weight` 缺省 1.0），使加权在 rerank 主路径正确生效；`max_rerank_score` 取**全局最大**原始 `rerank_score`（不乘权重），以保持闸门 2 的 0.35 兜底阈值语义不被加权排序污染。Rerank 降级（退回 RRF score 排序）路径因 `item["score"]` 已被加权放大，加权同样生效。加权关闭（`KB_WEIGHTING_ENABLED=false`）时 `weight` 缺省 1.0，排序退化为纯 `rerank_score`，与 V2 行为一致。

#### Scenario: 命中"协商还款"意图

- **WHEN** 用户 query="逾期协商还款"命中路由意图表 negotiate，KB_ROUTING_ENABLED=true，且配置为 case:1.0 / other:0.6
- **THEN** BM25 和 Milvus 的 category 权重按 YAML 配置施加（case ×1.0、other ×0.6，regulation/script 权重以配置实际值为准）重算 rank，再进入 RRF 融合；检索排序优先展示制度类文档

#### Scenario: 未命中意图 bypass

- **WHEN** 用户 query="怎么开始"不匹配任何路由意图表关键词
- **THEN** 检索流程与 V1 完全一致（4 个 category 等权召回），响应 degraded=false

#### Scenario: 路由配置文件为空

- **WHEN** 管理员删除了路由 YAML 配置文件并重启服务，KB_ROUTING_ENABLED=true
- **THEN** 系统在启动时加载空配置，所有检索均 bypass 路由步骤（等同于 V1），不抛异常，degraded=true
