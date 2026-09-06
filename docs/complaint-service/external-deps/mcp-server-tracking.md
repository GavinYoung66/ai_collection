# MCP Server 进度跟踪表（14.1 / REQ-CJ-034）

> 权威 Server 名用下划线；`prd_alias` 对照 PRD 连字符名。
> **不实现真实 MCP Server。不切换 stub 网关。编码侧继续 Mock MCP。**
> TBD ≠ 已确认。`ready_for_switch` 不授权本批切流量。
> last_updated: 2026-09-06

## 总览

| server | prd_alias | tools | owner_team | 域状态 |
| --- | --- | --- | --- | --- |
| business_ticket | business-ticket | 5 | 传统催收业务系统团队 + MCP Server 团队 | not_started |
| call_qc | call-qc | 3 | qc-service 团队 | not_started |
| rag_kb | rag-kb | 3 | rag-service 团队 | not_started |
| data_platform | data-platform-writeback | 3 | data-platform 团队 | not_started |

合计 **14** tool。本仓 `services/complaint-service` 仅为 `/health` stub，无 Mock MCP Client 实现；缓解策略仍是编码侧先 Mock、Server 就绪后再切。

## 进度表

| server | prd_alias | tool | owner_team | status | blocker | target_date | last_updated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| business_ticket | business-ticket | query_case_info | 传统催收业务系统团队 + MCP Server 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| business_ticket | business-ticket | query_history_tickets | 传统催收业务系统团队 + MCP Server 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| business_ticket | business-ticket | query_history_complaints | 传统催收业务系统团队 + MCP Server 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| business_ticket | business-ticket | query_user_history_complaints | 传统催收业务系统团队 + MCP Server 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| business_ticket | business-ticket | get_complaint_ticket | 传统催收业务系统团队 + MCP Server 团队 | not_started | 真实 MCP Server 未交付；P1.2 曾漏列 | TBD | 2026-09-06 |
| call_qc | call-qc | query_call_asr | qc-service 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| call_qc | call-qc | query_qc_violations | qc-service 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| call_qc | call-qc | query_call_sessions | qc-service 团队 | not_started | 真实 MCP Server 未交付；P1.2 曾漏列 | TBD | 2026-09-06 |
| rag_kb | rag-kb | search_regulations | rag-service 团队 | not_started | 真实 MCP Server 未交付；法规入库见 14.2 | TBD | 2026-09-06 |
| rag_kb | rag-kb | search_response_templates | rag-service 团队 | not_started | 真实 MCP Server 未交付；范本入库见 14.2 | TBD | 2026-09-06 |
| rag_kb | rag-kb | search_complaint_handling_guidelines | rag-service 团队 | not_started | 真实 MCP Server 未交付；P1.2 曾漏列 | TBD | 2026-09-06 |
| data_platform | data-platform-writeback | write_session | data-platform 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| data_platform | data-platform-writeback | write_human_review_truth | data-platform 团队 | not_started | 真实 MCP Server 未交付 | TBD | 2026-09-06 |
| data_platform | data-platform-writeback | push_rag_candidate | data-platform 团队 | not_started | 真实 MCP Server 未交付；P1.2 曾漏列 | TBD | 2026-09-06 |

## 切换边界

- 本批**不实现真实 MCP Server**。
- 本批**不切换 stub 网关**（prod 编排若存在 mcp-gateway stub，保持 stub）。
- 编码侧继续 **Mock**；`ready_for_switch` 出现后另开联调变更，不在批次12。
