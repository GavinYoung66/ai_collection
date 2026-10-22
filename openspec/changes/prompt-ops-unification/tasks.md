# Tasks: prompt-ops-unification

## W0 标准先行

- [x] T0.1 新增 `openspec/specs/platform/prompt-ops/spec.md`：七段基准 + rag 简版例外 + 双 profile 校验规则 + 禁止旁路条款 + 版本/A/B 约定 + 观测约定 + badcase 回流列入后续
- [x] T0.2 存量规格更新：`complaint-service/prompt-ops` 与 `prompt-templates` 中 `app/prompt/` 路径描述改为 common 公共入口（随本 change delta 同步）
- [x] T0.3 录制 qc/rag 当前各 prompt 真实输出 baseline，存档于 `services/<svc>/tests/baseline/prompt_pre_migration/`（纳入 git），供 W2/W3 迁移后人工比对（人工步骤，不进 CI）

## W1 loader 下沉（纯搬家，禁止顺手优化）

- [x] T1.1 `complaint-service/app/prompt/{loader,errors,ab}.py` 迁至 `common/prompt/`；loader 增加双 profile 校验与 `service` 模板目录参数
- [x] T1.2 删除 `common/llm/prompts/loader.py`
- [x] T1.3 投诉服务全部 import 改指 `common/prompt`；`test_prompt_loader.py`/`test_prompt_ab.py` 随迁 `common/tests/`；`grep -r "app.prompt" services/complaint-service` 清零；跑投诉全量 pytest 零回归
- [x] T1.4 CI `common` job 覆盖迁入的 prompt 单测（确认被发现执行）

## W2 qc 迁移

- [x] T2.1 `qc_review.yml` 按 D1 改名补齐七段 + few_shot，补 `prompt_id/version/profile: standard`
- [x] T2.2 `llm_reviewer.py` 改用 common loader 的 `load_prompt`
- [x] T2.3 qc 调用点接入 `select_version`；本地演练一次 `rollback` 并记录结果
- [x] T2.4 更新 `tests/test_qc_review_prompt.py`；用 T0.3 baseline 人工比对输出确认无漂移

## W3 rag 迁移

- [x] T3.1 `llm_answer_prompt.yaml` 升级为 D2 简版 + `prompt_id/version/profile: simple`；文件路径与 Dockerfile COPY 行为（镜像内 `/app/conf/`）保持不变
- [x] T3.2 `build_answer_prompt()` 替换为 `common/prompt/loader.load_prompt_sections("rag_answer")`（simple 渲染场景的合法入口，见平台规格「simple 渲染入口」），保留 `{query}/{context}` 变量语义
- [x] T3.3 rag 接入 A/B 与 rollback 演练（同 T2.3）
- [x] T3.4 用 T0.3 baseline 人工比对输出确认无漂移

## W4 golden 回归与门禁

- [x] T4.1 qc golden 集（典型质检文本→期望结论）与 rag golden 集（典型问题→期望答案要点）；Mock LLM + 固定 seed；阈值 ≥95%
- [x] T4.2 两套回归分别挂入 CI `service-test (qc-service)` / `service-test (rag-service)` 条目

### W4 验证证据

样本与判定：qc 24 条、rag 24 条（均 ≥20）；qc 一致率 1.0000、rag 命中率 1.0000。

门禁口径：`_SPEC_MIN_RATE = 0.95`（规格下限），`_GATE_RATE = 1.0`（当前门禁）。
原实现同时保留 `assert not misses` 与 `rate >= 0.95`，后者被前者短路而不可达；
现统一为单条 `rate >= _GATE_RATE`，阈值语义自洽且不放宽规格下限。

词表单一事实源：qc 回归不再本地复制关键词，改为直接引用
`app/services/rule_engine.py` 常量与 `app/seeds/qc_rules_v1.R14_KEYWORDS`
（对象同一性实测 True），规则改词后回归自动跟随。

Prompt 契约（补齐「prompt 变更 → 门禁拦截」在 qc 侧的可执行路径）：

| 变异（临时目录注入，不改仓库文件） | 拦截结果 |
|---|---|
| required 去掉 `rule_id` | `test_prompt_output_schema_matches_consumers` 失败：`required 缺字段: ['rule_id']` |
| redline_tag 枚举删掉 `threat` | `test_prompt_rule_vocabulary_consistent` 失败：`R05 的 redline_tag=threat 不在 prompt 枚举内` |
| 删除 `prohibitions` 段 | 三条契约用例均抛 `PromptStructureError: missing sections: ['prohibitions']` |

改动归属（时间窗）：本批仅新建两个 `tests/regression/**` 目录与修改 ci.yml
`Run tests` / `Prompt regression` 两处；prompt YAML、`common/prompt/**`、
qc/rag 业务代码在审查时段的改动归属批次 3 与批次 5（mtime 分别为 13:29–16:33、
18:32+），不属本批范围。

## W5 观测

- [x] T5.1 三服务 LLM 调用处将 `prompt_id@version` 写入 Langfuse span metadata（复用 langfuse_gate，未启用时静默跳过）

## W6 验收

- [x] T6.1 全量 CI 绿（6 条 check，common job 含 prompt 单测，qc/rag 条目含回归）——按 ci.yml 逐 job 本地等价复跑，6/6 全绿（未推送，远端复核待做）
- [x] T6.2 每服务一次真实调用抽查：输出与 baseline 无漂移，Langfuse 可见 prompt 版本
- [x] T6.3 对照 proposal 验收标准 1-5 逐条验证并留证

### W6 验证证据（2026-10-21）

LLM 端点：`.env` `LLM_DEEPSEEK_BASE_URL` 由 `http://host.docker.internal:8000/v1`（本地）改为 `https://api.deepseek.com/v1`（线上）；qc 保持 DashScope `qwen-plus`（云端，非本地）。

| 验收项（platform/prompt-ops Requirement） | 证据 |
|---|---|
| 1 统一加载入口与禁止旁路 | complaint 6 处、qc `llm_reviewer.py:373`、rag `base.py:43` 全部经 `load_prompt(_sections)`；全仓无硬编码中文 system prompt |
| 2 双 Profile 结构校验 | standard 七段与 simple 四段均有单测；rag 模板按 standard 加载抛 `PromptStructureError`，按 simple 通过 |
| 3 版本与 A/B 回滚 | 三服务均接入 `select_version`；rag/qc rollback 演练记录（`tests/baseline/rollback_drill.md`）1000/1000 命中目标版本 |
| 4 Golden 回归门禁 | qc 24 样本一致率 1.0000、rag 24 样本命中率 1.0000；`service-test` 新增 `pytest tests/regression -q` 步骤；主步骤 `--ignore=tests/regression` 防重复 |
| 5 Prompt 版本观测 | Langfuse 实查三 span metadata：`rag_answer_llm → rag_answer/1.0.0`、`complaint_judge → complaint_verdict/1.0.0`、`qc_llm_review → qc_review/1.0.0` |

真实调用抽查：rag `deepseek-chat` 返回「不可以。催收不得在每日22:00至次日8:00进行。（第18页）」；complaint 经 `complaint_verdict` 模板返回结构化 verdict；qc `qwen-plus` 返回 JSON。rag prompt 渲染与迁移前 baseline diff 为 0 行。

本地 CI 等价（按 ci.yml 逐 job 复跑，6/6 绿）：

| check | 结果 |
|---|---|
| common | 358 passed；scaffold call/case/review 各 4 passed |
| service-test (data-platform) | 17 passed |
| service-test (mcp-gateway) | 54 passed |
| service-test (rag-service) | 378 passed(1 skipped) + regression 4 passed |
| service-test (qc-service) | 382 passed(2 skipped) + regression 4 passed |
| service-test (complaint-service) | unit+integration 369 passed(cov 95.93%) + e2e 3 + regression 5 + smoke 13/13 exit 0 |

注：本地 smoke 首轮失败系 8006 端口被已在运行的 complaint 容器占用（该容器 `USE_MOCK_MCP=false` 走真 PG，`cases` 表无对应行触发 FK），非代码缺陷；换 8016 端口并以 CI 同款 `USE_MOCK_MCP=true` 复跑后 13/13 通过。

## 约束

- 每完成一个 W 立即增量验证，禁止攒到最后一次性跑
- 变更边界：`common/prompt/`、`common/llm/prompts/`、三服务 prompt 模板与调用点、`openspec/specs/`、`tests/`、`.github/workflows/ci.yml`；不碰业务逻辑与 prompt 文案内容
