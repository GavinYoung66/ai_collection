# Design: prompt-ops-unification

## Context

现状见 proposal.md。约束：complaint-service 的 loader 已有双能力（版本/A/B/七段校验）且被 CI 回归背书，是全平台唯一可复用实现；`common/` 已有公共零件先例（`common/trace/langfuse_gate.py`：引擎公共化、服务侧薄适配）；本变更为跨服务改动 + loader 公共化，属于需要设计先行的横切变更。

## Goals / Non-Goals

**Goals:**
- 唯一入口 `common/prompt/loader.py`，三服务模板本地化
- loader 双 profile 校验（standard 七段 / simple 四段），rag 例外落地
- qc/rag 补齐版本、A/B、golden 回归并挂 CI
- `prompt_id@version` 写入 Langfuse span metadata

**Non-Goals:**
- Nacos 真接入、A/B 配置持久化、badcase 回流（后续批次）
- 任何 prompt 文案优化（只做结构迁移与字段改名）
- 改动三服务业务逻辑

## Decisions

### D1 引擎公共化、模板本地化
loader/errors/ab 进 `common/prompt/`；YAML 模板留在各服务目录。理由：引擎是机械逻辑（同 langfuse_gate 先例），模板是业务知识不该进 common。备选方案「模板也进 common」被拒绝：会让 common 背负各服务业务内容，变更耦合。

### D2 模板目录解析：显式 service 参数
loader 增加 `service` 参数（或从调用方配置注入），按服务名解析到该服务模板目录。备选「自动探测调用方包名」被拒绝：隐式魔法，测试与打包场景易碎。

### D3 双 profile 校验
YAML 顶层 `profile: standard|simple`（缺省 standard）。simple 只校验四段 + few_shot。理由：D2 决策已定 rag 为例外；若 loader 一刀切七段，rag 永远不合规。备选「rag 也补齐七段」被拒绝：rag prompt 无枚举/禁用语需求，硬凑七段是形式主义。

### D4 qc 字段映射而非重写
`goal→task`、`constraints→input_constraint`、`schema→output_schema`，正文逐字保留。理由：范围锁定"不改业务措辞"，降低输出漂移风险。

### D5 qc/rag 回归判定复用投诉实现模式
Mock LLM + 固定 seed 保证可复现（对齐 complaint-service/prompt-regression 既有规格），阈值 ≥95% 起步。备选「真 LLM 回归」被拒绝：CI 不稳定且产生真实配额消耗。

### D6 观测复用 langfuse_gate
调用处将 `prompt_id@version` 塞进已有 LangfuseRun span metadata；Langfuse 未启用时静默跳过（遵守 gate 的 P1 降级安全约定）。不新增任何追踪组件。

## Risks / Trade-offs

- [qc/rag 迁移后输出漂移] → T0.3 迁移前录制真实输出 baseline，迁移后人工比对；差异超容忍则回滚该服务迁移
- [loader 搬家引入 import 遗漏] → W1 完成后立即跑投诉全量 pytest + `grep -r "app.prompt.loader"` 清零
- [双 profile 校验被滥用为例外口] → 平台规格显式声明 simple 仅 rag 使用；新增 simple 模板需评审
- [golden 阈值误报] → 初始 95%，连续误报时在规格中说明理由后调整，禁止静默放宽

## Migration Plan

1. W1 先搬 loader（纯重构，投诉全量测试零回归为门禁）
2. W2/W3 逐服务迁移，各自 baseline 比对通过后再进下一服务
3. W4 挂 CI 门禁，W5 观测，W6 全量验收
4. 回滚策略：每 W 独立 commit；任一服务迁移失败仅回滚该 W，不影响已完成部分

## Open Questions

- qc/rag golden 集首批样本量（建议各 ≥20 条，实施时按现有日志/种子数据确定）
