## Purpose

定义投诉服务集成测试：图全路径、扇出、reject_rejudge、11 降级（服务层直调）、PG+Redis 可切换。

## Requirements

### Requirement: 图全路径集成测试

系统 SHALL 在 `tests/integration/test_graph_flow.py` 覆盖：
1. 1 轮通过
2. 2 轮反思后通过
3. 3 轮超限转人工（loop≥3 → pending_review，exception 含 A5004 或节点等价标记）
4. MCP 故障时节点写入 exception_info（节点既有 try/except；**不要求**调用 DegradeMCP）
5. Token 超限转人工（若图状态可注入；否则与流程降级直调互补）

#### Scenario: 3 轮超限转人工

- **WHEN** 图执行 loop_count 达到 3
- **THEN** task_status=pending_review

### Requirement: 扇出与驳回重判

系统 SHALL 覆盖 judgment_fanout、evidence_re_gather；人工 **`reject_rejudge`** 后路由回 evidence_re_gather。

#### Scenario: reject_rejudge 回 evidence_re_gather

- **WHEN** human_review 提交 `ReviewAction.reject_rejudge`
- **THEN** 图路由到 evidence_re_gather 后重判

### Requirement: 11 降级场景（服务层直调）

系统 SHALL 在 `test_degrade.py` **直接调用** `DegradeLLM`（3）+ `DegradeMCP`（4）+ `DegradeFlow`（4），断言 exception_info 错误码与 pending_review 等行为。  
SHALL NOT 假设本批已将 degrade_* 挂入 graph 节点。

#### Scenario: 全 11 场景错误码断言

- **WHEN** 依次直调 11 个 handle_*
- **THEN** 码集合覆盖 A5002/A5004/A5005/A5006/A5008/A5010/A5013；权限拒绝无业务码仅审计

### Requirement: PG+Redis 可切换

系统 SHALL 支持通过环境变量 `USE_REAL_INFRA` 切换真实 PG+Redis 与 FakeRedis+Mock PG，CI 默认使用 FakeRedis+Mock。

#### Scenario: CI 默认无外部依赖

- **WHEN** 未设置 USE_REAL_INFRA=1
- **THEN** 使用 FakeRedis + Mock 通过
