# complaint-service/mock-deps

## Purpose

为 complaint-service 提供脱网可测的 Mock LLM、Mock MCP、种子数据与推送脚本骨架。交付路径均在 `services/complaint-service/` 下。

## Requirements

### Requirement: Mock LLM Provider 鸭子类型契约

系统 SHALL 在 `services/complaint-service/tests/mock/llm/mock_provider.py` 提供 `MockLLMProvider`，实现：

```
async invoke(prompt: str, schema: type[BaseModel], trace_id: str, session_id: str) -> BaseModel
```

以及 `set_delay` / `set_error_mode` / `set_seed`。Mock MUST NOT 因继承不存在的 `common.llm.provider.BaseLLMProvider` 而修改 `common/`；本批采用鸭子类型，批次3 Provider 定稿后再对齐。

#### Scenario: 可实例化且不依赖不存在的基类

- **WHEN** 开发者在服务根执行 `from tests.mock.llm.mock_provider import MockLLMProvider; MockLLMProvider()`
- **THEN** 实例化成功，且未要求存在 `BaseLLMProvider` ABC

#### Scenario: 按 Agent 返回预设 fixture

- **WHEN** 调用 `invoke` 且 prompt/上下文可识别 agent 角色（如 reflection）
- **THEN** 返回对应 fixture JSON 经 `schema` 校验后的模型实例（或本批在 schema 类尚未落地时以 dict 结构断言兼容路径，见 tasks）

#### Scenario: 延迟与错误注入

- **WHEN** 设置 `set_delay(2)` 或 `set_error_mode("timeout")`
- **THEN** invoke 分别延迟约 2s，或抛出可识别的超时类错误；`rate_limit` / `server_error` 同理可注入

#### Scenario: Verdict fixture 四值覆盖

- **WHEN** 检查 LLM fixtures 中含 verdict 字段的样本
- **THEN** 仅出现 `agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`，不得出现已废弃的 `normal`

### Requirement: Mock MCP Client 覆盖 PRD 14 tool

系统 SHALL 在 `tests/mock/mcp/mock_client.py` 提供 `MockMCPClient.call(server, tool, params) -> dict`，覆盖 PRD §4.4 的 4 Server × 14 tool（权威以 PRD 为准，补齐清单 P1.2 漏列的 `get_complaint_ticket`、`query_call_sessions`、`search_complaint_handling_guidelines`、`push_rag_candidate`）。每种 tool MUST 支持 success / timeout / error；timeout MUST 抛 `MCPTimeoutError`，error MUST 抛 `MCPError`。

#### Scenario: 14 个 tool 均有 fixture

- **WHEN** 枚举 PRD §4.4 全部 tool 名称
- **THEN** `tests/mock/mcp/fixtures/` 下每个 tool 至少存在一份可加载的成功响应样本

#### Scenario: 模式切换

- **WHEN** 对某 tool `set_mode(tool, "timeout")` 后调用 `call`
- **THEN** 抛出 `MCPTimeoutError`，不返回成功 payload

#### Scenario: 业务场景 fixture 可覆盖边界

- **WHEN** 测试需要多案件 / 重复投诉 / ASR 残缺 / 证据冲突 / RAG 多法规命中
- **THEN** 可通过已有 fixture 或 scenario 后缀样本获取对应数据（不必每个 tool×场景笛卡尔积）

### Requirement: 种子投诉数据

系统 SHALL 在 `tests/mock/data/` 提供不少于 8 条种子工单（`seed_complaints.py` 等），覆盖四值 verdict 与多案件、重复投诉、ASR 残缺、证据冲突、法规引用场景；每条 MUST 含 `complaint_id`、`user_id`、`case_ids`、`complaint_text`、`submit_time`、`source_channel` 及预期 verdict 标签。

#### Scenario: 种子可 import

- **WHEN** 在服务根 `from tests.mock.data.seed_complaints import SEED_COMPLAINTS`
- **THEN** `len(SEED_COMPLAINTS) >= 8` 且每条含非空 `complaint_id`

#### Scenario: 四值 verdict 均有样本

- **WHEN** 统计种子预期 verdict
- **THEN** 四个枚举值均至少出现一次

### Requirement: Mock 推送脚本骨架

系统 SHALL 在 `scripts/mock/` 提供 `push_complaint.sh` 与 `push_batch_complaints.py`。本批 MUST 验收：文件存在、可执行/`--help`、参数 `--count`/`--delay`/`--mode` 定义正确。本批 MUST NOT 将「curl POST 成功创建工单」作为完成条件（留给批次7 API 就绪后）。

#### Scenario: 脚本存在且帮助可输出

- **WHEN** 执行 `python scripts/mock/push_batch_complaints.py --help`
- **THEN** 输出包含 count/delay/mode 说明且进程退出码为成功

#### Scenario: 端到端推送不在本批强制

- **GIVEN** complaint-service API 路由尚未实现
- **WHEN** 完成本批 Mock 推送任务
- **THEN** 不以 POST `/api/v1/complaints` 返回 200 作为本批失败条件

### Requirement: V2 场景级 Mock fixtures 可区分

在既有 14 tool fixture 与种子契约之上，系统 SHALL 为 V2 至少三类业务场景提供可区分的 Mock MCP 成功响应（案件/ASR 或质检/法规或模板至少一类场景差异），供真图路径消费；`USE_MOCK_MCP` MUST 可保持为 true。

#### Scenario: 场景差异可加载

- **WHEN** 分别为违规倾向、用户误解、体验差三类场景加载 Mock 成功响应
- **THEN** 至少一类关键字段（如 ASR 片段、法规条文、模板正文）在场景间可区分且非空

#### Scenario: 不强制真 MCP

- **WHEN** V2 联调使用增强 fixtures
- **THEN** 无需配置真实业务 MCP Server 即可返回成功 payload
