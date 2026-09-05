## ADDED Requirements

### Requirement: 重复上传拒绝同名 active

`POST /kb/documents` MUST 拒绝创建第二条「同 title（文件名）且 status=active 且未删除」的文档：返回明确业务错误（B0001，文案须说明同名已入库），不得再开后台入库任务。同 title 且 status=processing 的拒绝契约保持不变（仍 B0001，不启并行任务）。本 Requirement 不要求按文件 content hash 去重（hash 仍为可选增强，非 MUST）。

#### Scenario: 同名 active 时 API 拒绝

- **GIVEN** 已存在 title=`催收七维AI大模型技术点与难点解决方案.docx` 且 status=active、未删除的文档
- **WHEN** 再次 POST 上传同名文件
- **THEN** 返回 B0001，不创建新 doc_id，kb_documents 中该 title 的 active 未删除行数仍为 1

#### Scenario: 同名 processing 时 API 仍拒绝

- **GIVEN** 已存在同 title 且 status=processing 的未删除文档
- **WHEN** 再次 POST 上传同名文件
- **THEN** 返回明确业务错误，不创建新 doc_id，不启动第二条后台任务

#### Scenario: 同名仅 archived 时允许再传

- **GIVEN** 同 title 仅存在 is_delete=1 / status=archived 的历史行，无 active 或 processing 未删除行
- **WHEN** 再次 POST 上传同名文件
- **THEN** 允许创建新文档并进入既有异步入库流程
