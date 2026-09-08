# Proposal: complaint-service-batch4

## Why

批次3 已交付 LLM Provider 与 model_router.yaml，但 LangGraph Agent 节点（批次5）调用 LLM 前必须先拼装 Prompt。当前批次0 已交付 8 个 Prompt YAML 正文，但缺少：统一加载器（`load_prompt`）、语义化版本引用（`prompt_id@version`）、Jinja2 渲染、A/B 流量分配、版本回滚、以及 model_router 的多环境/熔断阈值增强。批次4 交付 PromptOps（模块6），打通"Prompt 加载 → 渲染 → 版本/A-B → 路由增强"全链路，为批次5 Agent 节点提供可直接调用的 Prompt 能力。

## What Changes

**6.1 Prompt 加载器（新增）**
- `app/prompt/loader.py`：`load_prompt(prompt_id, **context_vars) -> str`
- dev：从 `app/prompt/templates/` 加载 YAML
- prod：配置中心（Nacos）加载——本批 stub/接口预留，不假装已接真 Nacos
- 支持 `prompt_id@version` 语义化版本引用
- Jinja2 变量替换渲染最终 prompt 字符串

**6.2 八模板元数据补齐（修改）**
- 批次0 已有正文 → 禁止删除/清空正文
- 对齐主 spec / PRD 8.1：校验并补齐元数据与七段键
  - 元数据：prompt_id / version / target_model / scene / change_log / guardrails
  - 七段：`role` / `task` / `input_constraint` / `output_schema` / `prohibitions` / `fallback` / **`enum_constraints`**
  - 附加键：`few_shot`（可空，不替代七段任一槽位）
- **禁止**使用 `examples` 作为第七段键名；**禁止**把 `few_shot` 改名为 `examples` 或清空正文

**6.3 model_router.yaml 增量增强（修改）**
- 批次3 已有 → 增量增强，禁止推倒重写
- 多环境差异（dev/pre/prod 模型名差异）——本批以配置层增量为主，`ModelRouter.route()` 行为不回归（可不改 router.py 读 environments）
- qwen-plus 熔断阈值 P95 > 800ms
- DeepSeek/千问差异配置（API base / reasoning_content 解析 / reasoning token 计费）

**版本/A-B（新增，独立编码任务，非 6.4）**
- 按流量百分比选择 Prompt 版本（如 10% v1.2 / 90% v1.1）
- 一键回滚接口/函数（进程内配置切换，60s 内生效）
- 本批多版本落盘策略见 design D6（单文件 + version 匹配；A/B 可用测试夹具/内存覆盖）

## Non-Goals

- 不实现 LangGraph 节点 / 业务 API
- 6.4 外部对齐（算法/产品/合规评审流程文档）—— 不写代码
- 不接真实 Nacos（prod 路径 stub/接口预留）
- 不删除/清空批次0 Prompt 正文；不把 `few_shot` 改成 `examples`
- 不推倒重写批次3 `model_router.yaml` / 不破坏 `ModelRouter.route()` 批次3 行为
- 本批不实现生产级多文件版本库目录迁移（见 design D6）

## Capabilities

### New Capabilities
- `complaint-service/prompt-ops`: Prompt 加载器 + 版本/A-B 管理（6.1 + 版本/A-B）

### Modified Capabilities
- `complaint-service/prompt-templates`: 8 个 YAML 元数据/七段校验对齐（6.2）
- `complaint-service/llm-layer`: model_router.yaml 增量增强（6.3）

## Impact

- **新增代码**：`app/prompt/loader.py`、`app/prompt/ab.py`（版本/A-B）、`tests/unit/test_prompt_loader.py`、`tests/unit/test_prompt_ab.py`
- **修改代码**：`app/prompt/templates/*.yaml`（仅补缺失元数据/七段键，不删正文）、`app/llm/model_router.yaml`（增量增强）
- **依赖**：批次3（LLM Provider + router）、批次0（8 YAML 正文）
- **不涉及**：LangGraph 节点、业务 API、6.4 外部对齐