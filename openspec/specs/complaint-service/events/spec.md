## Purpose

定义事件联动：4 频道 Pub-Sub（complaint.judged/closed/insight.weekly/mode.switched）与停止致电状态联动。

## Requirements

### Requirement: 4 频道 + 扩展 topic（单 Publisher）

系统 SHALL **扩展**既有 `app/events/publisher.py` 的 EventPublisher（**禁止**新建 `app/service/event_publisher.py`），支持：

**PRD 4 频道：**
1. complaint.judged
2. complaint.closed
3. complaint.insight.weekly
4. complaint.mode.switched

**扩展 topic（同一 Publisher，可断言，勿静默丢）：**
- complaint.degraded（流程降级告警）
- case.risk.upgraded（停止致电联动）

Payload 必须含 trace_id（缺失则生成）。Mock 可断言，不接真 MQ。

#### Scenario: 判责完成发布

- **WHEN** 判责完成
- **THEN** complaint.judged 事件发布，payload 含 trace_id

#### Scenario: 模式切换发布

- **WHEN** 模式切换成功
- **THEN** complaint.mode.switched 事件发布

#### Scenario: 降级告警发布

- **WHEN** 流程降级转 pending_review
- **THEN** complaint.degraded 经同一 EventPublisher 发布

### Requirement: 停止致电联动

系统 SHALL 在复核/判责后联动停止致电状态（集成 review_service / complaint_service）：
- 坐席违规（agent_violation）→ stop_call_flag=true + 发布 case.risk.upgraded
- 用户误解/合规体验差 → stop_call_flag=false（恢复可外呼）

#### Scenario: 坐席违规停止致电

- **WHEN** verdict=agent_violation
- **THEN** stop_call_flag=true，case.risk.upgraded 事件发布

#### Scenario: 非违规恢复外呼

- **WHEN** verdict=user_misunderstanding
- **THEN** stop_call_flag=false
