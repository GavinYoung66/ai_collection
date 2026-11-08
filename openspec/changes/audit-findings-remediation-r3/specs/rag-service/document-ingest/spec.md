# Spec Delta

## ADDED Requirements

### Requirement: 文档写入竞态原子性

同名文档上传 MUST 在存储层具备唯一性约束（部分唯一索引）或原子插入语义。并发同名上传 MUST 只产生一条 active processing 记录，冲突 MUST 转为明确业务错误并由调用方重试/查询收敛，MUST NOT 仅依赖先查后插。

#### Scenario: 并发同名上传只保留一条

- **WHEN** 两个请求并发上传同名文档
- **THEN** 只有一条文档进入 active/processing，另一条返回明确的冲突错误，不出现双 processing 记录

#### Scenario: 已存在同名 active 文档

- **WHEN** 已存在同名 active 文档时再次上传
- **THEN** 请求被拒绝并返回既有重复上传错误语义，不创建新记录

#### Scenario: 冲突后可自愈

- **WHEN** 并发冲突发生后调用方查询文档列表
- **THEN** 可见唯一一条有效记录，不遗留无法推进的僵尸 processing 行
