## ADDED Requirements

### Requirement: MCP 返回二次脱敏校验

系统 SHALL 在现有 `app/mcp/client.py` 返回路径上**补全** PII 二次脱敏能力（已有 `mask_text` 调用则保留，**禁止整段重写 Client**）：
- 复用 `common.security.mask.mask_text`
- 检测到明文 PII 被改写时：告警日志 + `common.security.audit.log_action`
- 确保进入 Agent 的是脱敏后文本

#### Scenario: MCP 返回含明文手机号

- **WHEN** Mock MCP 返回含 `13800138000` 的文本
- **THEN** `MCPClient.call` 返回值已脱敏，且产生审计记录（可 mock 断言）

### Requirement: Agent/LLM 落库前脱敏

系统 SHALL 提供 `mask_before_persist(text) -> str`（复用 `mask_text`），并在 provider / 节点日志关键路径调用：
- 原始明文不落库（本批可不接真 PG，但调用点可测）

#### Scenario: 落库前脱敏

- **WHEN** 待持久化文本含身份证号
- **THEN** `mask_before_persist` 输出为脱敏文本

### Requirement: 明文 PII 访问 RBAC 与审计

系统 SHALL 在 `app/service/audit_service.py` 实现（复用 `common.security.rbac` + `audit.log_action`）：
- 权限矩阵：终审运营可看明文；研发禁生产明文；运维仅脱敏日志；admin 解密 CoT 需双人授权
- 每次明文访问写审计

#### Scenario: 无权限访问明文

- **WHEN** 研发角色请求明文
- **THEN** 拒绝并记审计

### Requirement: L4 后置合规校验（红线范围见 design D8）

系统 SHALL 在 `app/service/guardrails_postcheck.py` 实现后置校验，至少覆盖 design D8 标注为「实现」的条目：
1. verdict 四值（无 normal）
2. regulation_refs / rag_source_ids ⊆ RAG doc_id
3. 答辩幻觉（金额/天数/政策未命中→替换「以坐席核实为准」）——对齐红线4/7
4. AC 红线（暴力/威胁/伪造身份）——对齐红线1/2
- 红线3（停止致电）本批字段/挂钩预留；红线5 部分（doc_id）；红线6 本批落实后置侧
- 失败重试≤3 → RuleBased 兜底

#### Scenario: verdict 非法值拦截

- **WHEN** 使用 `VerdictDraft.model_construct(verdict="normal", confidence=0.9)`（绕过 Enum 构造）送入 postcheck
- **THEN** 校验失败（`ok is False`）

#### Scenario: 红线 AC 命中

- **WHEN** 答辩文本含威胁性语句且 AC 命中
- **THEN** 拦截并替换安全兜底文本
