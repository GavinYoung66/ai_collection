"""Mock MCP Client：覆盖 PRD §4.4 的 4 Server × 14 tool。

支持 success / timeout / error 三模式；从 fixture JSON 加载返回。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from tests.mock.mcp.errors import MCPError, MCPTimeoutError

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# PRD §4.4 权威 14 tool 清单
SERVERS = {
    "business_ticket": [
        "query_case_info",
        "query_history_tickets",
        "query_history_complaints",
        "query_user_history_complaints",
        "get_complaint_ticket",
    ],
    "call_qc": [
        "query_call_asr",
        "query_qc_violations",
        "query_call_sessions",
    ],
    "rag_kb": [
        "search_regulations",
        "search_response_templates",
        "search_complaint_handling_guidelines",
    ],
    "data_platform": [
        "write_session",
        "write_human_review_truth",
        "push_rag_candidate",
    ],
}

ALL_TOOLS = {tool for tools in SERVERS.values() for tool in tools}


class MockMCPClient:
    """Mock MCP Client。"""

    def __init__(self):
        # tool -> mode: success | timeout | error
        self._modes: dict[str, str] = {}
        self._call_log: list[dict] = []

    def set_mode(self, tool: str, mode: str) -> None:
        """设置某个 tool 的调用模式：success | timeout | error。"""
        if mode not in ("success", "timeout", "error"):
            raise ValueError(f"unsupported mode: {mode}")
        if tool not in ALL_TOOLS:
            raise ValueError(f"unknown tool: {tool}")
        self._modes[tool] = mode

    def reset_mode(self, tool: Optional[str] = None) -> None:
        if tool is None:
            self._modes.clear()
        else:
            self._modes.pop(tool, None)

    @property
    def call_log(self) -> list[dict]:
        return list(self._call_log)

    async def call(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> dict:
        """调用 MCP tool（签名对齐 app.mcp.client.MCPClient.call）。

        Args:
            server: MCP Server 名（business_ticket / call_qc / rag_kb / data_platform）
            tool: tool 名
            params: 入参
            trace_id: 全链路追踪 ID（审计用，Mock 记入 call_log）
            session_id: 会话 ID（审计用；亦可写在 params 内）

        Returns:
            dict 格式的返回数据
        """
        if tool not in ALL_TOOLS:
            raise MCPError(server, tool, f"unknown tool")

        self._call_log.append(
            {
                "server": server,
                "tool": tool,
                "params": params,
                "trace_id": trace_id,
                "session_id": session_id or params.get("session_id", ""),
            }
        )

        mode = self._modes.get(tool, "success")
        if mode == "timeout":
            raise MCPTimeoutError(server, tool)
        if mode == "error":
            raise MCPError(server, tool, "mock error mode")

        return self._load_fixture(server, tool, params)

    def _load_fixture(self, server: str, tool: str, params: dict) -> dict:
        # 优先尝试带场景的 fixture（multi_case / repeat / corrupted 等）
        scenario = self._detect_scenario(tool, params)
        candidates = [
            f"{server}_{tool}_{scenario}.json",
            f"{server}_{tool}_normal.json",
            f"{server}_{tool}.json",
        ]
        for name in candidates:
            path = _FIXTURES_DIR / name
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        raise MCPError(server, tool, f"no fixture found for {server}.{tool}")

    def _detect_scenario(self, tool: str, params: dict) -> str:
        """根据入参推断场景，用于匹配带场景的 fixture。"""
        case_id = str(params.get("case_id") or "")
        v2 = self._v2_scenario_from_case_id(case_id)

        if tool in ("query_case_info", "query_history_tickets", "query_history_complaints"):
            case_ids = params.get("case_ids")
            if isinstance(case_ids, list) and len(case_ids) >= 3:
                return "multi_case"
            if v2:
                return v2
        if tool == "query_user_history_complaints":
            return "repeat"
        if tool in ("query_call_asr", "query_qc_violations", "query_call_sessions"):
            if params.get("scenario") == "corrupted" or params.get("asr_corrupted") is True:
                return "corrupted"
            if v2:
                return v2
            return "normal"
        if tool in (
            "search_regulations",
            "search_response_templates",
            "search_complaint_handling_guidelines",
        ):
            q = str(params.get("query") or "")
            rag_v2 = self._v2_scenario_from_query(q)
            if rag_v2:
                return rag_v2
            return "rag_match"
        return "normal"

    @staticmethod
    def _v2_scenario_from_case_id(case_id: str) -> str | None:
        """V2 种子 case_id → fixture 场景后缀。"""
        cid = (case_id or "").lower()
        if "v2-agent" in cid or "v2_agent" in cid:
            return "v2_agent_violation"
        if "v2-misunderstand" in cid or "v2_misunderstand" in cid or "v2-mis" in cid:
            return "v2_user_misunderstanding"
        if "v2-poor" in cid or "v2_poor" in cid:
            return "v2_poor_experience"
        return None

    @staticmethod
    def _v2_scenario_from_query(query: str) -> str | None:
        """用投诉文案关键词推断 RAG 场景（节点传 query=complaint_text）。"""
        q = query or ""
        if any(k in q for k in ("威胁", "上门", "半夜", "同事", "单位")):
            return "v2_agent_violation"
        if any(k in q for k in ("利息", "结清", "多收", "计息")):
            return "v2_user_misunderstanding"
        if any(k in q for k in ("语气", "生硬", "打断", "敷衍", "体验")):
            return "v2_poor_experience"
        return None
