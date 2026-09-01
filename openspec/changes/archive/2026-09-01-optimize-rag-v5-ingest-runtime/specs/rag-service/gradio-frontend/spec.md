## MODIFIED Requirements

### Requirement: 网页上传文档/音频

系统 SHALL 支持在网页上传全部支持格式（txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma），上传时 MUST 调用后端 `POST /kb/documents` ingest 接口（携带文件与 category）；上传成功 MUST 展示返回的 doc_id 与入库状态；上传失败 MUST 在页面友好提示错误原因（如解析失败 A3001、文件超限 B0001），**不得白屏、不得出现未捕获 500**。

上传请求的传输失败 MUST 按原因分流提示，不得把「读超时」与「后端宕机/连不上」写成同一句：

- **连接失败**（无法建立连接、连接被拒绝、DNS 失败等）：页面提示「后端服务暂不可用」等友好信息；
- **读超时且请求已发出**（等待 ingest 响应超时，后端可能仍在处理）：页面 MUST 提示入库处理中，引导用户刷新文档列表查看 processing/active，并提示勿重复上传同一文件；MUST NOT 提示「后端服务暂不可用」。

本 Requirement 不要求修改上传等待秒数，不要求改为异步 202。

#### Scenario: 上传成功返回 doc_id

- **WHEN** 用户在网页选择上传一个 TXT 文件并选择分类后点击上传
- **THEN** 页面调用后端 ingest 接口成功，展示返回的 doc_id、chunks_count 与 status=active

#### Scenario: 上传失败页面友好提示

- **WHEN** 用户在网页上传一个不支持格式的文件或后端返回业务错误
- **THEN** 页面展示友好错误提示（含错误码/原因），页面不白屏、不显示裸 500 页面

#### Scenario: 后端不可用时上传友好降级提示

- **WHEN** 后端服务不可用（连接失败、拒绝连接、DNS 失败等，请求未能建立连接）
- **THEN** 页面提示"后端服务暂不可用"等友好信息，页面不白屏、不卡死

#### Scenario: 上传读超时按入库处理中提示

- **GIVEN** 上传请求已成功发往后端 `POST /kb/documents`
- **WHEN** 等待 ingest 响应发生读超时，后端可能仍在处理该文档
- **THEN** 页面提示入库处理中，引导刷新文档列表查看 processing/active，并提示勿重复上传；MUST NOT 提示「后端服务暂不可用」；页面不白屏、不卡死
