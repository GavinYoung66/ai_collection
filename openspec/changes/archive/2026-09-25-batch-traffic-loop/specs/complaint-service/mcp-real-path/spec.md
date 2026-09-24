## MODIFIED Requirements

### Requirement: business_ticket 进程内桥

complaint-service SHALL 在进程内为研判图调用的 `business_ticket` 工具补真桥，图节点仍只调 `mcp.call`，SHALL NOT 直接 import 其它服务。桥行为：
- `query_case_info`：用 case_id 向 data-platform 快照接口 `GET /api/v1/data/snapshot/{case_id}` 查数据；快照查不到时 fallback 读 `public.cases` 同一行。返回形状对齐现有 fixture（`case_id`、`overdue_amount`、`overdue_days`、`customer_name`、`product_type`、`create_time`；`repayment_records` 允许空数组）。桥 SHALL 从快照行的 `product_type`、`repayment_records`、`create_time` 三列读取实际业务值并填入返回，SHALL NOT 在这三字段固定填空串或空列表；仅当快照行对应列确为空（NULL）时才允许返回空值。客户名保持脱敏
- `query_history_tickets`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表
- `query_history_complaints`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表

#### Scenario: query_case_info 返回脱敏客户名

- **WHEN** 研判图调用 business_ticket.query_case_info 且快照行存在
- **THEN** 返回 fixture 形状，customer_name 为脱敏值

#### Scenario: query_case_info 读取快照新三列

- **WHEN** 研判图调用 business_ticket.query_case_info 且快照行的 `product_type`/`repayment_records`/`create_time` 三列有业务值
- **THEN** 返回的 `product_type`、`create_time`、`repayment_records` 为快照行的实际业务值，SHALL NOT 是固定空串或空列表

#### Scenario: 快照新三列为空时仍不崩

- **WHEN** 快照行的 `product_type`/`repayment_records`/`create_time` 三列为 NULL
- **THEN** 返回值可为空，但 SHALL NOT 返回 500，图继续执行

#### Scenario: 历史为空不报错

- **WHEN** query_history_tickets 或 query_history_complaints 查不到历史
- **THEN** 返回空列表，不返回 500
