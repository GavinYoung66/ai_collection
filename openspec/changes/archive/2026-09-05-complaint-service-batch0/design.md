# Design: complaint-service-batch0

## 0. 路径与范围

- **服务根**：`services/complaint-service/`
- 下文相对路径均相对服务根；绝对仓库路径 = `services/complaint-service/` + 相对路径
- 本批不写业务 API / LangGraph / ORM；不改 `common` 源码

## 1. Mock 目录布局

```
services/complaint-service/tests/mock/
├── __init__.py
├── llm/
│   ├── __init__.py
│   ├── mock_provider.py
│   └── fixtures/
│       ├── evidence_case_normal.json
│       ├── evidence_case_multi.json
│       ├── evidence_call_qc_normal.json
│       ├── evidence_call_qc_corrupted.json
│       ├── reflection_sufficient.json
│       ├── reflection_insufficient.json
│       ├── fact_timeline_normal.json
│       ├── fact_timeline_conflict.json
│       ├── compliance_violation.json
│       ├── compliance_compliant.json
│       ├── user_demand_refund.json
│       ├── user_demand_emotional.json
│       ├── verdict_agent_violation.json
│       ├── verdict_user_misunderstanding.json
│       ├── verdict_compliant_but_poor_experience.json
│       ├── verdict_pending.json
│       ├── generate_package_complete.json
│       └── generate_package_pending.json
├── mcp/
│   ├── __init__.py
│   ├── mock_client.py
│   └── fixtures/          # 每 tool 至少一个 normal fixture；场景覆盖见 §2
│       └── {server}_{tool}.json 等
└── data/
    ├── __init__.py
    ├── seed_complaints.py
    ├── seed_cases.py
    └── seed_asr_samples.py
```

（`conftest.py` 可选；本批不强制。需要时在后续测试批次补。）

## 2. Fixture 命名规则

```
{server}_{tool}[_{scenario}].json
```

- server：`business_ticket` / `call_qc` / `rag_kb` / `data_platform`
- tool：与 PRD §4.4 工具名一致
- scenario（可选）：`normal` / `empty` / `error` / `multi_case` / `repeat` / `corrupted`

LLM fixture：

```
{agent_role}_{scenario}.json
```

## 3. Prompt YAML 约定

### 目录

```
services/complaint-service/app/prompt/templates/
├── evidence_case_v1.yaml
├── evidence_call_qc_v1.yaml
├── complaint_reflection.yaml
├── complaint_fact_timeline.yaml
├── complaint_compliance.yaml
├── complaint_user_demand.yaml
├── complaint_verdict.yaml
└── complaint_generate_package.yaml
```

### YAML 结构（对齐 PRD 8.1 七段 + 8.4 元数据）

```yaml
prompt_id: <string>          # 与文件名去 .yaml 一致（含 _v1 的保留，如 evidence_case_v1）
version: "1.0.0"
target_model: qwen-turbo | deepseek-r1 | qwen-plus
scene: complaint_judgment/<role>
change_log: []

guardrails:
  require_json_output: true
  require_rag_source: <bool>   # PRD 字段名；P2.5/P2.8 为 true（本批不用 require_source_id 别名）
  max_tokens: 4096
  temperature: 0.1
  max_response_length: <int>   # 仅 P2.8 = 800

sections:
  role: ...
  task: ...
  input_constraint: ...
  output_schema: ...           # Pydantic 类名字符串
  prohibitions: [...]
  fallback: ...
  enum_constraints: [...]
  few_shot: ...                # 附加：不占用七段槽位；清单要求的示例放这里
```

### target_model 路由（与 PRD 8.5 一致）

| Agent | target_model |
|---|---|
| evidence_case / evidence_call_qc / reflection / fact / user_demand | qwen-turbo |
| compliance / verdict | deepseek-r1 |
| generate_package | qwen-plus |

### 本批验收（无 load_prompt）

`load_prompt()` 属批次4（6.1）。本批仅：

```python
import yaml
from pathlib import Path
root = Path("services/complaint-service")
data = yaml.safe_load((root / "app/prompt/templates/complaint_reflection.yaml").read_text())
assert data["prompt_id"] == "complaint_reflection"
assert len(data["sections"]) >= 7
assert "few_shot" in data["sections"] or data["sections"].get("few_shot") is not None
```

## 4. Mock 与 common.llm 对齐方式（决策）

### 背景

现网 `common/llm/provider.py` 为函数式 `chat` / `chat_json`，**不存在** `BaseLLMProvider` ABC。清单写「继承 BaseLLMProvider」与现状冲突。

### 决策（本批）

1. **不在 common 新增 ABC**（避免越权改 M0）。
2. `MockLLMProvider` 采用**鸭子类型**：实现与 PRD/任务清单约定一致的  
   `async def invoke(prompt, schema, trace_id, session_id) -> BaseModel`，以及 `set_delay` / `set_error_mode` / `set_seed`。
3. 若 import 时发现未来已有 `BaseLLMProvider`，可再 `issubclass` 对齐；**本批验收不要求继承**。
4. 批次3（5.1）定稿真实 Provider 后，再统一抽象；允许微调 Mock 签名。

### MockLLMProvider 草图

```python
# services/complaint-service/tests/mock/llm/mock_provider.py

class MockLLMProvider:
    """契约对齐 PRD invoke；不强制继承 common（当前无基类）。"""

    async def invoke(self, prompt: str, schema: type, trace_id: str, session_id: str):
        ...
```

### MockMCPClient

```python
# services/complaint-service/tests/mock/mcp/mock_client.py

class MockMCPClient:
    async def call(self, server: str, tool: str, params: dict) -> dict:
        ...
    def set_mode(self, tool: str, mode: str): ...  # success|timeout|error
```

异常：`MCPTimeoutError` / `MCPError` 可定义在 `tests/mock/mcp/errors.py` 或后续迁到 `app/mcp/errors.py`（批次3）；本批放 tests 侧即可。

### 14 tool 权威来源

以 **PRD §4.4** 为准（清单 P1.2 硬约束漏列 4 个，本批不跟漏列）：

| Server | Tools |
|---|---|
| business-ticket | `query_case_info`, `query_history_tickets`, `query_history_complaints`, `query_user_history_complaints`, `get_complaint_ticket` |
| call-qc | `query_call_asr`, `query_qc_violations`, `query_call_sessions` |
| rag-kb | `search_regulations`, `search_response_templates`, `search_complaint_handling_guidelines` |
| data-platform-writeback | `write_session`, `write_human_review_truth`, `push_rag_candidate` |

合计 **14**。

## 5. P1.4 延后联调决策

| 验收项 | 本批次 | 批次7 |
|---|---|---|
| 脚本文件存在于服务根 `scripts/mock/` | ✅ | — |
| 参数 `--count` / `--delay` / `--mode` | ✅ | — |
| `push_complaint.sh` 可执行 + batch `--help` | ✅ | — |
| curl POST / GET status 成功 | ❌ | ✅ |

默认 base URL：`http://localhost:8006/api/v1`。

## 6. 种子数据设计

| # | 场景 | verdict |
|---|---|---|
| 1 | 坐席违规 | agent_violation |
| 2 | 用户误解 | user_misunderstanding |
| 3 | 合规体验差 | compliant_but_poor_experience |
| 4 | 多案件合并（3+） | agent_violation |
| 5 | 重复投诉（90 天同 user_id） | user_misunderstanding |
| 6 | ASR 残缺 | pending |
| 7 | 证据冲突 | pending |
| 8 | 法规引用（RAG 3+） | agent_violation |

每条含初始 State 快照字段：`complaint_id` / `user_id` / `case_ids` / `complaint_text` / `submit_time` / `source_channel` + 预期 verdict 标签。
