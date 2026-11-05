## Purpose

扩展事件总线基线（第一期已立「死信消费」「订阅循环韧性」「异步发布任务强引用」等需求）：本轮补齐生产者侧的初始化线程安全——RocketMQEventBus 的发布执行器为多线程池，生产者懒加载是跨线程状态，第一期「异步发布任务强引用」需求未覆盖此面。

## ADDED Requirements

### Requirement: 生产者初始化线程安全

事件总线生产者的懒加载初始化 MUST 线程安全（互斥锁保护或启动时急切初始化），MUST NOT 在多线程发布执行器下出现 check-then-act 竞态。并发首次发布 MUST 仅创建一个 Producer 实例，MUST NOT 产生未 shutdown 的被覆盖 Producer 导致底层连接泄漏。

#### Scenario: 并发首发仅创建一个 Producer

- **WHEN** 同一 EventBus 上两个线程并发执行首次发布（多线程发布执行器）
- **THEN** Producer 构造次数为 1，无被覆盖实例残留泄漏
