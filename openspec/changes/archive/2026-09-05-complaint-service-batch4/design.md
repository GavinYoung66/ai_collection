# Design: complaint-service-batch4

## Context

批次3 已交付 LLM Provider（`ComplaintLLMProvider.invoke`）与 `model_router.yaml`（按 agent_role 路由 + 四级降级链）。批次0 已交付 8 个 Prompt YAML 正文（含 prompt_id/version/target_model/scene/change_log/guardrails + 七段 `enum_constraints` + 附加 `few_shot`）。批次5 LangGraph Agent 节点调用 LLM 前必须加载并渲染 Prompt。

当前缺口：
- 无统一 `load_prompt` 加载器（无法 `prompt_id@version` + Jinja2 渲染）
- 无版本/A-B 流量分配与回滚
- model_router 缺多环境差异、qwen-plus 阈值、DeepSeek/千问差异配置

## Goals / Non-Goals

**Goals:**
- 8 个 YAML 均可被 `load_prompt` 加载并 Jinja2 渲染
- 支持 `prompt_id@version`（与文件内 version 匹配）与版本切换
- A/B 流量分配可单测
- model_router 增强后 `ModelRouter.route()` 仍可 route（不破坏批次3）

**Non-Goals:**
- 不实现 LangGraph 节点/业务 API
- 6.4 外部对齐（算法/产品/合规评审流程文档）—— 不写代码
- 不接真实 Nacos（prod 路径 stub/接口预留）
- 不删除/清空批次0 Prompt 正文；不引入 `examples` 替代 `enum_constraints`/`few_shot`
- 不推倒重写批次3 model_router.yaml
- 本批不落地生产级多文件版本目录（见 D6）

## Decisions

### D1: load_prompt 用 Jinja2 渲染 sections 拼接

`load_prompt(prompt_id@version, **ctx)` 流程：
1. 解析 prompt_id 与 version（`@` 分隔，无 version 时：若传入 `session_id` 则经 A/B `select_version`；否则取该 prompt_id 文件内 version）
2. 加载 YAML（dev: 读文件；prod: Nacos stub）
3. 校验结构：元数据齐全；七段键 `role/task/input_constraint/output_schema/prohibitions/fallback/enum_constraints` 齐全；`few_shot` 键必须存在（可空）
4. 对 sections 中文本段逐段 Jinja2 渲染（传入 context_vars）；已声明变量必须被替换
5. 按固定顺序拼接（含 `few_shot`）成最终 prompt 字符串返回

- 理由：与主 spec / PRD 8.1 一致；逐段渲染边界清晰
- 备选：整段拼接后一次渲染 → 拒绝；用 `examples` 当第七段 → 拒绝（与现网 YAML 冲突）

### D2: A/B 基于 session_id 哈希，确定性分配

`select_version(prompt_id, session_id)`：
1. 取该 prompt_id 的 A/B 配置（版本→百分比）
2. `hash(session_id + prompt_id) % 100` 落在哪个版本区间
3. 返回版本号

- 理由：同会话稳定，无需持久化；可单测断言比例区间
- 备选：随机分配 → 拒绝，同会话可能跳变

### D3: 回滚用进程内配置切换

`rollback(prompt_id, version)` 直接修改进程内 A/B 配置为 {version: 100}，立即生效。

- 理由：PRD 要求 60s 生效，进程内切换远快于 60s；真实生产可叠加配置中心推送，本批不实现
- 备选：写文件/Redis → 拒绝，增加依赖且本批无必要

### D4: model_router 增量增强，保留批次3 结构

在批次3 yaml 基础上：
- 顶层新增 `environments: {dev, pre, prod}` 分组，各环境可覆盖模型名/API base（**配置层预留**）
- `breaker.qwen_plus` 新增 `p95_threshold_ms: 800`
- 新增 `provider_configs: {deepseek, qwen}` 差异配置（base_url / reasoning_content 解析 / reasoning token 计费开关）
- **本批默认不改 `ModelRouter.route()` 读 environments**——只要顶层 `routes` 保留，批次3 `route()` 行为不变；环境覆盖留给后续或可选增强

- 理由：批次3 路由已验证可用，增量增强不改变 `route()` 行为
- 备选：重写 yaml / 强制改 router.py → 拒绝无必要回归风险

### D5: 七段补齐只增不删（键名对齐主 spec）

对 8 个 YAML：
- 校验七段键为 **`enum_constraints`（第 7 段）**，附加 **`few_shot`**
- 缺失的元数据/段键补默认值或空占位；**已有段正文不动**
- **禁止**新增权威键 `examples` 替代上述结构

- 理由：批次0 + 已归档 `prompt-templates` 主 spec 已定稿
- 备选：改名为 examples → 拒绝

### D6: 本批多版本落盘策略（单文件 + version 匹配）

现状：每个 prompt_id 对应 `templates/{prompt_id}.yaml` 一个文件，内嵌 `version` 字段（当前多为 `1.0.0`）。

本批约定：
1. **权威存储**：仍为单文件；文件内 `version` 为该 prompt_id 的当前落地版本
2. **`load_prompt("id@ver")`**：加载文件后，若 `ver !=` 文件内 `version` → 抛 `PromptVersionNotFoundError`（本批无第二套文件则无法加载「另一版本正文」）
3. **A/B / 单测**：
   - 可用进程内 **version override registry**（测试注册假版本内容或指向同一正文），或
   - 单测仅验证 `select_version` 比例/稳定性 + `rollback`，加载路径用现网 `1.0.0`
4. **生产多版本目录**（如 `templates/{id}/{ver}.yaml`）或 Nacos 多版本：**本批不做**，在 Open Questions / 后续批次

- 理由：避免本批大搬家；仍满足接口形态与可测 A/B 选择逻辑
- 备选：本批就建多文件目录 → 拒绝（范围膨胀、易伤批次0 正文）

## 文档冲突处理

1. **任务表 6.4 = 外部对齐不写代码** vs **批次说明写「6.4 A/B」**：
   - 本 change 将 A/B 作为独立编码任务（`app/prompt/ab.py`），不占用 6.4
   - 6.4 仍为 Non-Goals

2. **任务表 6.2「仅骨架」** vs **批次0「已有正文」**：
   - 以保留正文 + 补元数据/缺键为准

3. **规划曾写第七段 `examples`** vs **PRD/主 spec/`enum_constraints`+`few_shot`**：
   - 以主 spec + 现网 YAML 为准（见 D5）

## Risks / Trade-offs

- **[Nacos 未对接]** → prod 路径用 stub，接口预留
- **[Jinja2]** → context_vars 由内部传入；验收不简单用「全串无 `{{`」一刀切（正文可能含字面量）
- **[A/B 比例断言]** → 大量样本 ±10% 容忍，避免 flaky
- **[单文件多版本限制]** → `@其他版本` 本批会 NotFound；A/B「双版本正文」靠测试 registry，不假装已有两套生产文件
- **[model_router 增强回归]** → 保留批次3 `route()` 断言

## Migration Plan

1. 新增 app/prompt/loader.py + ab.py
2. 校验/补齐 8 YAML 缺键（不删正文）
3. 增量增强 model_router.yaml
4. 回滚：删除 loader/ab，还原 yaml 增强项

## Open Questions

- prod Nacos 接入与多版本存储格式待后续批次
- 环境覆盖是否由 `ModelRouter` 读取 `environments` 待产品确认（本批配置先落地）
