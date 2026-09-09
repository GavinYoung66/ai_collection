# 催收合规拦截与安全护栏 Guardrails 规范

> 文档类型：技术规范（规范层）
> 版本：v1.4
> 状态：定稿
> 更新日期：2026-09-09
> 约束声明：本文件为 Guardrails 合规拦截单一事实源；遵循 step1/01 全局统一口径与 openspec/specs/00（红线）、04（护栏）；红线本体以 openspec/specs/00_biz_redline.md（S0·全局最高）为唯一源；兜底话术统一在 rag-service 知识库维护，禁止硬编码。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅双校验与拦截动作 | — |
| v1.0 | 2026-08-19 | 补五层护栏架构、AC 自动机实现、拦截动作矩阵、降级链路、停止致电状态机、规则配置化、审计追溯、回归测试门禁 | 规范定稿 |
| v1.1 | 2026-09-09 | P1-15 §七停止致电标记落库改为引用 cases.stop_call_status 字段（含 5 值枚举 + audit_logs 留痕），消除"case_status 或专用标记"歧义（决策A执行） | Ellis Guo |
| v1.2 | 2026-09-09 | P1-16 §4.3 敏感词端点统一 /sensitive-words/current.dict → /api/v1/sensitive-words/current.dict（决策A执行） | Ellis Guo |
| v1.4 | 2026-09-09 | 对齐 DD-02 V1.5：L5 事中告警改 system.alert.raised；禁止 qc.alert.raised；追溯链去掉「LLM→质检→TTS」 | Ellis Guo |

## 一、合规红线（与 openspec/specs/00 逐条对齐，S0）

以下 7 条为全站最高优先级，任何业务逻辑、话术生成、代码逻辑不得与之冲突；冲突一律以本红线为准，宁可降级不可违规。

| # | 红线 | 违规标签 |
|---|---|---|
| 1 | 禁止暴力、恐吓、施压、骚扰、威胁类语句；禁止暗示起诉、上门、征信黑名单、通讯录曝光 | threat |
| 2 | 禁止 AI 伪造身份：法院、公安、律师、催收专员、官方机构 | fake_identity |
| 3 | 客户表达"停止致电、不要打、已投诉、拒绝沟通"，必须暂停外呼 | stop_call |
| 4 | 减免、延期、免息、分期政策只允许读取后台配置，AI 禁止编造/承诺 | fake_commit |
| 5 | 逾期规则、征信影响、法律后果只允许调取标准答案库，禁止自由解读 | legal_overstate |
| 6 | 所有对话输出必须经前置 + 后置双合规校验，拦截不通过直接返回兜底 | unverified |
| 7 | 欠款金额、逾期天数、账单记录只允许来源于数据库，禁止 AI 编造/篡改 | data_fabrication |

## 二、五层护栏架构

```
请求入站
  │
  ▼
┌─────────────────────────────────────────────────┐
│ L1 入口护栏（pre-input）                          │
│ · 外呼时段校验（08:00-21:00）                      │
│ · 停止致电标记校验                                 │
│ · 同用户每日上限校验（≤3次）                       │
│ · 并发上限校验（≤50路）                            │
│ 不通过 → A1001/A1004/A1012 拦截                   │
└─────────────────────────────────────────────────┘
  │ 通过
  ▼
┌─────────────────────────────────────────────────┐
│ L2 前置合规校验（pre-LLM）                        │
│ · 客户输入敏感词检测（AC 自动机）                  │
│ · 客户输入意图分类（是否停止致电/投诉/辱骂）        │
│ · Prompt 组装时数据脱敏（mask.py）                │
│ 不通过 → 返回兜底话术 / 转人工                     │
└─────────────────────────────────────────────────┘
  │ 通过
  ▼
┌─────────────────────────────────────────────────┐
│ L3 LLM 推理与结构化校验                           │
│ · 强制 JSON 输出（response_format json_object）   │
│ · Pydantic Schema 校验（必填/类型/枚举/长度）      │
│ · 置信度门控（confidence < 0.7 禁止直接回复）     │
│ · 熔断检测（连续3次失败 → 切 RuleBasedScriptEngine）│
│ 不通过 → 重试3次 → 走兜底                         │
└─────────────────────────────────────────────────┘
  │ 通过
  ▼
┌─────────────────────────────────────────────────┐
│ L4 后置合规校验（post-LLM）                       │
│ · LLM 输出 answer 敏感词检测（AC 自动机）          │
│ · 红线内容语义校验（暴力/伪造/编造/承诺）          │
│ · 金额/账单数据真实性校验（与 DB 比对）            │
│ 不通过 → B0010 拦截 → 兜底话术 → 不向下游传递      │
└─────────────────────────────────────────────────┘
  │ 通过
  ▼
┌─────────────────────────────────────────────────┐
│ L5 出口护栏（post-output）                        │
│ · TTS 合成前最终校验                              │
│ · 告警推送（高危 → Redis Pub-Sub system.alert.raised；**不是** qc.alert.raised）│
│ · 审计落库（llm_call_logs + audit_logs）          │
│ · 停止致电标记写入                                │
└─────────────────────────────────────────────────┘
  │
  ▼
输出给客户
```

## 三、双校验机制（核心）

### 3.1 前置校验（pre-LLM）

| 校验项 | 实现方式 | 命中处理 | 错误码 |
|---|---|---|---|
| 客户输入敏感词 | AC 自动机扫描 | 返回合规话术引导 | — |
| 停止致电意图 | 意图识别 + 关键词匹配 | 终止通话 + 写停止标记 | A1001 |
| 投诉意图 | 意图识别 | 降强度 + 转人工 | — |
| Prompt 数据脱敏 | mask.py 过滤手机号/身份证/金额 | 脱敏后送入 LLM | — |

### 3.2 后置校验（post-LLM）

| 校验项 | 实现方式 | 命中处理 | 错误码 |
|---|---|---|---|
| answer 敏感词 | AC 自动机扫描 | 拦截 + 兜底话术 | B0010 |
| 暴力/威胁内容 | 语义检测 + 关键词 | 立即转人工 + 高危告警 | — |
| 伪造身份 | 角色关键词 + 语义 | 替换为标准答案库内容 | — |
| 编造金额/账单 | 与 DB 数据比对 | 提示以坐席核实为准 | — |
| 自定义减免政策 | 关键词 + 语义 | 提示以坐席核实为准 | — |
| 法规来源校验 | 输出引用 source_id 是否存在/有效（查 kb_documents，须附原文片段） | 无 source_id 或无效 → 拦截 + 兜底话术（视为幻觉） | B0010 |

### 3.3 校验链路

```
客户输入
  → L2 前置校验（AC + 意图 + 脱敏）
    → 通过 → LLM 推理
      → L3 结构化校验（JSON + Schema + 置信度 + 熔断）
        → 通过 → L4 后置校验（AC + 语义 + 数据比对 + 法规来源校验）
          → 通过 → L5 出口护栏（TTS 前终检 + 告警 + 审计）
            → 输出客户
          → 不通过 → 兜底话术
        → 不通过 → 重试/兜底
      → 不通过 → 兜底/转人工
    → 不通过 → 兜底/转人工
```

## 四、AC 自动机敏感词匹配

### 4.1 实现规范

- 实现：`common/security/sensitive_words.py`，使用 `pyahocorasick` AC 自动机；
- 三级词库分类：

| 分类 | severity | 示例 | 处置 |
|---|---|---|---|
| threat（威胁类） | high | "上门催收"、"影响征信"、"通知通讯录" | B0010 拦截 + 转人工 |
| abuse（辱骂类） | medium | 脏话、人身攻击 | B0010 拦截 + 替换话术 |
| induce（诱导类） | low | "可以帮你减免"、"不用还了" | B0010 拦截 + 标准答案替换 |

### 4.2 词库管理

- 词库由 rag-service 维护（`sensitive_words` 表），三分类 + severity 等级；
- qc-service 启动时拉取 + 每小时增量拉取；
- 词库变更通过 Redis Pub-Sub `kb.sensitive_updated` 通知，60s 内热刷新；
- 命中即返回 B0010 拦截，违规内容不向下游传递。

### 4.3 调用方式

```python
# common/security/sensitive_words.py
class SensitiveWordFilter:
    """
    AC 自动机敏感词匹配。
    rag-service 维护词库，qc-service/call-service 各自客户端缓存。
    """
    def __init__(self):
        self._automaton: Automaton  # pyahocorasick.Automaton

    def load_words(self, words: list[dict]) -> None:
        """从 rag-service /api/v1/sensitive-words/current.dict 加载并构建自动机"""

    def scan(self, text: str) -> list[SensitiveHit]:
        """扫描文本，返回命中列表 [{word, category, severity, position}]"""

    def is_violation(self, text: str) -> bool:
        """是否存在违规内容"""
```

## 五、拦截动作矩阵

| 触发场景 | 红线 | 处置动作 | 兜底话术 | 错误码 | 告警 |
|---|---|---|---|---|---|
| 暴力/威胁/骚扰 | 红线1 | 立即转人工 + 结束通话 | "为了更好地为您服务，正在为您转接专属顾问" | B0010 | 高危告警 `system.alert.raised`（call 事中；`qc.alert.raised` 仅事后质检） |
| 伪造身份 | 红线2 | 替换为标准答案库内容 | "我是XX平台的智能助手" | B0010 | 中危告警 |
| 停止致电 | 红线3 | 终止外呼 + 写停止标记 | "好的，您的要求我们已记录" | A1001 | 记录 |
| 自定义减免承诺 | 红线4 | 提示以坐席核实为准 | "具体优惠政策请与专属顾问核实" | B0010 | 中危告警 |
| 法律后果自由解读 | 红线5 | 替换为标准答案库内容 | 标准答案库对应内容 | B0010 | 中危告警 |
| 双校验未通过 | 红线6 | 返回兜底话术 | 场景对应兜底话术 | B0010 | 记录 |
| 编造金额/账单 | 红线7 | 提示以坐席核实为准 | "具体金额请与专属顾问核实" | B0010 | 中危告警 |

> 兜底话术统一在 rag-service 标准答案库维护（category=script），禁止硬编码在业务代码中。

## 六、降级链路

### 6.1 三级降级

| 级别 | 触发条件 | 降级动作 | 依据 |
|---|---|---|---|
| L1 LLM 调用失败 | 超时/熔断/报错 | 返回预设兜底话术（RuleBasedScriptEngine） | step3/03 |
| L2 知识库检索无果 | score<0.6（精排后最终融合分数口径，见 step3/03 §一.2）或为空 | 使用标准 FAQ 兜底，不自由作答 | step3/03 |
| L3 合规拦截触发 | 前置/后置校验不通过 | 返回合规兜底话术，记告警，违规内容不向下游传递 | step3/03 |

### 6.2 降级链路

```
LLM 调用
  ├─ 成功 (confidence ≥ 0.7) → 正常回复
  ├─ confidence < 0.7 → 转人工 (A1010)
  ├─ 超时/报错 → 重试 3 次
  │    ├─ 重试成功 → 正常回复
  │    └─ 重试耗尽 → L1 降级 → RuleBasedScriptEngine 兜底
  └─ 熔断 → L1 降级 → RuleBasedScriptEngine 兜底

RAG 检索
  ├─ 成功 (score ≥ 0.6) → 正常使用
  └─ score < 0.6 → L2 降级 → 标准 FAQ 兜底

Guardrails 校验
  ├─ 通过 → 正常输出
  └─ 不通过 → L3 降级 → 合规兜底话术 + B0010
```

## 七、停止致电状态流转

```
可外呼
  │ 客户表达"停止致电/不要打/已投诉/拒绝沟通"
  ▼
停止致电 ──→ 外呼拦截（全链路拦截该客户）
  │                    │
  │                    ▼
  │              人工复核
  │              ├─ 坐席审批恢复 → 可外呼（留痕）
  │              └─ 确认投诉成立 → 永久停止
  ▼
（已投诉客户）降强度 → 投诉工单闭环后 → 恢复可外呼
```

- 停止致电标记写入客户档案 `cases.stop_call_status`（VARCHAR(20) CHECK IN ('callable','stop_requested','stopped','permanent_stop','reduced_intensity')，默认 'callable'，见 step4/01 cases DDL；状态流转经 audit_logs 留痕 who/when/why/action）；
- call-service 外呼调度与 case-service 分案分流在发呼前强制校验该标记，命中即拦截；
- 已投诉客户降低催收强度、禁止继续施压，强度恢复须等投诉工单闭环。

## 八、规则配置化

### 8.1 可配置规则

| 规则类型 | 配置表 | 说明 |
|---|---|---|
| 敏感词库 | sensitive_words | 三分类 + severity，rag-service CRUD |
| 质检规则 | qc_rules | 扣分项、阈值、模式，无需改代码 |
| 外呼策略 | call_strategies | 时段/重试/限流/兜底脚本 |
| 分案策略 | assignment_strategies | 分流规则、坐席匹配 |
| 策略版本 | strategy_versions | 版本管理 + 审批 + 回滚 |

### 8.2 规则变更流程

1. 在配置表/策略表中新增或修改规则（draft 状态）；
2. 经审批后激活（active 状态）；
3. 通过 Redis Pub-Sub 通知相关服务热加载；
4. 版本切换失败自动回退到上一 active 版本（A3006/A2010）；
5. 所有变更记录写入 audit_logs + strategy_versions。

## 九、审计追溯

### 9.1 审计表

| 表 | 用途 | 关键字段 |
|---|---|---|
| llm_call_logs | LLM 调用全量审计 | trace_id, prompt, response, confidence, status |
| audit_logs | 操作审计 | user_id, action, resource_type, detail |
| dialog_turns | 逐轮对话 | session_id, speaker, text, reasoning_encrypted, intent_label |
| qc_records | 质检结果 | session_id, compliance_score, violations, qc_result |
| etl_task_logs | ETL 任务日志 | task_name, status, records_processed |

### 9.2 追溯能力

- 所有 AI 会话、话术、研判结果、质检结果必须可追溯、可复盘、可回滚、可审计；
- 单 trace_id 可串联：用户请求 → ASR → 意图 → LLM → Guardrails → TTS → 审计日志；**质检不在 TTS 前**，通话结束后 qc 用同一 `session_id`/`trace_id` 离线串上 `qc_records`；
- `GET /api/v1/audit-logs?trace_id=` 支持单案件全流程串联查询；
- reasoning_encrypted 仅 role=admin 可解密，解密操作记 audit_logs。
- **RAG 来源溯源**：输出引用的 source_id（kb_chunks.doc_id / kb_documents.id）随 llm_call_logs.prompt 内 RAG 上下文标注与 dialog_turns 话术落库，单 trace_id 可追溯至知识库原文（对齐 step4/02 v2.3）；source_id 无着落即视为幻觉，由 §3.2 法规来源校验 B0010 拦截。

## 十、回归测试门禁

红线违规案例必须进入 LLM 回归测试集，防止模型迭代复现违规：

| 字段 | 说明 |
|---|---|
| case_id | 唯一编号 |
| 场景分类 | 对应红线 1~7 |
| 输入 | 模拟用户输入与上下文 |
| 预期拦截 | true |
| 预期标签 | threat / fake_identity / stop_call / fake_commit / legal_overstate / unverified / data_fabrication |
| 风险等级 | high / medium / low |

> CI 门禁要求红线用例拦截率 100%，任一用例未拦截即阻断发布。详见 step7/01。

## 十一、验收标准

| 验收项 | 标准 |
|---|---|
| 双校验覆盖 | 所有 AI 输出经前置 + 后置双校验，缺一不可 |
| 敏感词拦截 | AC 自动机命中即 B0010，违规内容不向下游传递 |
| 兜底话术 | 拦截后返回对应场景合规兜底话术，话术来自知识库非硬编码 |
| 降级链路 | LLM 失败 → RuleBasedScriptEngine；RAG 空 → FAQ 兜底；校验不通过 → 合规兜底 |
| 停止致电 | 客户表达停止致电后全链路拦截，写标记，外呼前强制校验 |
| 置信度门控 | confidence < 0.7 禁止直接回复，走兜底或转人工 |
| 审计追溯 | 单 trace_id 可串联全链路，llm_call_logs + audit_logs 完整 |
| 规则配置化 | 敏感词/质检规则/策略可配置变更，无需改代码，支持版本回滚 |
| 回归测试 | 红线违规用例拦截率 100%，CI 门禁阻断 |
| 日志脱敏 | 所有日志经 mask.py 脱敏，不含明文手机号/身份证/金额 |
