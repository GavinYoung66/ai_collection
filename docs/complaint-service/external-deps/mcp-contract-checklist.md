# MCP 契约清单（个人项目 · Ellis Guo）

信封 `{code, message, data}`；tool 必传 `session_id`；PII Server 侧脱敏。  
确认人：**Ellis Guo** · 日期：2026-09-07 · 本地以 Mock 对齐为准。

| request_fields | response_fields | exception_class | tool | session_id_required | envelope | pii_masked_at_server | mock_aligned | confirmed_by | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| case_id, session_id | case_id, overdue_amount, overdue_days, repayment_records, customer_name, product_type, create_time | 业务类：案件不存在 | query_case_info | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_id, session_id, limit=20 | list[{ticket_id, type, content, create_time, status}] | 无历史→空数组 | query_history_tickets | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_id, session_id, limit=10 | list[{complaint_id, content, verdict, create_time}] | 无历史→空数组 | query_history_complaints | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| user_id, days=90, session_id | list[{complaint_id, case_id, content, verdict, create_time}] | 无历史→空数组 | query_user_history_complaints | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| complaint_id, session_id | complaint_id, user_id, case_ids, complaint_text, submit_time, source_channel | 业务类：工单不存在 | get_complaint_ticket | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_id, session_id | list[{call_session_id, call_start_time, call_duration, asr_text_with_timestamp, agent_role}] | 无通话→空数组 | query_call_asr | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_id, session_id | list[{qc_id, violation_type, violation_rule_id, call_session_id, start_ts, end_ts, snippet_text, score}] | 无违规→空数组 | query_qc_violations | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_id, session_id | list[{call_session_id, call_time, agent_id, call_duration, call_result}] | 无通话→空数组 | query_call_sessions | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| query, top_k=5, session_id | list[{doc_id, title, content_snippet, source_type, score, source_url}] | 无匹配→空数组 | search_regulations | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| verdict, complaint_type=general, session_id | list[{template_id, title, template_text, doc_id, applicable_scenarios}] | 无匹配→通用模板 | search_response_templates | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| query, top_k=3, session_id | list[{guideline_id, title, content, doc_id, score}] | 无匹配→空数组 | search_complaint_handling_guidelines | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-08 | **reserved-unused**：图节点未调用；真路径返回 [] |
| session_data(dict), session_id | success, write_id | 服务故障：失败重试3次 | write_session | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| complaint_id, human_review, session_id | success | 服务故障：失败重试3次 | write_human_review_truth | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
| case_data, quality_score, session_id | success, submitted | 推送失败→告警不阻塞 | push_rag_candidate | yes | {code, message, data} | yes | yes | Ellis Guo | 2026-09-07 | 无（本地 Mock） |
