# Tasks: complaint-service-batch0

> **路径硬约束**：所有文件路径相对于 `services/complaint-service/`。禁止写到仓库根 `app/` / `tests/` / `scripts/`。
>
> 拆为两轮 Apply：轮次 A（Prompt）→ 轮次 B（Mock）。同一 change，分两次 `/opsx-apply`。

---

## 轮次 A：Prompt 模板正文（P2.1 ~ P2.8）

> 目标：8 个 YAML 可被 `yaml.safe_load()` 解析；含 PRD 八章七段 + `sections.few_shot`；`guardrails.require_rag_source` 用 PRD 字段名。

### A.1 — P2.1 evidence_case_v1

- [x] 创建 `app/prompt/templates/evidence_case_v1.yaml`
- [x] `prompt_id=evidence_case_v1`，`version=1.0.0`，`target_model=qwen-turbo`
- [x] 七段：`role/task/input_constraint/output_schema/prohibitions/fallback/enum_constraints`
- [x] `sections.few_shot`：正常提取 + 多案件去重
- [x] 输出 Schema 引用 `CaseEvidenceBundle`；空输入兜底 `evidence_status=insufficient`
- [x] **验证**：在服务根下 `python -c "import yaml; d=yaml.safe_load(open('app/prompt/templates/evidence_case_v1.yaml')); assert d['prompt_id']=='evidence_case_v1'; assert len([k for k in d['sections'] if k!='few_shot'])>=7; assert 'few_shot' in d['sections']"`

### A.2 — P2.2 evidence_call_qc_v1

- [x] 创建 `app/prompt/templates/evidence_call_qc_v1.yaml`
- [x] `prompt_id=evidence_call_qc_v1`，`target_model=qwen-turbo`
- [x] 七段齐全；`few_shot`：正常通话 + ASR 残缺
- [x] ASR 乱码原样保留；兜底 `is_corrupted=true`
- [x] **验证**：同 A.1 模式，`prompt_id=='evidence_call_qc_v1'`

### A.3 — P2.3 complaint_reflection

- [x] 创建 `app/prompt/templates/complaint_reflection.yaml`
- [x] `prompt_id=complaint_reflection`，`target_model=qwen-turbo`
- [x] Schema：`ReflectionReport`；证据冲突 6 条规则写入；`<3` 条 → insufficient
- [x] `few_shot`：充分 + 不足
- [x] **验证**：`prompt_id=='complaint_reflection'` 且七段+few_shot

### A.4 — P2.4 complaint_fact_timeline

- [x] 创建 `app/prompt/templates/complaint_fact_timeline.yaml`
- [x] `prompt_id=complaint_fact_timeline`，`target_model=qwen-turbo`
- [x] Schema：`FactTimeline`；时间倒序；矛盾双方保留
- [x] `few_shot`：正常 + 矛盾
- [x] **验证**：同上

### A.5 — P2.5 complaint_compliance

- [x] 创建 `app/prompt/templates/complaint_compliance.yaml`
- [x] `prompt_id=complaint_compliance`，`target_model=deepseek-r1`
- [x] `guardrails.require_rag_source: true`（PRD 字段名，不用 `require_source_id`）
- [x] 必须引用法规 doc_id；禁止编造；催收禁止行为清单写入
- [x] **验证**：`assert d['guardrails']['require_rag_source'] is True`

### A.6 — P2.6 complaint_user_demand

- [x] 创建 `app/prompt/templates/complaint_user_demand.yaml`
- [x] `prompt_id=complaint_user_demand`，`target_model=qwen-turbo`
- [x] Schema：`UserDemandAnalysis`；重复投诉 6 条规则；`few_shot`：退款 + 情绪激动
- [x] **验证**：同上结构断言

### A.7 — P2.7 complaint_verdict

- [x] 创建 `app/prompt/templates/complaint_verdict.yaml`
- [x] `prompt_id=complaint_verdict`，`target_model=deepseek-r1`
- [x] verdict 四值枚举硬约束（含 `compliant_but_poor_experience`）；禁止 `normal`
- [x] `few_shot`：至少 3 种 verdict 示例（建议含 pending 或兜底段覆盖 pending）
- [x] **验证**：`'compliant_but_poor_experience' in str(d)` 且 `'normal'` 不作为 verdict 枚举值出现

### A.8 — P2.8 complaint_generate_package

- [x] 创建 `app/prompt/templates/complaint_generate_package.yaml`
- [x] `prompt_id=complaint_generate_package`，`target_model=qwen-plus`
- [x] `guardrails.require_rag_source: true`，`max_response_length: 800`
- [x] 禁止编造金额/天数/政策/法规；pending 标准模板兜底
- [x] **验证**：`require_rag_source` 与 `max_response_length==800`

### 轮次 A 完成标准

- [x] 8 个 YAML 均在 `services/complaint-service/app/prompt/templates/`
- [x] 均可 `yaml.safe_load`；含七段 + `few_shot`；无废弃 `normal` verdict

---

## 轮次 B：Mock 依赖层（P1.3 → P1.1 → P1.2 → P1.4）

> 依赖轮次 A 的 Schema 类名字符串。Mock **不强制继承** `BaseLLMProvider`（common 当前无此基类）。

### B.1 — P1.3 种子数据

- [x] 创建 `tests/mock/data/__init__.py`、`seed_complaints.py`、`seed_cases.py`、`seed_asr_samples.py`
- [x] 8 条种子覆盖 design §6 矩阵（含四值 verdict）
- [x] 每条含 `complaint_id` / `user_id` / `case_ids` / `complaint_text` / `submit_time` / `source_channel` + 预期 verdict
- [x] **验证**：`cd services/complaint-service && python -c "from tests.mock.data.seed_complaints import SEED_COMPLAINTS; assert len(SEED_COMPLAINTS)>=8"`

### B.2 — P1.1 Mock LLM Provider

- [x] 创建 `tests/mock/llm/__init__.py`、`mock_provider.py`
- [x] 实现 `MockLLMProvider`：**鸭子类型** `async invoke(prompt, schema, trace_id, session_id)`；**禁止**因找不到 `BaseLLMProvider` 而修改 `common/`
- [x] `set_delay` / `set_error_mode`（timeout|rate_limit|server_error）/ `set_seed`
- [x] deepseek-r1 reasoning token 单独计入模拟用量（字段可放返回旁路属性或 invoke 内部计数器）
- [x] 创建 design 所列 18 个 LLM fixture JSON
- [x] fixture verdict 仅四值
- [x] **验证**：`from tests.mock.llm.mock_provider import MockLLMProvider; MockLLMProvider()` 可实例化
- [x] **注明**：批次3（5.1）定稿后可再对齐真实 Provider

### B.3 — P1.2 Mock MCP Client

- [x] 创建 `tests/mock/mcp/__init__.py`、`mock_client.py`（及本批 errors 若需要）
- [x] `async call(server, tool, params) -> dict`；`set_mode(tool, mode)`
- [x] **以 PRD §4.4 的 14 tool 为准**（覆盖清单 P1.2 漏列的 `get_complaint_ticket` / `query_call_sessions` / `search_complaint_handling_guidelines` / `push_rag_candidate`）
- [x] timeout → `MCPTimeoutError`；error → `MCPError`
- [x] 至少 14 个 tool 的 normal fixture；场景覆盖 normal / multi_case / repeat / asr_corrupted / evidence_conflict / rag_match（可分布在不同 fixture）
- [x] **验证**：`MockMCPClient()` 可实例化；14 个 tool 均有对应 fixture 文件

### B.4 — P1.4 Mock 推送脚本

- [x] 创建 `scripts/mock/push_complaint.sh`、`push_batch_complaints.py`
- [x] shell：curl POST `http://localhost:8006/api/v1/complaints`；注释写明 GET status 联调属批次7
- [x] python：`--count` / `--delay` / `--mode normal|burst|error`；读 `seed_complaints`
- [x] `chmod +x`；`--help` 可输出
- [x] **验证**：两文件存在 + `python scripts/mock/push_batch_complaints.py --help`
- [x] **验收边界**：本批不要求 API 已通

### 轮次 B 完成标准

- [x] MockLLMProvider + 18 fixtures；MockMCPClient + 14 tools；8 种子；推送脚本
- [x] 无 `normal` verdict；未改 `common/` 强行加基类

---

## 全部完成标准（A + B）

- [x] Prompt ×8、Mock LLM、Mock MCP、种子 ×8、推送脚本均在 **服务根** 下就绪
- [x] 为后续批次提供可 import 的 Mock/Prompt 基线（不声称批次1 已依赖本批）
