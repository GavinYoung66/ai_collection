# Proposal: prompt-ops-unification

## Why

项目内 LLM prompt 目前三套体系并存：complaint-service 有完整的 PromptOps（统一 loader + 七段校验 + 版本/A/B + golden 回归挂 CI）；qc-service 用 `common/llm/prompts/loader.py` 另一个入口且字段名不一致（已有版本提取，但无语义化版本管理、无 A/B、无回归门禁）；rag-service 仅 system/user 拼接、无任何约束。入口不统一导致标准无法强制、prompt 变更无门禁保护、输出漂移无法追溯。

## What Changes

- **BREAKING（内部）**：`complaint-service/app/prompt/{loader,errors,ab}.py` 下沉为 `common/prompt/`，删除重复入口 `common/llm/prompts/loader.py`；loader 增加双 profile 校验（`standard` 七段 / `simple` 四段）与按调用方服务名解析模板目录。
- complaint-service 全部 import 改指 `common/prompt`，模板与行为零变化；loader/ab 单测随迁 `common/tests/`。
- qc-service：`qc_review.yml` 按七段基准改名补齐（`goal→task`、`constraints→input_constraint`、`schema→output_schema`），补 `prompt_id/version/profile: standard`；调用点改走 common loader；接入 A/B 与 rollback。
- rag-service：`llm_answer_prompt.yaml` 升级为简版四段 + `few_shot`（profile: simple，平台唯一例外）；`build_answer_prompt()` 替换为 `load_prompt("rag_answer")`；接入 A/B 与 rollback。
- qc/rag 各建 golden 回归集并挂入 CI 对应 `service-test` 条目（阈值初定 ≥95%）。
- 观测：三服务 LLM 调用处将 `prompt_id@version` 写入 Langfuse span metadata（复用 `common/trace/langfuse_gate`，不新增追踪组件）。
- 迁移前后对 qc/rag 真实输出做人工 baseline 比对（人工步骤，不进 CI）。
- 不做：Nacos 接入（保持 stub）；A/B 配置持久化（保持进程内存态，已知边界）；badcase 回流机制（列入后续）；不改任何 prompt 业务措辞。

## Capabilities

主规格 Purpose（归档后以此为准）：为全平台服务提供统一的 PromptOps 标准：唯一加载入口与禁止旁路、双 profile 结构校验（standard 七段 / simple 四段，rag 为唯一例外）、版本与 A/B 回滚约定、golden 回归门禁约定，以及 `prompt_id@version` 观测约定。

### New Capabilities

- `platform/prompt-ops`: 全平台 PromptOps 标准——七段基准与 rag 简版例外、loader 双 profile 校验规则、禁止旁路条款（任何 LLM 调用必须经 loader）、版本与 A/B 约定、golden 回归门禁约定、`prompt_id@version` 观测约定。

### Modified Capabilities

- `complaint-service/prompt-ops`: loader/ab 落点从 `app/prompt/` 变更为 `common/prompt/`；加载器校验规则从单一七段变更为双 profile（standard/simple）。
- `complaint-service/prompt-templates`: 验收条款中对 `app.prompt.loader.load_prompt` 的引用更新为 common 公共入口（路径描述性修改，模板位置与内容不变）。

## Impact

- 代码：`common/prompt/`（新增）、`common/llm/prompts/`（删 loader）、`services/complaint-service/app/`（import 与 Langfuse metadata）、`services/qc-service/`（模板与 llm_reviewer 调用点）、`services/rag-service/`（模板与 base.py 调用点）。
- 测试：`common/tests/`（迁入 loader/ab 单测）、qc/rag `tests/`（回归集与断言更新）。
- CI：`.github/workflows/ci.yml` 的 `service-test (rag-service)` / `service-test (qc-service)` 条目新增回归步骤。
- 规格：`openspec/specs/platform/prompt-ops/`（新增）、`complaint-service/prompt-ops` 与 `prompt-templates`（增量）。
