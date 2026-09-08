## Purpose

为 complaint-service 提供 PromptOps 能力：Prompt 统一加载器（支持 `prompt_id@version` 语义化版本 + Jinja2 渲染）与版本/A-B 流量管理（按比例选版本 + 一键回滚）。多版本落盘本批采用「单文件 + 文件内 version 匹配」（见 change design D6）。

## ADDED Requirements

### Requirement: Prompt 加载器

系统 SHALL 在 `app/prompt/loader.py` 提供 `load_prompt(prompt_id: str, **context_vars) -> str`：
- dev：从 `app/prompt/templates/` 加载 YAML（单文件 `{prompt_id}.yaml`）
- prod：配置中心（Nacos）加载——本批允许 stub/接口预留，不得假装已接真 Nacos
- 支持 `prompt_id@version`：加载后校验文件内 `version` 与请求版本一致，不一致抛版本不存在异常；无 `@version` 时，若传入 `session_id` 则可先经 A/B `select_version` 再按该版本规则加载，否则使用文件内 version
- Jinja2 变量替换：对 sections 文本段渲染；**调用方传入的 context 键对应的占位符必须被替换**（不得简单用「整串不含 `{{`」作为唯一验收——正文可能含字面量）
- 加载后校验：元数据齐全；七段键 `role` / `task` / `input_constraint` / `output_schema` / `prohibitions` / `fallback` / **`enum_constraints`** 齐全；附加键 **`few_shot`** 必须存在（可空）；缺失抛结构异常
- **禁止**将 `examples` 作为第七段权威键名

#### Scenario: 加载并渲染 Prompt

- **WHEN** `load_prompt("complaint_verdict", case_id="c1", user_text="...")`（若模板声明了对应 Jinja 变量）
- **THEN** 返回渲染后的 prompt 字符串，且传入的变量占位已被替换

#### Scenario: 指定版本加载（单文件策略）

- **WHEN** `load_prompt("complaint_verdict@1.0.0")` 且文件内 version 为 `1.0.0`
- **THEN** 返回该 Prompt 渲染结果

- **WHEN** `load_prompt("complaint_verdict@9.9.9")` 且磁盘仅有 version=`1.0.0` 的单文件
- **THEN** 抛出版本不存在异常（本批无第二套正文文件）

#### Scenario: 七段结构校验

- **WHEN** 加载的 YAML 缺少七段中任一段（含 `enum_constraints`）或缺少 `few_shot` 键
- **THEN** 抛出结构校验异常，不返回不完整 Prompt

### Requirement: Prompt 版本/A-B 流量管理

系统 SHALL 在 `app/prompt/ab.py` 提供版本与 A/B 能力：
- `select_version(prompt_id, session_id) -> str`：按流量百分比选择版本，基于 `hash(session_id + prompt_id) % 100`，同会话稳定
- `configure_ab` / `rollback`：进程内配置切换，回滚后立即 100% 指定版本
- 双版本**正文**本批可用测试夹具或进程内 override registry；不要求生产目录已有多文件
- `load_prompt` 宜支持可选 `session_id=` 走 A/B 后再加载（或由调用方先 `select_version` 再 `load_prompt(id@ver)`——二者择一写进实现，tasks 需可测）

#### Scenario: A/B 流量分配

- **WHEN** 配置某 prompt_id 的 A/B 为 {v1.1: 90, v1.2: 10}，用大量 session_id 调用 `select_version`
- **THEN** 命中比例约在配置附近（断言合理区间，如 ±10%）

#### Scenario: 同会话版本稳定

- **WHEN** 同一 session_id 多次 `select_version`
- **THEN** 每次命中同一版本

#### Scenario: 一键回滚

- **WHEN** 调用 `rollback(prompt_id, version)`
- **THEN** 新请求立即全部返回该 version
