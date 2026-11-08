# Spec Delta

## ADDED Requirements

### Requirement: 质检写端点限流身份可信

`replay/raw`、`replay/{session_id}` 与 `check/batch` MUST 统一接入鉴权与限流。限流键 MUST 使用服务端可信身份（验签后的用户标识或连接对端 IP）并带 `uid:`/`ip:` 命名空间，MUST NOT 直接采用客户端可伪造的 `X-User-Id`。三个端点 MUST 使用同一限流键口径。

#### Scenario: 伪造身份头不能获得独立配额

- **WHEN** 未认证调用方每请求变换 `X-User-Id` 高频调用质检写端点
- **THEN** 按 IP 维度限流生效，不因伪造头获得独立配额

#### Scenario: 批量与单会话端点受同等约束

- **WHEN** 调用方以 `check/batch` 或 `replay/{session_id}` 高频请求
- **THEN** 请求受与 `replay/raw` 同口径的限流约束，不出现无限流入口

### Requirement: 告警查询全量过滤

按 `qc_record_id` 查询告警时 MUST 在数据层完成过滤或分页遍历全部匹配结果，MUST NOT 只读取固定条数后在内存过滤，导致目标不在前若干条时漏报。

#### Scenario: 目标告警位于结果集后段仍可查得

- **WHEN** 某 `qc_record_id` 对应告警在按时间倒序结果中位于第 101 条之后
- **THEN** 查询仍返回该告警，不出现静默漏报

### Requirement: 运行态键 TTL 与幂等键校验

replay 运行态键 MUST 设置 TTL，MUST NOT 永久驻留。`X-Idempotency-Key` MUST 校验长度与字符形态，超长或非法值 MUST 返回 400。

#### Scenario: 运行态键自动过期

- **WHEN** 一次 replay 完成后超过配置 TTL
- **THEN** `qc:replay:run:{session_id}` 键自动消失，不永久累积

#### Scenario: 超长幂等键被拒

- **WHEN** 请求携带超过长度上限或含非法字符的 `X-Idempotency-Key`
- **THEN** 返回 400，不写入运行态键

### Requirement: 统计查询类型容错

LLM 用量聚合 MUST 对非数字的 `token_usage` 历史数据做安全转换或过滤。MUST NOT 让数据库 CAST 抛错导致统计接口 500。

#### Scenario: 非数字历史数据不使统计接口失败

- **WHEN** 数据表中存在 `token_usage="abc"` 的历史行且调用用量汇总
- **THEN** 接口返回 200，非法行按 0 或跳过处理，其余数据正常汇总
