# RAG Search Enhancements Specification

## Purpose

在 V1 混合检索的 RRF→Rerank 阶段叠加三项增强：业务加权（按文档元数据放大分值）、行级权限（按 JWT role/dept 过滤类别）、同 doc_id 多 chunk 只保留最高分（对齐 step4/02 规范）；前两项带独立开关默认关闭=V1 行为，去重为强制规范对齐不设开关。

## Requirements

### Requirement: 业务加权打分
当 `KB_WEIGHTING_ENABLED=true` 时，系统 SHALL 在 RRF 融合后、Rerank 之前，对每个候选 chunk 读取其关联文档的元数据（kb_documents.customer_id、agent_id、call_date、risk_tag），并按权重规则计算权重系数：新通话（call_date 近 7 天）×1.5、高风险（risk_tag="high"）×1.3，其他默认 ×1.0。RRF score 乘以权重系数后再送入 Rerank。缺省元数据为 null 时 ×1.0（不惩罚旧数据，保障兼容）。

#### Scenario: 新通话+高风险双重加权
- **WHEN** 一个 chunk 归属文档 call_date=2026-08-20（近 7 天）、risk_tag="high"，KB_WEIGHTING_ENABLED=true
- **THEN** 该 chunk 的 RRF score ×1.5 ×1.3 = ×1.95 后送入 Rerank，在候选集中排名明显上升

#### Scenario: 老文档元数据为空
- **WHEN** 一个 chunk 归属文档 call_date=null、risk_tag=null（存量 V1 文档未回填）
- **THEN** 权重系数 1.0，与 V1 原排序完全一致，不破坏既有检索顺序

#### Scenario: 开关关闭 bypass
- **WHEN** KB_WEIGHTING_ENABLED=false（默认）
- **THEN** 完全不读取文档元数据，RRF 分数原样送入 Rerank，行为=V1

### Requirement: 行级权限过滤
当 `KB_ROW_LEVEL_ENABLED=true` 时，系统 SHALL 在 Rerank 之后、截取 top_k 之前，从请求头 JWT Bearer Token 的 payload 提取 `role`（字符串，大小写归一）和 `department`（可选）claim 字段，映射到 YAML 配置的 category 白名单矩阵 `(role, dept) → 允许的 category 集合`，只保留命中白名单的 chunk。
- **JWT claim 来源约束**：department claim 由认证/签发系统（如 SSO、JWT issuer）登录时写入 payload；本 V2 **不修改 users 关系表**（step4/01 DDL 无 department 列）。若 JWT 缺失 department 或缺省为 "UNKNOWN"，则矩阵 lookup 使用 `(role, default)`，若仍无匹配则退到 `(default, default)` 保底（默认放行全部 4 类 = V1 兼容行为）。
- **Tenant_id 预留分支 bypass**：JWT 可选携带 `tenant_id` claim，矩阵支持 `(tenant_id, role, dept)` 三元组；当 JWT 无 tenant_id 或矩阵未配置任何三元组键时，tenant_id 分支整体 **bypass**（不将单组织部署误过滤为空）。
- **结果为空不告警不抛 403**：过滤后结果为空，由下游兜底闸门接管（与 V1 行级前无差别）；接口层角色粗权限由 require_role.py 独立处理，两者职责分离。
开关关闭时完全不解析 JWT 的 role/department 字段（与 V1 一致，只按请求参数 category 过滤）。

#### Scenario: 合规部只读制度类（department claim 正常签发）
- **WHEN** 用户 JWT payload 含 `role="compliance_officer"` `department="合规部"`，矩阵映射 `(compliance_officer, 合规部) → {regulation}`，KB_ROW_LEVEL_ENABLED=true，融合后重排结果包含 regulation/script/case 三类 chunk
- **THEN** 返回结果仅保留 regulation 类 chunk，script/case 类被过滤，top_k 不足则后续双闸门兜底策略接管

#### Scenario: 坐席无 department claim → 走 default→全量（V1 兼容）
- **WHEN** 用户 JWT payload 仅有 `role="agent"`，无 department claim（老版签发系统未升级），矩阵 `(agent, default)` 无匹配 → fallback `(default, default) → {regulation, script, case, other}`
- **THEN** 返回结果不被行级权限过滤，等同于 V1 行为（只按请求参数 category 过滤）

#### Scenario: 开关关闭 bypass
- **WHEN** KB_ROW_LEVEL_ENABLED=false（默认）
- **THEN** 不解析 JWT 的 role/department 字段，只按请求 category 参数过滤，行为与 V1 完全一致

### Requirement: 同 doc_id 多 chunk 只保留最高分（纯规范对齐引用，见 hybrid-search Modified RRF 融合）
系统 MUST 在 RRF 融合去重之后、送入 Rerank 之前，按 doc_id 分组，对每个 doc_id 仅保留 RRF score 最高的单个 chunk，其余同 doc_id chunk MUST 从候选集中剔除（本逻辑与 hybrid-search spec Modified Requirement 强制收录一致，对齐 step4/02 §四.2，无独立开关）。

#### Scenario: 同一制度文档 3 个 chunk，仅最高分入 Rerank
- **WHEN** RRF 融合后，文档 doc_id="DOC001" 产出 3 个 chunk，RRF score 分别为 0.78 / 0.52 / 0.41，另 3 个 chunk 归属其他 doc_id
- **THEN** 仅 doc_id=DOC001 且 score=0.78 的 chunk 被保留进入 Rerank，其余 2 个同文档 chunk 被剔除；其他 doc_id chunk 不受影响

#### Scenario: 单 chunk 文档不做剔除（向后兼容）
- **WHEN** RRF 融合后候选集 50 条 chunk，每条 doc_id 均唯一（典型长文档策略=每 doc 仅一段）
- **THEN** 去重后数量仍为 50，排序与 RRF 输出完全一致，与 V1 行为等价

#### Scenario: Rerank 输出再受行级权限过滤，同 doc 不二次去重
- **WHEN** 同 doc_id 去重完成 → 送入 Rerank → 行级权限过滤后结果中意外出现 2 条同 doc_id（如未来模块扩展引入）
- **THEN** 去重阶段仅在 RRF→Rerank 之间执行一次；Rerank 后重复由下游 top_k 截断自然处理，接口契约不承诺 Rerank 后 doc_id 唯一（与 hybrid-search 交叉引用保持一致）
