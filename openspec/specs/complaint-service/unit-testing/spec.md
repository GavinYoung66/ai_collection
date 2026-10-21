## Purpose

定义投诉服务单元测试体系：核心模块覆盖范围、Mock 规范与覆盖率门槛 ≥85%；覆盖率口径见 design D8。

## Requirements

### Requirement: 核心模块单元测试覆盖

系统 SHALL 在 `services/complaint-service/tests/unit/` 下补齐/强化：
- state reducer（状态迁移、幂等、非法迁移拒绝）
- graph edges（全路径连通、条件路由）
- fallback / recovery（checkpointer 恢复、Redis fallback、TTL）
- mcp_client（工具调用、失败、权限拒绝）
- evidence_helper（证据汇总、缺失标注、冲突规则）
- 已有测试不退化

#### Scenario: 核心覆盖率门槛达标

- **WHEN** 按 tasks 指定的 `--cov` 模块列表执行 pytest
- **THEN** 核心五类合并覆盖率 ≥85%（非整包强制 fail-under）

#### Scenario: 外部依赖全 Mock

- **WHEN** 运行任意 unit 测试
- **THEN** 不访问真实 LLM、MCP、PG、Redis、Pub-Sub

### Requirement: Mock 规范

单元测试 SHALL 复用 `tests/mock/` 与 `conftest.py`，禁止直连外部系统。

#### Scenario: Mock 可断言

- **WHEN** 测试验证 LLM/MCP 调用
- **THEN** 可断言调用次数与关键参数
