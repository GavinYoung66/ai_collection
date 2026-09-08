# Tasks: platform-arch-unification-v5

Issue map（原 A–G 字母）:
- **A** ARCH-01/06 TECH-01/02 COMP-04
- **B** DEP-01/02/03 DEBT-04
- **C** ARCH-02/03 PLAT-01/02
- **F**（本 change 执行）DEBT-01/02/03/05 ARCH-05 DEP-04
- **E 轻量** DEP-05 checklist only（非 ARCH-04/COMP-07）
- **Stretch** ENG-01（D 最小切片）· COMP-03（非 A–G 必选）
- **V5.1 / G**：见 proposal Non-Goals

环境：conda `ai_collection`；仓库根执行。

---

## 1. Batch-A — 叙事与口径

- [x] 1.1（ARCH-01）更新 `requirements_document/step3_架构设计/01_整体技术架构*.md`：RAG 归 `rag-service(8002)`，common 不含 RAG 产品；验证：`rg -n "公共能力|RAG|common" requirements_document/step3_架构设计/01*.md` 人工确认无「RAG∈common」
- [x] 1.2（ARCH-06）更新 step3 `02*`（若有 Agent 直连 HTTP 歧义）与 README / step1：改为「2 深切片 + 5 占位」；验证：`rg -n "深切片|占位|脚手架|完备" README.md requirements_document/step1_项目总览/*.md | head`
- [x] 1.3（TECH-01/02）在 step1 或 step3 增加**容量模型表**（外呼并发 ≈50 vs 质检/转写吞吐另计或对齐）；禁止未标注的「10万工单/10万篇」作已验证事实；验证：表存在 + `rg -n "50|100|并发|容量" requirements_document/step1_项目总览 requirements_document/step3_架构设计 | head` 且 `rg -n "10万工单|10万篇" … || true` 无未加免责声明命中
- [x] 1.4（COMP-04）`docs/complaint-service/` 写明 V5 Demo 必须 `run_graph=true` + human_review；验证：`rg -n "run_graph|human_review" docs/complaint-service/`
- [x] 1.5（R11）检查 `openspec/specs/01_arch_constraint.md` 与归档主规范 `platform/integration-boundaries` 是否需一句对齐 RAG/分层表述；若需则补一句（不削弱强制 6/8）；验证：人工 diff 或 `rg -n "transport|MCP|RAG" openspec/specs/01_arch_constraint.md`

---

## 2. Batch-C 骨架 — reliability + guardrails（含 PLAT）

- [x] 2.1（ARCH-02）新增 `common/llm/reliability.py` 并导出；验证：约定符号可 import + 测试可收集
- [x] 2.2 reliability 单测（成功清零、阈值 degrade、前缀隔离）；验证：`pytest common/tests/test_llm_reliability.py -q`
- [x] 2.3（ARCH-03）新增 `common/guardrails/` `precheck_text`/`postcheck_text`；验证：`pytest common/tests/test_guardrails.py -q`
- [x] 2.4 更新 `common/llm/__init__.py` 导出；验证：`python -c "from common.llm import post_chat_completions; import common.llm.reliability"`
- [x] 2.5（PLAT-01/02 / D9）在 `common/llm/provider.py` 或 `docs/platform/` 短注：`chat/chat_json` 为遗留薄封装；Redis degrade 为多实例权威；验证：`rg -n "legacy|遗留|Redis|degrade" common/llm/provider.py docs/platform/ 2>/dev/null | head`

---

## 3. Batch-C + F — 双服务接入与债务

- [x] 3.1（ARCH-02）**rag-service 与 complaint-service 各至少一处** LLM HTTP 路径改用 reliability helpers（禁止只改一侧）；验证：两侧源码均 `rg reliability` 有命中 + 相关单测通过
- [x] 3.2（ARCH-03）rag `llm_service` answer 组合 common guardrails；验证：`pytest services/rag-service/tests/test_llm_answer.py -q`（或最相关用例）
- [x] 3.3（ARCH-03）complaint L4/`guardrails_postcheck` 组合 common guardrails；验证：`pytest services/complaint-service/tests -k guardrail -q`
- [x] 3.4（DEBT-01/05）RealLLMTransport：业务 system prompt 迁出；厂商 URL/Key 配置化；另对业务节点做硬编码门禁：`rg -n "api\\.deepseek|dashscope\\.aliyuncs|httpx\\.AsyncClient\\(" services/complaint-service/app services/rag-service/app --glob '!**/settings*.py' --glob '!**/rag_settings.py'` 人工确认无新增裸客户端；验证：transport 无大段业务 prompt + 上述 rg 可解释
- [x] 3.5（DEBT-02 / DEBT-04 联动）`change_me` 占位：真 LLM/真 MCP fail-closed；验证：单测 placeholder → 拒绝
- [x] 3.6（ARCH-05）`app/settings.py` 显式 `rag_settings`/`platform_settings` + proxy deprecate；验证：`pytest services/rag-service/tests/test_rag_settings.py -q`
- [x] 3.7（DEBT-03）删除或隔离 `services/rag-service/common/`，统一根 `common/`；验证：目录不存在或含禁止说明 + `rg -n "from rag.common|services\\.rag-service\\.common" services/rag-service || true` 无非法 import

---

## 4. Batch-B — MCP real path + bridge

- [x] 4.1（DEP-02）实现 gateway standalone（或等价），去掉支持工具上的 NotImplemented；验证：单测 tool call 返回 dict
- [x] 4.2（DEP-01）`rag_kb` bridge → `POST /api/v1/kb/search`；验证：httpx mock 契约测含 `doc_id`（或约定键）
- [x] 4.3（DEBT-04）`USE_MOCK_MCP=false` 缺配置 fail-fast；验证：单测 missing URL/placeholder
- [x] 4.4（DEP-03）compose：mock 不硬依赖 rag；真路径注释/依赖说明；验证：`rg -n "depends_on|rag-service|Mock MCP" docker-compose.dev.yml docker-compose.complaint.yml`
- [x] 4.5（DEP-04）`search_complaint_handling_guidelines` reserved-unused 或最小挂接；验证：`rg -n "search_complaint_handling_guidelines|reserved" docs/complaint-service services/complaint-service`
- [x] 4.6 更新 `docs/complaint-service/external-deps/` Mock vs Real；验证：含 `USE_MOCK_MCP` 与 Real 章节

---

## 5. Batch-E 轻量（DEP-05 only）+ 卫生

- [x] 5.1（DEP-05）新增 `docs/platform/new-service-checklist.md`（MCP/HTTP/事件、Settings、错误码、common 必用）；验证：文件存在且含上述四节标题
- [x] 5.2（R10）删除空 `openspec/changes/complaint-service-v5/` **或** 在其目录写入 `OBSOLETE.md` 指向本 change；验证：空 change 不再可被误认为活跃规划

---

## 6. V5 验收门禁

- [x] 6.1 新增 `docs/complaint-service/v5-acceptance.md`（或 scripts）：mock health、fail-fast、可选 real smoke、文档 grep（含容量模型关键词）；验证：按文档跑通 mock + fail-fast
- [x] 6.2 Mock 回归：`pytest services/complaint-service/tests/test_health.py services/complaint-service/tests/test_mock_batch0.py -q`（路径以仓库为准）
- [x] 6.3 common 新测：`pytest common/tests/test_llm_transport.py common/tests/test_llm_reliability.py common/tests/test_guardrails.py -q`
- [x] 6.4（R12）规划完整性：`openspec status --change platform-arch-unification-v5` 显示 proposal/specs/design/tasks 均为 done（**不要**使用不存在的 `openspec validate --change`）

---

## 7. Stretch（不阻断；非 Batch-D 完成）

- [x] 7.1（ENG-01）MCP/LLM call_log 可插拔 sink（最小 tempfile）；验证：单测 sink 被调用。**说明：不等于 ENG-02/07、COMP-01/02**
- [x] 7.2（COMP-03）API/Demo 暴露 evidence_refs/`doc_id`；验证：schema/API 断言。**说明：非原 A–G 必选，面试加分**

---

## 8. 收口

- [x] 8.1 自检：proposal 覆盖表与本 tasks 字母一致；P0（A/B/C/F + DEP-05 清单 + DEBT-03）均有任务；验证：人工勾选对照
- [x] 8.2 P2 记录确认：Batch-G 与 ARCH-04/COMP-07/ENG-02/07/COMP-01/02 仍在 Non-Goals，本 change 不实现；验证：proposal Non-Goals 含上述 ID
