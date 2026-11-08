# Spec Delta

## ADDED Requirements

### Requirement: 运行期总线配置不可变

事件总线类型与开关 MUST 在进程启动时确定并在进程生命周期内保持只读。请求处理路径与事件处理路径 MUST NOT 写入 `os.environ` 修改总线配置，MUST NOT 产生跨请求、跨协程的进程全局副作用。运行期切换总线 MUST 通过显式配置变更与重启完成。

#### Scenario: 处理请求不修改进程配置

- **WHEN** 一次业务请求或事件处理完成后检查 `os.environ["EVENT_BUS"]`
- **THEN** 值与启动时配置一致，未被请求路径改写

#### Scenario: 并发请求观察到一致的总线类型

- **WHEN** 并发请求分别经过不同的发布分支
- **THEN** 所有分支使用同一总线实现，不出现按请求切换总线

#### Scenario: 同步写入与异步写入行为一致

- **WHEN** 以相同配置分别从同步 CLI 与异步服务写入事件
- **THEN** 两者使用同一持久化后端语义，不出现配置分裂
