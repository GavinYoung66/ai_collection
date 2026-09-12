## 1. 引擎与 hits 字段

- [x] 1.1 改 `ScoringService`：可计分扣分>0 → `PENDING_REVIEW`/`pending_review`，=0 → `DONE`/`compliant`；更新原先「中低危 DONE」单测并验证 pytest `test_offline_qc` / `test_engine` 中相关断言通过
  验证：`PYTHONPATH="services/qc-service" python -m pytest services/qc-service/tests/test_offline_qc.py services/qc-service/tests/test_engine.py services/qc-service/tests/test_merge_hits.py services/qc-service/tests/test_alerts.py services/qc-service/tests/test_assertions.py services/qc-service/tests/test_assertions_15_28.py services/qc-service/tests/test_r10.py`
- [x] 1.2 Alembic 为 `qc_violations` 增加可空 `source`、`word`、`kb_snippet`（或等价列），ORM 与 GET/replay hits 序列化 `source`/`word`/条款；验证迁移升级可跑且详情 JSON 含新键
  验证：`PYTHONPATH="services/qc-service" python -m pytest services/qc-service/tests/test_migration_0012.py services/qc-service/tests/test_records.py services/qc-service/tests/test_replay_api.py services/qc-service/tests/test_models.py`

## 2. 演示灌数 API

- [x] 2.1 实现 `POST /api/v1/qc/demo/seed`：插入 cases/call_records/dialog_turns（speaker 仅 `ai|user`），三条 `data_materials` 场景、每次新 ID、不插 qc_records、可选返回 QC JWT 与种子 users；验证连续两次 seed 共 6 个不同 session_id 且无 qc_records
  验证：`PYTHONPATH="services/qc-service" python -m pytest services/qc-service/tests/test_demo_seed.py -q`
- [x] 2.2 实现 `GET /api/v1/qc/demo/pending`（Redis 登记 ∩ 无有效 qc_records）；验证 seed 后 3 条可见、replay 成功后该 session 离开 pending
  验证：`PYTHONPATH="services/qc-service" python -m pytest services/qc-service/tests/test_demo_seed.py -q`

## 3. 检索、词库与 compose

- [x] 3.1 `RagClient` 增加 `POST /api/v1/kb/search`；`QC_R14_RAG_ENABLED=true` 时管道调用，失败回退关键词且不 PARTIAL；验证单测：开关 true 有 search 调用，超时仍出分
  验证：`PYTHONPATH="services/qc-service" python -m pytest services/qc-service/tests/test_rag_client.py services/qc-service/tests/test_engine.py services/qc-service/tests/test_rule_engine.py services/qc-service/tests/test_kb_search.py services/qc-service/tests/test_offline_qc.py -q`
- [x] 3.2 compose/env：`QC_MOCK_LLM=false`、`QC_R14_RAG_ENABLED=true`、`QC_RAG_SENSITIVE_BASE` 指向 rag ~50 词 dict；提供 `data_materials` 入库步骤或脚本；验证 qc health `rag_connected` 且不再默认 7 词 mock
  验证：grep `docker-compose.dev.yml` `.env.example` 三开关；入库脚本 `scripts/qc/ingest_demo_materials.py`（本轮未 curl 真 health）

## 4. 时间线、CORS、人审契约

- [x] 4.1 管道步骤写入 Redis `qc:demo:trace:{session_id}`，实现 `GET /api/v1/qc/demo/{session_id}/trace`（可选 `langsmith_url`）；验证无 Key 时 HTTP 200 且 events 非 5xx，replay 后含取源/AC/规则/LLM/search/打分可区分项
- [x] 4.2 CORS 放行 localhost:8080 与 127.0.0.1:8080（可选 5500）；验证预检或带 Origin 请求不被拒
- [x] 4.3 确认 `POST /reviews/{id}` 的 CORRECT+手填分路径；演示文档写明前端不暴露 DISMISS；验证 CORRECT 100 → REVIEWED 且分数 100，缺分 A2008

## 5. 静态前端

- [x] 5.1 新增 `services/qc-service/web/`（index.html/app.js/styles.css/README）：灌数按钮、列表、待检点开检走 replay/{id}、已出分不重跑、健康条、token；验证 `python -m http.server 8080` 可打开且请求 8003
- [x] 5.2 详情：分数/命中表（AC|规则|LLM、扣分、条款、skipped 未审文案）、零扣分无人审、有扣分 CONFIRM/误报/改分（后两者必填 0–100）、REVIEWED 禁用、时间线与可选 LangSmith；验证合规通 100 无人审按钮、待复核误报未填分无法提交

## 6. 浏览器验收

- [x] 6.1 按 README 走通：灌数两次不撞 ID → 点 A 得 100 DONE → 点 B 或 C 有扣分+AC 或规则+条款（检索失败则回退仍出分）→ 手填分 REVIEWED → 时间线可见 DP/词库/LLM/search；真 LLM 至少一条停呼后仍催或暗示；记录步骤通过，全程无需 curl
