# 大模型 Prompt 与 Structured Output 开发规范

> 文档类型：技术规范（规范层）
> 版本：v1.5
> 状态：定稿
> 更新日期：2026-09-09
> 约束声明：本文件为 LLM Prompt 编写与结构化输出单一事实源；遵循 step1/01 全局统一口径与 openspec/specs/05、04；LLM 调用统一经 common/llm/provider.py，业务层不得绕过；推理参数按场景分层（评测分支 temperature=0.1/top_p=0.9/seed=42 固定；生产对话生成 temperature 默认 0.3、范围 0.1~0.5 可配置），禁止业务层硬编码覆盖（见 §6.2）。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，6 条规则 | — |
| v1.0 | 2026-08-19 | 补标准 Prompt 七段结构、Pydantic Schema 校验链、CoT 剥离与加密、置信度门控、调用审计、熔断参数、Prompt 模板示例 | 规范定稿 |
| v1.1 | 2026-09-09 | P1-9 §3.4 枚举表 verdict 去除 'normal'，改为 'compliant_but_poor_experience'（4 值，对齐 complaint-service VerdictType 实际交付） | Ellis Guo |
| v1.2 | 2026-09-09 | P1-16 §8.2 敏感词端点统一 /sensitive-words/current.dict → /api/v1/sensitive-words/current.dict（决策A执行） | Ellis Guo |
| v1.3 | 2026-09-09 | P1-18 §7.3 兜底话术来源改为双层：rag-service 标准答案库为热更新主源 + common/llm/prompts/fallback_scripts.yml 为只读灾难快照（随版本发布、禁止日常编辑）；§7.2 代码注释同步修正（决策A执行） | Ellis Guo |
| v1.5 | 2026-09-09 | 对齐 DD-02 V1.4：§3.4 增 grade / qc_status / redline_tag；§3.3 登记 qc-service LLMReviewer 置信度门控豁免 | Ellis Guo |

## 一、Prompt 配置化管理

### 1.1 管理原则

- 所有 Prompt 统一配置文件管理，yml/常量文件统一维护，禁止业务代码硬编码 Prompt；
- Prompt 文件存放于 `common/llm/prompts/`，按场景命名 `<scenario>.yml`；
- 环境变量注入动态参数，禁止字符串拼接明文 Prompt；
- Prompt 变更走 Git PR + Code Owner 评审，禁止热更新。

### 1.2 目录结构

```
common/llm/
├── provider.py            # 统一 LLM 调用入口
├── circuit_breaker.py     # 熔断器
├── rule_engine.py         # RuleBasedScriptEngine 兜底
├── schemas/               # Pydantic 结构化输出 Schema
│   ├── dialog_schema.py   # 对话应答 Schema
│   ├── intent_schema.py   # 意图识别 Schema
│   ├── qc_review_schema.py # 质检 LLM 审查 Schema
│   ├── complaint_schema.py # 投诉判责 Schema
│   └── review_classify_schema.py # 复盘分类 Schema
└── prompts/               # Prompt 模板
    ├── dialog.yml          # 对话应答 Prompt
    ├── intent.yml          # 意图识别 Prompt
    ├── qc_review.yml       # 质检 LLM 审查 Prompt
    ├── complaint_judge.yml # 投诉判责 Prompt
    └── review_classify.yml # 复盘分类 Prompt
```

## 二、标准 Prompt 结构

所有催收业务场景 Prompt 必须包含以下七段，缺一不可：

### 2.1 七段结构

| 段 | 名称 | 内容 | 示例 |
|---|---|---|---|
| 1 | 角色定位 | LLM 扮演的角色与职责边界 | "你是一名合规催收话术生成助手" |
| 2 | 任务目标 | 本轮调用的具体任务 | "根据客户回复生成下一轮催收话术" |
| 3 | 输入约束 | 入参格式与来源说明 | "客户说：{user_text}；案件信息：{case_info}；历史对话：{history}" |
| 4 | 输出 JSON Schema | 强制结构化输出格式定义 | `{"reasoning":"...","answer":"...","confidence":0.0,"intent":"..."}` |
| 5 | 禁止行为 | 红线约束，禁止生成的内容 | 禁止暴力/威胁/伪造身份/编造金额/自定义减免政策 |
| 6 | 兜底规则 | 无法确定时的降级行为 | confidence<0.7 时 answer 返回"转人工"兜底话术 |
| 7 | 枚举限定 | 固定枚举输出字段 | intent ∈ [willing_to_pay, extension_request, ...] |

### 2.2 Prompt 模板示例（对话应答）

```yaml
# common/llm/prompts/dialog.yml
role: "你是一名合规催收话术生成助手，仅生成合规催收话术，不做任何业务决策。"
task: "根据客户最新回复和案件信息，生成下一轮催收应答话术。"
input_constraints:
  customer_text: "{user_text}"
  case_info: "案件号：{case_number}，逾期金额：{overdue_amount}元，逾期天数：{overdue_days}天，账龄：{account_stage}"
  dialog_history: "{history_turns}"
  rag_context: "{retrieved_knowledge}"
output_schema:
  reasoning: "推理过程（仅管理员可见）"
  answer: "面向客户的催收话术"
  confidence: "0.0-1.0，低于0.7须返回兜底"
  intent: "意图标签"
prohibited:
  - 禁止暴力、恐吓、施压、骚扰、威胁类语句
  - 禁止暗示起诉、上门、征信黑名单、通讯录曝光
  - 禁止伪造法院、公安、律师、官方机构身份
  - 禁止编造金额、逾期天数、还款状态
  - 禁止自定义减免、延期、免息、分期政策
  - 禁止自由解读征信影响、法律后果
fallback:
  rule: "confidence < 0.7 时 answer 返回标准转人工话术，intent 返回 uncertain"
  script: "您好，为了更好地帮您处理，我将为您转接专属顾问，请稍候。"
enum_constraints:
  intent:
    - willing_to_pay
    - extension_request
    - financial_difficulty
    - stalling
    - rejection
    - anger
    - complaint
    - uncertain
```

## 三、结构化输出强制规范

### 3.1 强制 JSON 输出

- 所有催收业务场景必须启用结构化 JSON 输出，禁止自由文本、无格式乱输出；
- LLM 调用强制 `response_format={"type": "json_object"}`；
- 按 Pydantic Schema 校验，校验不通过触发重试，重试耗尽走兜底。

### 3.2 输出校验链

```
LLM 返回 JSON 字符串
  ↓
JSON 解析（json.loads）
  ↓ 失败 → 重试（最多 3 次）
Pydantic Schema 校验（必填字段 + 类型 + 枚举 + 长度）
  ↓ 失败 → 重试
置信度门控（confidence < 0.7）
  ↓ 不通过 → 转人工 / 兜底
敏感词后置校验（AC 自动机）
  ↓ 命中 → B0010 拦截 + 兜底话术
返回业务层使用
```

### 3.3 校验规则

| 校验项 | 说明 | 失败处理 |
|---|---|---|
| JSON 解析 | 返回内容必须是合法 JSON | 重试 3 次，耗尽走兜底 |
| 必填字段 | reasoning/answer/confidence 不可缺 | 重试 3 次，耗尽走兜底 |
| 类型校验 | confidence 为 float 0-1 | 重试 3 次，耗尽走兜底 |
| 枚举校验 | intent 必须在预定义枚举内 | 重试 3 次，耗尽走兜底 |
| 长度校验 | answer 长度 ≤ 500 字 | 截断 + 标记 |
| 置信度门控 | confidence < 0.7 禁止直接回复 | 走兜底或转人工 |

> **豁免（qc-service LLMReviewer）**：质检 LLM 输出不直达客户，允许跳过本步门控，但必须保留 JSON Schema、幻觉校验（evidence turn_id 存在、reason 不含编造金额）、熔断降级为 AC+规则。`llm_call_logs.status=qc_review_exemption`。该豁免不适用于 call-service / complaint-service。Schema 仍输出 `confidence` 供审计。

### 3.4 枚举限定场景

质检、打分、标签、分类、投诉研判、风险判定场景严格固定枚举输出，禁止自定义文字结果：

| 场景 | 枚举字段 | 枚举值 |
|---|---|---|
| 对话意图 | intent | willing_to_pay / extension_request / financial_difficulty / stalling / rejection / anger / complaint / uncertain |
| 质检结果 | qc_result | compliant / violation / pending_review |
| 合规档位 | grade | EXCELLENT / PASS / WARNING / SERIOUS |
| 质检状态 | qc_status | DONE / PENDING_REVIEW / REVIEWED / PARTIAL |
| 红线标签 | redline_tag | threat / fake_identity / stop_call / fake_commit / legal_overstate / unverified / data_fabrication / privacy_leak |
| 风险等级 | risk_level | low / medium / high |
| 案件账龄 | account_stage | M0 / M1 / M2 / M3 |
| 案件状态 | case_status | pending / processing / paid / bad_debt / lost |
| 投诉判责 | verdict | agent_violation / user_misunderstanding / compliant_but_poor_experience / pending |
| 复盘分类 | category | good / bad / violation / ineffective |

## 四、CoT 剥离与加密

### 4.1 双字段拆分

LLM 输出 Schema 统一拆 `reasoning` + `answer` 两字段：

| 字段 | 用途 | 可见性 | 存储 |
|---|---|---|---|
| reasoning | 推理过程（Chain of Thought） | 仅 role=admin 可解密 | AES-256 加密写入 `dialog_turns.reasoning_encrypted` |
| answer | 面向客户的催收话术 | 客户/坐席可见 | 明文写入 `dialog_turns.text` |

### 4.2 加密规范

- 加密算法：AES-256-GCM；
- 密钥来源：docker secrets 注入，禁止入 Git / .env / 代码；
- 加密位置：`common/security/crypto.py`，`encrypt_reasoning(text) -> encrypted_text`；
- 解密权限：仅 role=admin 通过专门接口解密，记录 audit_logs。

## 五、置信度门控

| 阈值 | 适用场景 | 处置 |
|---|---|---|
| confidence ≥ 0.7 | LLM 对话应答 | 正常回复客户 |
| 0.6 ≤ confidence < 0.7 | LLM 对话应答 | 触发二次确认或转人工 |
| confidence < 0.6 | 意图识别 | 记为 uncertain，不阻断流程但落记录 |
| score < 0.6 | RAG 检索 | 判定知识库无匹配，标记降级、不自由作答 |

> confidence < 0.7 禁止直接回复用户，走兜底或转人工（openspec/specs/04 强制4）。

## 六、LLM 调用规范

### 6.1 调用入口

统一入口：`common/llm/provider.py`，业务层只调用，不得绕过。

```python
# common/llm/provider.py
class LLMProvider:
    async def invoke(
        self,
        prompt: str,
        schema: type[BaseModel],    # Pydantic Schema
        trace_id: str,
        session_id: str | None = None,
    ) -> LLMResponse:
        """
        统一 LLM 调用入口：
        1. 组装推理参数（按场景分层：评测分支 temperature=0.1/top_p=0.9/seed=42；生产对话 temperature=0.3 默认可配置，见 §6.2）
        2. 强制 response_format=json_object
        3. 5s 超时 + 3 次指数退避
        4. JSON 解析 + Pydantic 校验
        5. 置信度门控
        6. 敏感词后置校验
        7. 写入 llm_call_logs
        """
```

### 6.2 推理参数（评测/生产分层）

| 场景 | 参数 | 值 | 说明 |
|---|---|---|---|
| 评测/回归集 | temperature | 0.1 | 低随机性，保证评测可复现（评测分支固定） |
| 评测/回归集 | seed | 42 | 固定种子，评测可复现 |
| 生产对话生成 | temperature | 0.3（默认，可配置 0.1~0.5） | 兼顾话术多样性与可控性；seed 不固定（对话实时生成不要求可复现） |
| 通用 | top_p | 0.9 | 核采样 |
| 通用 | response_format | {"type": "json_object"} | 强制 JSON 输出 |

> 温度分层：评测/回归集固定 temperature=0.1+seed=42（仅评测分支）；生产对话生成默认 0.3、范围 0.1~0.5，由 provider 统一注入（可配置化，禁止业务层硬编码覆盖）。生产用 0.1 会导致话术过度机械并放大"3 轮内重复话术转人工"的误触发（见 step3/03）。

### 6.3 超时与重试

| 参数 | 值 |
|---|---|
| 超时 | 5s |
| 重试次数 | 3 次 |
| 退避策略 | 指数退避 1s / 2s / 4s |
| 重试条件 | 超时、网络异常、JSON 解析失败、Pydantic 校验失败 |
| 重试耗尽 | 触发降级（走 RuleBasedScriptEngine 兜底） |

### 6.4 熔断

| 参数 | 值 | 说明 |
|---|---|---|
| 熔断器 | pybreaker | 连续 3 次失败或 P95 > 800ms 触发 |
| 熔断窗口 | 窗口期内停止 LLM 调用 | 走 RuleBasedScriptEngine 固定兜底 |
| 恢复 | 60s 后半开探测 | 恢复后重试 LLM 调用 |
| 标识 | `{service}:llm_degraded` = "1" | Redis 标识，见 step4/03 |

### 6.5 调用审计

每次 LLM 调用自动写入 `llm_call_logs`：

| 字段 | 来源 | 说明 |
|---|---|---|
| trace_id | contextvars | 全链路追踪 |
| session_id | 入参 | 会话关联 |
| provider | 配置 | 如 dashscope |
| model | 配置 | 如 qwen-plus |
| prompt | 入参（脱敏后） | 经 mask.py 过滤 |
| response | LLM 返回（脱敏后） | 经 mask.py 过滤 |
| token_usage | SDK 返回 | token 消耗统计 |
| latency_ms | 计时 | 调用延迟 |
| confidence | LLM 输出 | 置信度 |
| temperature | 按场景分层注入 | 评测 0.1 / 生产默认 0.3（见 §6.2） |
| status | 内部判定 | success/failed/timeout/circuit_open |
| error_msg | 异常信息 | 失败原因 |

> prompt 和 response 落库前必须经 mask.py 脱敏（手机号/身份证/金额打码），违规即重大事故。

## 七、RuleBasedScriptEngine 兜底

### 7.1 适用场景

LLM 熔断、超时、报错、返回空值、置信度不足时，切换 RuleBasedScriptEngine 本地催收话术决策树。

### 7.2 实现规范

```python
# common/llm/rule_engine.py
class RuleBasedScriptEngine:
    """
    本地催收话术决策树，LLM 不可用时的兜底。
    基于 intent_label + dialog_turn 匹配预设话术。
    """
    async def generate(
        self,
        intent: str,
        turn_number: int,
        case_info: dict,
    ) -> FallbackResponse:
        """
        根据意图标签和对话轮次返回兜底话术。
        灾难快照话术（rag 故障时）存储在 common/llm/prompts/fallback_scripts.yml
        """
```

### 7.3 兜底话术来源（双层：热更新主源 + 灾难快照）

兜底话术采用双层来源，避免单一依赖（决策A执行）：

1. **热更新主源（默认）**：rag-service 标准答案库（category=script），支持热更新，业务运行期从知识库检索兜底话术；
2. **灾难快照（兜底）**：`common/llm/prompts/fallback_scripts.yml`，仅当 rag-service 也故障时由 RuleBasedScriptEngine 读取的只读快照，随版本发布、禁止日常编辑话术。

- 兜底话术统一在 rag-service 标准答案库维护（category=script），禁止硬编码在业务代码（详见 step1/03 兜底话术表）；
- `common/llm/prompts/fallback_scripts.yml` 为只读灾难快照，仅随版本发布更新，禁止作为日常话术维护入口；
- 兜底话术须经合规审核，不含任何违规内容。

## 八、敏感词后置校验

### 8.1 校验流程

LLM 输出 `answer` 字段后，须经 `common/security/sensitive_words.py`（AC 自动机）校验：

```
LLM 返回 answer
  ↓
AC 自动机扫描（threat/abuse/induce 三类）
  ↓ 命中
B0010 拦截 → 记录日志 → 返回合规兜底话术 → 不向下游传递违规内容
  ↓ 未命中
正常返回 answer 给业务层
```

### 8.2 敏感词库

- 敏感词库由 rag-service 维护（sensitive_words 表），三分类：threat / abuse / induce；
- qc-service 通过 `/api/v1/sensitive-words/current.dict` 拉取 + Redis Pub-Sub 热刷新（60s 内生效）；
- 命中即返回 B0010 拦截，详见 step5/04。
