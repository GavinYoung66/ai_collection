## Purpose

定义投诉服务 E2E：接单→judge→status→verdict→review→close→Pub-Sub→回流；HTTP 方法对齐现网。

## ADDED Requirements

### Requirement: 投诉全旅程 E2E

系统 SHALL 在 `tests/e2e/test_complaint_journey.py` 覆盖：
1. POST `/api/v1/complaints`
2. POST `/api/v1/complaints/{id}/judge`（body 含 **`run_graph: true`**）
3. GET `/api/v1/complaints/{id}/status`
4. GET `/api/v1/complaints/{id}/verdict`
5. **PUT** `/api/v1/complaints/{id}/review`（approve 或业务允许动作）
6. **PUT** `/api/v1/complaints/{id}/close`
7. Pub-Sub：经 `app.events.publisher.publisher` 断言 `complaint.judged` / `complaint.closed`（含 trace_id）
8. 回流：Mock 断言（不要求真数据中台）

#### Scenario: 关键路径 0 失败

- **WHEN** 执行完整 E2E
- **THEN** 各步断言通过

#### Scenario: judge 真图路径

- **WHEN** E2E 传 `run_graph=true`
- **THEN** 走 `build_complaint_graph` 执行路径并产出可查询 verdict（非仅 judge_stub 写死路径）

#### Scenario: Publisher 单真相

- **WHEN** 断言事件
- **THEN** 仅使用 `app.events.publisher`；不存在 `app/service/event_publisher.py`
