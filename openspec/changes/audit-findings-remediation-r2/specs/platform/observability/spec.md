## Purpose

扩展可观测性基线（第一期已立「日志脱敏覆盖面与异常可观测」需求）：本轮修复该需求的两个结构性绕过面——嵌套结构整体逃过脱敏、各服务另立正则产生行为差分；并将 T40 静默异常治理从"两处样例修复"升级为"全仓零容忍 + 守卫测试真实生效"。

## ADDED Requirements

### Requirement: 脱敏递归覆盖与单一事实源

日志脱敏 MUST 覆盖结构化日志字段的嵌套值（dict/list 内的字符串叶子），MUST NOT 仅处理顶层字符串导致 dict 型字段（如审计 `params`）整体绕过。审计落盘通道（call_log sink 等）与 structlog 日志 MUST 同等脱敏。脱敏正则 MUST 单一事实源（`common/security/mask`），MUST NOT 各服务另立正则副本；正则 MUST 覆盖 15 位老版身份证、15 位银行卡（含 Luhn 校验）与"手机号+尾号/分机"拼接形态。

#### Scenario: 嵌套入参 PII 不落盘

- **WHEN** MCP 网关审计记录含手机号的嵌套 `arguments` dict
- **THEN** structlog 日志与 call_log 文件中的手机号均为掩码形式，无明文 PII 双路落盘

#### Scenario: 拼接形态手机号被掩码

- **WHEN** 日志文本含 `138001380008001`（手机号+尾号拼接）或 15 位老版身份证
- **THEN** 敏感主体被掩码，不因位数边界（12–15 位非 16–19 位）而漏脱敏

### Requirement: 静默异常零容忍收口

吞异常（`except Exception: pass` / 吞掉后返回固定值）MUST 留痕：按可忽略性分级 `logger.warning/debug(..., exc_info=True)`，MUST NOT 无日志静默。主 pipeline 级异常被降级（如 QC pipeline 异常转 PARTIAL）时 MUST 记录异常摘要与栈，调用方可区分"部分成功"与"崩溃"。守卫测试 MUST 真实注册进 CI（在 pytest 收集范围内执行），MUST NOT 形同虚设。

#### Scenario: QC pipeline 崩溃可观测

- **WHEN** QC 主 pipeline 抛异常被捕获转为 PARTIAL
- **THEN** 日志含 `exc_info` 栈，`QcRunResult.message` 携带异常类型摘要，运维可区分崩溃与部分成功

#### Scenario: T40 守卫测试真实执行

- **WHEN** 仓库引入新的 `except Exception: pass` 无日志代码并提交 CI
- **THEN** CI 中的守卫测试（`test_t40_silent_exception_logging`）变红阻断
