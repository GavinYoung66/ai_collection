# MCP 契约对齐清单（14.1 / REQ-CJ-034）

> 关闭模块 3.3/3.4 的契约确认。不改 `mcp-client` 代码。
> 全部 tool `session_id_required` = yes；统一信封 `envelope` = `{code, message, data}`。
> MCP Server 层完成 PII 脱敏（`pii_masked_at_server`）。
> **不实现真实 MCP Server。不切换 stub 网关。继续 Mock。**
> confirmed_by / confirmed_date 为 TBD 表示对接方尚未签字。

## 统一约定

| 项 | 约定 |
| --- | --- |
| session_id_required | 全部 14 tool 必传 `session_id`（全链路追踪） |
| envelope | `{code, message, data}` |
| exception_class | 业务类（可在循环上限内重试）/ 服务故障（禁止重试、降级人工）/ 权限拒绝（禁止重试、安全审计） |
| pii_masked_at_server | yes（禁止明文 PII 回 Agent） |

## 清单

| server | prd_alias | tool | request_fields | response_fields | exception_class | session_id_required | envelope | pii_masked_at_server | mock_aligned | confirmed_by | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| business_ticket | business-ticket | query_case_info | case_id:str, session_id:str | case_id, overdue_amount, overdue_days, repayment_records, customer_name, product_type, create_time | 业务类：案件不存在 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | 待 MCP Server 团队确认字段类型与金额单位 |
| business_ticket | business-ticket | query_history_tickets | case_id:str, session_id:str, limit:int=20 | list[{ticket_id, type, content, create_time, status}] | 业务类：无历史工单→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | type/status 枚举待确认 |
| business_ticket | business-ticket | query_history_complaints | case_id:str, session_id:str, limit:int=10 | list[{complaint_id, content, verdict, create_time}] | 业务类：无历史投诉→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | verdict 须为四值、禁止 normal |
| business_ticket | business-ticket | query_user_history_complaints | user_id:str, days:int=90, session_id:str | list[{complaint_id, case_id, content, verdict, create_time}] | 业务类：无历史投诉→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | 90 天窗口是否可配待确认 |
| business_ticket | business-ticket | get_complaint_ticket | complaint_id:str, session_id:str | complaint_id, user_id, case_ids, complaint_text, submit_time, source_channel | 业务类：工单不存在 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | P1.2 漏列，须纳入 Server 实现 |
| call_qc | call-qc | query_call_asr | case_id:str, session_id:str | list[{call_session_id, call_start_time, call_duration, asr_text_with_timestamp, agent_role}] | 业务类：无通话→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | ASR 时间戳粒度待确认 |
| call_qc | call-qc | query_qc_violations | case_id:str, session_id:str | list[{qc_id, violation_type, violation_rule_id, call_session_id, start_ts, end_ts, snippet_text, score}] | 业务类：无违规→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | violation_type 枚举对齐 qc-service |
| call_qc | call-qc | query_call_sessions | case_id:str, session_id:str | list[{call_session_id, call_time, agent_id, call_duration, call_result}] | 业务类：无通话→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | P1.2 漏列 |
| rag_kb | rag-kb | search_regulations | query:str, top_k:int=5, session_id:str | list[{doc_id, title, content_snippet, source_type, score, source_url}] | 业务类：无匹配→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | 空结果须降级规则兜底（研判侧已规划，本批不改图） |
| rag_kb | rag-kb | search_response_templates | verdict:str, complaint_type:str=general, session_id:str | list[{template_id, title, template_text, doc_id, applicable_scenarios}] | 业务类：无匹配→通用模板 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | verdict 四值约束 |
| rag_kb | rag-kb | search_complaint_handling_guidelines | query:str, top_k:int=3, session_id:str | list[{guideline_id, title, content, doc_id, score}] | 业务类：无匹配→空数组 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | P1.2 漏列 |
| data_platform | data-platform-writeback | write_session | session_data:dict（完整 session 序列化）, session_id:str | success:bool, write_id:str | 服务故障：写入失败→自动重试3次 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | session_data schema 版本待确认 |
| data_platform | data-platform-writeback | write_human_review_truth | complaint_id:str, human_review:dict, session_id:str | success:bool | 服务故障：写入失败→自动重试3次 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | human_review 字段对齐 14.4 复核动作 |
| data_platform | data-platform-writeback | push_rag_candidate | case_data:dict, quality_score:float, session_id:str | success:bool, submitted:bool | 服务故障：推送失败→告警，不阻塞主流程 | yes | `{code, message, data}` | yes | 本仓无 Mock 实现（仅 health stub） | TBD | TBD | P1.2 漏列；quality_score 门槛 TBD |

## 错误分级（对照 DD-05 §4.3）

| exception_class | 场景 | 处置 | 重试 |
| --- | --- | --- | --- |
| 业务类 | 查无案件、无通话、无匹配 | 标记缺失 / 空数组 | 循环上限内可重试 |
| 服务故障 | 超时、连接失败 | 标记异常、转人工 | 禁止重试（写回类除外：可重试3次） |
| 权限拒绝 | 鉴权失败、越权 | 终止自动流程、安全审计 | 禁止重试 |

服务故障与权限拒绝时，调用方按 MCP Client 已规划的 `MCPTimeoutError` / `MCPError` 处置；本批不实现 Client。
