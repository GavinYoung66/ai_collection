# RAG Search Enhancements Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

### Requirement: 行级权限过滤

当 `KB_ROW_LEVEL_ENABLED=true` 时，系统 SHALL 在 Rerank 之后、截取 top_k 之前，从请求头 JWT Bearer Token 的 payload 提取 `role`（字符串，大小写归一）和 `department`（可选）claim 字段，映射到 YAML 配置的 category 白名单矩阵 `(role, dept) → 允许的 category 集合`，只保留命中白名单的 chunk。

- **JWT claim 来源约束**：department claim 由认证/签发系统（如 SSO、JWT issuer）登录时写入 payload；本 V3 **不修改 users 关系表**（step4/01 DDL 无 department 列）。若 JWT 缺失 department 或缺省为 "UNKNOWN"，则矩阵 lookup 使用 `(role, default)`，若仍无匹配则退到 `(default, default)` 保底（默认放行全部 4 类 = V1 兼容行为）。
- **Tenant_id 预留分支 bypass**：JWT 可选携带 `tenant_id` claim，矩阵支持 `(tenant_id, role, dept)` 三元组；当 JWT 无 tenant_id 或矩阵未配置任何三元组键时，tenant_id 分支整体 **bypass**（不将单组织部署误过滤为空）。
- **权限过滤异常 MUST fail-closed**：row_access_service.filter 内部抛异常（配置缺失、矩阵解析失败、JWT 解析异常等）时，系统 **不得静默跳过过滤**（禁止 fail-open 返回越权数据），MUST 按保守策略处理：① 记录 error 级日志（含 trace_id、异常摘要）；② 降级为 `(default, default)` 矩阵（默认放行全部 4 类，与 V1 兼容）；③ 若连保底矩阵也不可用（如配置整体损坏），则该请求返回空 results 走下游兜底闸门（不返回越权数据）。此行为变更消除 V2 遗留的"权限过滤异常静默放行越权风险"（fail-open）。
- **结果为空不告警不抛 403**：过滤后结果为空，由下游兜底闸门接管（与 V1 行级前无差别）；接口层角色粗权限由 require_role.py 独立处理，两者职责分离。
  开关关闭时完全不解析 JWT 的 role/department 字段（与 V1 一致，只按请求参数 category 过滤）。

#### Scenario: 合规部只读制度类（department claim 正常签发）

- **WHEN** 用户 JWT payload 含 `role="compliance_officer"` `department="合规部"`，矩阵映射 `(compliance_officer, 合规部) → {regulation}`，KB_ROW_LEVEL_ENABLED=true，融合后重排结果包含 regulation/script/case 三类 chunk
- **THEN** 返回结果仅保留 regulation 类 chunk，script/case 类被过滤，top_k 不足则后续双闸门兜底策略接管

#### Scenario: 坐席无 department claim → 走 default→全量（V1 兼容）

- **WHEN** 用户 JWT payload 仅有 `role="agent"`，无 department claim（老版签发系统未升级），矩阵 `(agent, default)` 无匹配 → fallback `(default, default) → {regulation, script, case, other}`
- **THEN** 返回结果不被行级权限过滤，等同于 V1 行为（只按请求参数 category 过滤）

#### Scenario: 权限矩阵配置损坏时 fail-closed 保底

- **WHEN** `KB_ROW_LEVEL_ENABLED=true` 且权限矩阵 YAML 配置加载失败（文件损坏/字段缺失），row_access_service.filter 抛异常
- **THEN** 系统记录 error 日志（含 trace_id），不静默放行；降级使用 `(default, default)` 全量矩阵，若保底矩阵可用则正常返回 4 类结果（与 V1 等价），若保底不可用则返回空 results 走兜底闸门，绝不返回越权数据

#### Scenario: 开关关闭 bypass

- **WHEN** KB_ROW_LEVEL_ENABLED=false（默认）
- **THEN** 不解析 JWT 的 role/department 字段，只按请求 category 参数过滤，行为与 V1 完全一致
