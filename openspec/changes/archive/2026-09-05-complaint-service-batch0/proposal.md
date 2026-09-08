# Proposal: complaint-service-batch0

## Why

complaint-service M3 里程碑的 86 个开发任务中，批次0（前置准备）是后续 Mock 测试与 Prompt 加载的基础依赖。没有 Mock 依赖层，后续批次无法在脱网环境下做单元测试；没有 Prompt 模板正文，批次3（LLM）与批次4（PromptOps）无法加载和渲染 Prompt。

本 change 交付批次0 的全部 12 项产物，为后续批次提供可 import 的 Mock/Prompt 基线（不声称批次1 脚手架本身依赖 Mock）。

## What

交付 12 项产物，分两个能力域。

**路径硬约束**：本 change 所有交付路径均相对于仓库内 `services/complaint-service/`（下文写成该服务根下的相对路径）。禁止把文件写到仓库根的 `app/` 或 `tests/`。

### Mock 依赖层（P1.1~P1.4）

| 任务 | 产物（相对服务根） | 核心价值 |
|---|---|---|
| P1.1 Mock LLM Provider | `tests/mock/llm/mock_provider.py` + fixtures | 按 Agent 返回预设 JSON；延迟/错误注入；seed 确定性；4 种 verdict fixture |
| P1.2 Mock MCP Client | `tests/mock/mcp/mock_client.py` + fixtures | 4 Server × **PRD §4.4 共 14 tool**；success/timeout/error |
| P1.3 种子数据 | `tests/mock/data/seed_*.py` | 8 条典型工单，覆盖 4 种 verdict 与边界场景 |
| P1.4 Mock 推送脚本 | `scripts/mock/push_complaint.sh` + `push_batch_complaints.py` | 模拟业务系统推送；本批只验脚本与参数，E2E 留给批次7 |

### Prompt 模板正文（P2.1~P2.8）

8 个 YAML，覆盖 8 个 LLM Agent；遵循 PRD 第八章七段结构 + PromptOps 元数据；few-shot 以 `sections.few_shot` 附加（不挤占七段槽位）。

## 非目标

- 不做模块 1~14 任何正式业务代码（脚手架/ORM/LangGraph/API/灰度等）
- 不做真实 LLM / MCP Server 对接（批次3）
- 不在 `common` 新增 `BaseLLMProvider` ABC（见 design：Mock 鸭子类型，批次3 再对齐）
- P1.4 端到端 curl 成功留给批次7
- P1.1/P1.2 接口形状按 PRD 契约先行；批次3（3.1/5.1）定稿后可微调

## 影响面

### 文件新增（均在 `services/complaint-service/` 下）

```
tests/mock/llm/mock_provider.py
tests/mock/llm/fixtures/                 # 18 个 JSON
tests/mock/mcp/mock_client.py
tests/mock/mcp/fixtures/                 # 14 个 tool 的响应样本
tests/mock/data/seed_complaints.py
tests/mock/data/seed_cases.py
tests/mock/data/seed_asr_samples.py
scripts/mock/push_complaint.sh
scripts/mock/push_batch_complaints.py
app/prompt/templates/evidence_case_v1.yaml
app/prompt/templates/evidence_call_qc_v1.yaml
app/prompt/templates/complaint_reflection.yaml
app/prompt/templates/complaint_fact_timeline.yaml
app/prompt/templates/complaint_compliance.yaml
app/prompt/templates/complaint_user_demand.yaml
app/prompt/templates/complaint_verdict.yaml
app/prompt/templates/complaint_generate_package.yaml
```

### 依赖关系

- 上游：M0 `common`（logging/errors；LLM 现为函数式 `chat`/`chat_json`，本批不强制继承不存在的基类）
- 下游：为批次3+ 的脱网测试与 PromptOps 提供基线；批次1 脚手架不依赖本批产物

### 枚举硬约束

verdict 仅允许 `agent_violation | user_misunderstanding | compliant_but_poor_experience | pending`，禁止已废弃的 `normal`。
