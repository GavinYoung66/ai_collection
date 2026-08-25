# RAG Summary Metadata — Delta Spec（rag-service-final-fix）

## ADDED Requirements

### Requirement: 重新入库保持摘要一致性

系统 MUST 在文档 reindex（重新解析入库）后保持摘要两端一致：重建索引时从 PG 权威源回读 `summary` 并写入 ES `doc_summary`。reindex 完成后 ES `doc_summary` 与 PG `summary` MUST 一致，禁止 reindex 清空 ES 摘要造成「PG 有摘要、索引无摘要」脏状态。

#### Scenario: reindex 后摘要保留

- **WHEN** 管理员对一个携带摘要的文档执行 reindex 操作
- **THEN** 重建后 ES `doc_summary` 与 PG `summary` 内容一致，`KB_SUMMARY_ENABLED=true` 时检索仍能返回该摘要

#### Scenario: reindex 无摘要文档无异常

- **WHEN** 管理员对一个无摘要的文档执行 reindex 操作
- **THEN** 重建后 ES `doc_summary` 保持为空，reindex 正常完成，不产生异常
