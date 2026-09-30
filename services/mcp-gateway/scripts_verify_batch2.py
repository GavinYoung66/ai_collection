"""Batch 2 验收脚本：list_tools 全量 + 每工具 call_tool + 未知 server 报错。"""
import asyncio
import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

GATEWAY = "http://localhost:9100/mcp"
CASE_ID = "11111111-2222-4333-8444-555555555501"  # min-loop 案件
CALL_SESSION = "min-loop-sess-001"


async def main() -> int:
    async with httpx.AsyncClient(timeout=30.0) as http_client:
      async with streamable_http_client(GATEWAY, http_client=http_client) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = await s.list_tools()
            by_server = {}
            for t in tools.tools:
                server, _, tool = t.name.partition(".")
                by_server.setdefault(server, []).append(tool)
            print("== list_tools ==")
            for server in ("rag_kb", "business_ticket", "data_platform", "call_qc"):
                print(f"  {server}: {sorted(by_server.get(server, []))}")
            expected = {
                "rag_kb": {"search_regulations", "search_response_templates"},
                "business_ticket": {"query_case_info", "query_history_tickets", "query_history_complaints"},
                "data_platform": {"write_session", "write_human_review_truth"},
                "call_qc": {"query_call_asr", "query_qc_violations", "query_qc_alerts"},
            }
            for server, want in expected.items():
                got = set(by_server.get(server, []))
                assert got == want, f"{server} tools mismatch: {got} != {want}"
            print("  list_tools 全量 OK")

            async def call(name, args, expect="list"):
                res = await s.call_tool(name, args)
                if res.isError:
                    return {"__error__": res.content[0].text if res.content else "?"}
                sc = res.structuredContent
                if isinstance(sc, dict):
                    payload = sc.get("result", sc)
                    return payload
                # FastMCP list[dict] 返回：N 个 text block（每块一个 JSON dict）；
                # dict 返回：1 个 text block。按 expect 解析。
                blocks = [b for b in (res.content or []) if getattr(b, "type", "") == "text"]
                parsed = []
                for b in blocks:
                    try:
                        parsed.append(json.loads(b.text))
                    except ValueError:
                        return {"__raw__": b.text}
                if expect == "dict":
                    return parsed[0] if parsed else {}
                if len(parsed) == 1 and isinstance(parsed[0], list):
                    return parsed[0]
                return parsed

            print("== call_tool ==")
            r1 = await call("rag_kb.search_regulations", {"query": "威胁恐吓催收", "top_k": 3})
            print(f"  rag_kb.search_regulations: {len(r1)} hits, keys={sorted(r1[0].keys()) if r1 else '-'}")
            assert isinstance(r1, list) and r1 and "doc_id" in r1[0]

            r2 = await call("rag_kb.search_response_templates", {"query": "催收话术", "top_k": 3})
            print(f"  rag_kb.search_response_templates: {len(r2)} hits, has_template={'template_text' in r2[0] if r2 else '-'}")

            r3 = await call("business_ticket.query_case_info", {"case_id": CASE_ID}, expect="dict")
            print(f"  business_ticket.query_case_info: keys={sorted(r3.keys())}")
            assert set(r3) == {"case_id", "overdue_amount", "overdue_days", "repayment_records",
                               "customer_name", "product_type", "create_time"}, r3

            r4 = await call("business_ticket.query_history_tickets", {"case_id": CASE_ID})
            r5 = await call("business_ticket.query_history_complaints", {"case_id": CASE_ID})
            print(f"  history_tickets={r4}, history_complaints={r5}")
            assert r4 == [] and r5 == []

            r6 = await call("call_qc.query_call_asr", {"case_id": CASE_ID})
            print(f"  call_qc.query_call_asr: {len(r6)} items")
            if r6:
                keys = sorted(r6[0].keys())
                print(f"    keys={keys}, turns={len(r6[0]['asr_text_with_timestamp'])}, duration={r6[0]['call_duration']}")
                assert "asr_text_with_timestamp" in r6[0] and r6[0]["call_duration"] > 0
                speakers = {t["speaker"] for t in r6[0]["asr_text_with_timestamp"]}
                assert speakers <= {"agent", "user"}, speakers
                print(f"    speakers={sorted(speakers)}")

            r7 = await call("call_qc.query_qc_violations", {"case_id": CASE_ID, "session_id": CALL_SESSION})
            print(f"  call_qc.query_qc_violations: {len(r7)} items, keys={sorted(r7[0].keys()) if r7 else '-'}")
            assert r7 and "qc_record_id" in r7[0] and "violation_rule_id" in r7[0]

            r8 = await call("call_qc.query_qc_alerts", {"case_id": CASE_ID, "session_id": CALL_SESSION})
            print(f"  call_qc.query_qc_alerts: {len(r8)} items, keys={sorted(r8[0].keys()) if r8 else '-'}")
            assert r8 and "qc_record_id" in r8[0] and "alert_id" in r8[0]

            r9 = await call("data_platform.write_session", {"session_id": "verify-nonexistent-sess"}, expect="dict")
            print(f"  data_platform.write_session(resolve-miss): {r9}")
            assert r9 == {"success": True, "persisted": False}

            r10 = await call("data_platform.write_session", {
                "session_id": "verify-nonexistent-sess",
                "case_id": "00000000-0000-4000-8000-000000000099",
                "complaint_id": "00000000-0000-4000-8000-000000000099",
                "task_status": "resolved",
                "verdict": "approved",
            }, expect="dict")
            print(f"  data_platform.write_session(snapshot-404→enqueue): {r10}")
            assert r10.get("success") is True and r10.get("persisted") is False

            r11 = await call("data_platform.write_human_review_truth", {
                "session_id": "verify-nonexistent-sess",
                "case_id": "00000000-0000-4000-8000-000000000099",
                "complaint_id": "00000000-0000-4000-8000-000000000099",
                "task_status": "resolved",
                "verdict": "approved",
            }, expect="dict")
            print(f"  data_platform.write_human_review_truth: {r11}")
            assert r11.get("success") is True

            print("== unknown server/tool ==")
            r12 = await call("unknown_server.any_tool", {})
            print(f"  MCP 层未知工具: {str(r12)[:120]}")
            assert "__error__" in r12

            # dispatch 层未知 server（直接进程内调用验证）
            sys.path.insert(0, ".")
            from app.mcp_server import UnknownServerError, UnknownToolError, dispatch
            try:
                await dispatch("unknown_server", "x", {})
                raise AssertionError("should raise")
            except UnknownServerError as e:
                print(f"  dispatch 未知 server: {e}")
            try:
                await dispatch("rag_kb", "no_such_tool", {})
                raise AssertionError("should raise")
            except UnknownToolError as e:
                print(f"  dispatch 未知 tool: {e}")

    print("\nALL CHECKS PASSED")
    return 0


sys.exit(asyncio.run(main()))
