# platform/prompt-ops Specification

## Purpose

TBD — created from archiving change prompt-ops-unification. Update Purpose after archive.

## Requirements

### Requirement: 统一加载入口与禁止旁路

全平台所有服务的 LLM prompt MUST 为 YAML 模板，MUST 经 `common/prompt/loader.py` 的 `load_prompt(prompt_id, service, **context_vars)` 唯一入口加载；MUST NOT 在服务代码内拼接 prompt 明文字符串，MUST NOT 新增第二个 prompt 加载入口。

模板文件 MUST 留在各服务本地目录（`services/<service>/app/prompt/templates/` 或该服务既有 conf 目录），loader 接口 MUST 为 `load_prompt(prompt_id: str, service: str, **context_vars) -> str`，按 `service` 参数解析到对应服务模板目录。rag-service 的 conf 目录在 Docker 镜像内路径为 `/app/conf/`，迁移 MUST 保持该路径与 Dockerfile COPY 行为不变。

#### Scenario: 旁路禁止

- **WHEN** 代码审查或 CI 自查发现服务代码内存在未走 loader 的 prompt 明文拼接
- **THEN** 该变更 MUST NOT 合入主干

### Requirement: 结构校验双 Profile

每个 prompt YAML MUST 声明顶层 `profile` 字段（缺省按 `standard` 处理），并 MUST 含 `prompt_id` 与语义化 `version`，`prompt_id` MUST 与文件名去掉扩展名一致。加载器 MUST 按 profile 执行对应校验规则，缺失 MUST 抛 `PromptStructureError`。

#### Scenario: 缺省 profile 按 standard 处理

- **WHEN** YAML 未声明 `profile` 字段
- **THEN** 加载器 MUST 按 `standard` 规则校验

### Requirement: Standard Profile 七段结构

`profile: standard` 的 YAML：`sections` MUST 含七段键 `role` / `task` / `input_constraint` / `output_schema` / `prohibitions` / `fallback` / `enum_constraints`，附加键 `few_shot` MUST 存在（可空）。

#### Scenario: standard 缺段报错

- **WHEN** `profile: standard` 的 YAML 缺七段任一键或缺 `few_shot`
- **THEN** 加载时 MUST 抛 `PromptStructureError`

### Requirement: Simple Profile 四段结构（rag 唯一例外）

`profile: simple` 仅 rag-service 使用（全平台唯一例外）：`sections` MUST 含 `role` / `task` / `input_constraint` / `output_schema` 四段，附加键 `few_shot` MUST 存在（可空，置于 `sections` 内）。

命名例外：rag-service 模板文件名受 Dockerfile `COPY services/rag-service/conf/ ./conf/` 与存量路径约束，保留 `llm_answer_prompt.yaml`，其 `prompt_id` 为 `rag_answer`。这是「`prompt_id` MUST 与文件名去掉扩展名一致」的唯一允许例外；loader 以 YAML 内 `prompt_id` 为准匹配，不校验文件名。其余服务 MUST 遵守文件名一致性。

#### Scenario: simple 不强制七段

- **WHEN** `profile: simple` 的 rag YAML 四段齐全但无 `prohibitions` / `fallback` / `enum_constraints`
- **THEN** 加载 MUST 成功

#### Scenario: simple 渲染入口

- **WHEN** 调用方需要把 `sections` 自行组装为 `(system, user)` 等多段消息（如 qc 的 `build_system_prompt`、rag 的 `build_answer_prompt`）
- **THEN** MAY 使用同模块的 `load_prompt_sections()`，其与 `load_prompt()` 同属唯一加载入口，不构成旁路

### Requirement: 版本与 A/B 回滚

各服务引用 prompt MUST 使用 `prompt_id@version` 或经 A/B 分流。系统 SHALL 在 `common/prompt/ab.py` 提供 `select_version(prompt_id, session_id) -> str`（基于 `hash(session_id + prompt_id) % 100`，同会话稳定）与 `configure_ab` / `rollback`（回滚后立即 100% 指定版本）。qc-service 与 rag-service 的 LLM 调用点 MUST 接入该能力。A/B 配置本期保持进程内存态，为已知边界。

#### Scenario: 同会话稳定命中

- **WHEN** 同一 `session_id` 多次调用 `select_version`
- **THEN** 返回值 MUST 一致

### Requirement: Golden 回归门禁

qc-service 与 rag-service MUST 各建 golden 回归集，判定 SHALL 对齐 complaint-service 既有实现（Mock LLM + 固定 seed 保证可复现）。badcase 回流机制列入后续规划，本期不做。

具体约定：
- 存放：`services/<svc>/tests/regression/golden_set/`，JSONL 格式，每行含 `input` 与 `expected` 两键
- 规模：每服务首批 MUST ≥20 条
- 判定：qc 为结论枚举完全一致率；rag 为 `expected` 中答案要点在输出中的命中率；阈值初始 ≥95%（样本特性不符时 MUST 在规格中说明理由后调整）
- CI 挂点：`service-test (qc-service)` / `service-test (rag-service)` 条目内执行 `pytest tests/regression -q`

#### Scenario: 回归不过门禁拦截

- **WHEN** qc 或 rag 的 prompt 变更导致 golden 一致率低于阈值
- **THEN** CI 对应条目 MUST 失败，PR MUST NOT 合并

#### Scenario: 回归可复现

- **WHEN** 相同 golden 集与相同代码连续执行两次 `pytest tests/regression -q`
- **THEN** 两次结果 MUST 完全一致（Mock LLM + 固定 seed）

### Requirement: Prompt 版本观测

三服务 LLM 调用处 MUST 将实际使用的 `prompt_id@version` 写入 Langfuse span metadata（复用 `common/trace/langfuse_gate`，MUST NOT 新增追踪组件；Langfuse 未启用时 MUST 静默跳过且不抛错）。

具体约定：metadata 键名 MUST 为 `prompt_id` 与 `prompt_version`；写入位置为发起该次 LLM 调用的 span；单测 MUST 能在 Langfuse 未启用时断言 metadata 组装函数的输出。

#### Scenario: 版本可追溯

- **WHEN** Langfuse 已启用且完成一次 LLM 调用
- **THEN** 对应 span metadata MUST 含 `prompt_id` 与 `prompt_version`

#### Scenario: 未启用时静默降级

- **WHEN** Langfuse 未启用（gate 返回 None）
- **THEN** LLM 调用 MUST 正常完成，不因 metadata 写入抛错
