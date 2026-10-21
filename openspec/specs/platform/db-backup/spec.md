# platform/db-backup Specification

## Purpose
为生产环境的有状态存储提供可执行、可验证、可恢复的备份能力：PostgreSQL、Milvus、Elasticsearch 三类核心数据可备份、可恢复、按策略保留，使数据损坏或误操作时有确定的恢复路径。

## Requirements

### Requirement: PostgreSQL 备份与恢复

系统 SHALL 提供 PostgreSQL 全量备份脚本（pg_dump 逻辑全量导出）与对应恢复脚本。备份文件输出到宿主机 `./backups/pg/`，文件名含日期与时间戳；同日重复执行 MUST NOT 覆盖已有备份、MUST NOT 报错。恢复脚本 SHALL 能将备份完整还原到可启动的 PostgreSQL 实例。

#### Scenario: 生成 PG 全量备份

- **WHEN** 执行 PG 备份脚本
- **THEN** `./backups/pg/` 下生成新的备份文件，文件存在且非空，内容为可用于恢复的全量导出

#### Scenario: PG 备份可恢复且数据一致

- **WHEN** 记录关键表 `count(*)` 后删除/清空表，再执行恢复脚本
- **THEN** 恢复后各关键表 `count(*)` 与恢复前一致，抽样 3 条记录的字段值完全相同

#### Scenario: 同日重复执行不冲突

- **WHEN** 同一天连续执行两次 PG 备份
- **THEN** 两次均成功，生成两个不同文件名的备份，互不覆盖

### Requirement: Milvus 备份与恢复

系统 SHALL 提供 Milvus 备份与恢复脚本，优先通过 Milvus 2.4 backup API 完成一致性备份；当该 API 在当前部署不可用时，SHALL 降级为 etcd snapshot 加对象存储（minio）数据拷贝的组合备份，并在日志中明确记录实际采用的路径。备份产物输出到 `./backups/milvus/`，恢复脚本覆盖所用备份路径对应的恢复方式。

#### Scenario: 优先生成 API 备份

- **WHEN** Milvus 2.4 backup API 可用且执行 Milvus 备份脚本
- **THEN** 通过 backup API 生成备份，产物落在 `./backups/milvus/` 且非空，日志记录为 API 路径

#### Scenario: API 不可用时降级备份

- **WHEN** backup API 不可用且执行 Milvus 备份脚本
- **THEN** 脚本改采 etcd snapshot 与 minio 数据拷贝完成备份，产物存在且非空，日志显式记录已降级及降级原因

### Requirement: Elasticsearch 备份与恢复

系统 SHALL 提供 Elasticsearch 备份与恢复脚本，通过 ES snapshot API 创建快照，快照仓库指向宿主机 `./backups/es/`（经集群可访问的仓库路径挂载）。备份后 SHALL 校验快照创建成功且可被 ES 列出；恢复脚本 SHALL 能从快照还原索引。

#### Scenario: 生成 ES 快照

- **WHEN** 执行 ES 备份脚本
- **THEN** 快照仓库中出现新快照且状态可被 ES 成功列出，`./backups/es/` 对应数据存在且非空

#### Scenario: 快照可恢复

- **WHEN** 索引数据丢失或被删除后执行恢复脚本
- **THEN** 索引从快照还原，可重新检索到备份前的数据

### Requirement: 统一编排、校验与保留策略

系统 SHALL 提供统一编排脚本串行执行 PG、Milvus、ES 三类备份：每类备份输出独立日志；单个备份失败时编排 MUST 以非零状态退出并在日志中标明失败项；每个备份完成后 SHALL 校验产物存在且非空，校验不过视为该备份失败。编排脚本 SHALL 自动清理超过 7 天的历史备份。备份产物固定输出到宿主机 `./backups/{pg,milvus,es}/` 目录结构。

#### Scenario: 一键串行备份三类存储

- **WHEN** 执行统一编排脚本
- **THEN** PG、Milvus、ES 备份串行依次执行，`./backups/` 下生成三类备份文件且均非空，全过程有日志可查

#### Scenario: 备份失败被显式暴露

- **WHEN** 三类备份中任一类失败或产物为空
- **THEN** 编排脚本以非零状态退出，日志明确指出失败的存储类型与失败步骤

#### Scenario: 过期备份自动清理

- **WHEN** `./backups/` 中存在超过 7 天的备份文件并执行编排脚本
- **THEN** 超过保留期的文件被清理，7 天内的备份保留不受影响

### Requirement: 定时执行仅提供示例不自动注册

所有备份/恢复脚本 SHALL 在注释中提供 cron 定时示例（每日凌晨执行，形如 `0 2 * * *`），但 MUST NOT 自动写入或修改任何 crontab；定时任务的注册由人工显式完成。Redis 不在备份范围（数据可重建）。

#### Scenario: 脚本不自动挂定时任务

- **WHEN** 备份脚本交付并执行
- **THEN** 系统 crontab 不发生任何变化；脚本注释中可找到 `0 2 * * *` 的 cron 配置示例
