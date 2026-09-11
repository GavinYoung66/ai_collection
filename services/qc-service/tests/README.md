# qc-service M1 测试说明

## 收集范围

`services/qc-service/tests` 覆盖离线质检 M1。断言 1–14 见 `test_assertions.py`；断言 15–28 见 `test_assertions_15_28.py`；引擎核心见 `test_engine.py`。

## QC-11.4 明确跳过（不实现这些能力）

下列能力 **不是 M1 必过项**。收集 M1 测试集时，会调用 `/kb/search` 或断言 `guardrail.pass` 的用例必须 `pytest.mark.skip` 或不存在。标记见 `test_m2_skipped.py`。

| 跳过项 | 说明 |
| :--- | :--- |
| RAG 标准答案检索 | 不测 `POST /api/v1/kb/search`；M1 `QC_R14_RAG_ENABLED=false`；RagClient 无 search |
| 红线 6 | 不测 call 护栏放行日志 `audit_logs.action=guardrail.pass`；缺记录不得用 unverified 充数 |
| 实时 WS | 不测 `WS /qc/check/stream`；M1 无实时质检 |

红线 6 **非 M1 必过**。禁止为「跳过」去实现 `/kb/search` 客户端、guardrail.pass 读取器或 WS 模块。
