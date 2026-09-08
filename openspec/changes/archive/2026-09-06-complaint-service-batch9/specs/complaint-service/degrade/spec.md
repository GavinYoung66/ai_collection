## ADDED Requirements

### Requirement: LLM 降级（3 场景）

系统 SHALL 在 `app/service/degrade_llm.py` 实现 3 个 LLM 降级场景，降级标记写入 state.exception_info：
1. LLM 判责失败 → RuleBasedScriptEngine 生成最小判责（A5004）
2. 产出物生成失败 → 模板填充答辩初稿（A5010）
3. RAG 法规检索失败 → 规则兜底不引用法规（A5005）

#### Scenario: 判责失败降级

- **WHEN** LLM 判责抛异常
- **THEN** 调用 RuleBasedScriptEngine，state.exception_info 含 A5004

#### Scenario: RAG 失败兜底

- **WHEN** RAG 检索失败
- **THEN** 生成不引用法规的判责，exception_info 含 A5005

### Requirement: MCP 降级（4 场景）

系统 SHALL 在 `app/service/degrade_mcp.py` 实现 4 个 MCP 降级场景：
1. 业务工单 MCP 失败 → 证据缺失触发反思循环（**A5006**）
2. 通话质检 MCP 失败 → 通话证据缺失标注（**A5008**）
3. 数据中台回写失败 → 重试 3 次+告警不阻塞（**A5013**）
4. MCP 权限拒绝 → 终止流程+安全审计（**无业务错误码**；不占用 A5006）

#### Scenario: 回写失败重试不阻塞

- **WHEN** 数据中台回写失败 3 次
- **THEN** 告警（A5013）但工单主流程继续

#### Scenario: 权限拒绝终止

- **WHEN** MCP 返回权限拒绝
- **THEN** 流程终止并写安全审计（不返回 A5006）

### Requirement: 流程降级（4 场景）

系统 SHALL 在 `app/service/degrade_flow.py` 实现 4 个流程降级场景，每个场景写 exception_info + task_status=pending_review + 经 EventPublisher 发布 **complaint.degraded**：
1. 反思循环超限（loop_count≥3）→ A5004
2. 步骤超限（max_steps）→ A5002
3. Token 超限 → A5004
4. 全局超时（30min）→ A5010

#### Scenario: 反思超限转人工

- **WHEN** loop_count 达到 3
- **THEN** task_status=pending_review，发布 complaint.degraded

#### Scenario: 全局超时转人工

- **WHEN** 工单执行超 30min
- **THEN** task_status=pending_review，exception_info 含 A5010
