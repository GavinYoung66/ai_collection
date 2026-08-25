# rag-service-final-fix — Tasks

## 1. 配置项新增（前置）

- [x] 1.1 `common/config/settings.py` 新增 `KB_MAX_FILE_SIZE: int = 20 * 1024 * 1024`（20MB）与 `KB_PARSE_TIMEOUT: float = 30.0`，注释对齐 DD-03 §8
- [x] 1.2 `.env.example` 与 `services/rag-service/Dockerfile` ENV 同步新增 `KB_MAX_FILE_SIZE`、`KB_PARSE_TIMEOUT`
- [x] 1.3 配置项单元测试：`common/tests/test_settings.py::test_rag_file_size_parse_timeout_defaults`（断言默认 20MB/30s）

## 2. A 类：上传大小与解析超时

- [x] 2.1 `api/deps.py::save_upload_file` 增加大小校验：优先读 Content-Length，超限抛 B0001；无头时 read 后按字节校验；校验先于任何解析/落库
- [x] 2.2 上传测试：`test_api_upload.py::test_upload_over_size_limit_returns_b0001`、`test_upload_at_limit_boundary_succeeds`、`test_upload_normal_file_still_works`
- [x] 2.3 `services/ingest_service.py` ingest 与 reindex 调用 `extract_text` 处包 `asyncio.wait_for(timeout=KB_PARSE_TIMEOUT)`，超时抛 `ParseTimeoutError`
- [x] 2.4 超时按既有解析失败路径处理：A3001 + status=failed；`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有降级分支（不产生部分索引残留）
- [x] 2.5 解析超时测试：`test_ingest_service.py::test_parse_timeout_returns_a3001`、`test_parse_timeout_degraded_keep_text_branch`

## 3. A 类：/health 依赖探活

- [x] 3.1 检索全局 step6/02 §5.1 定稿 /health 响应结构（status/service/version/checks/ts；healthy/unhealthy），记录于设计备注
- [x] 3.2 `api/health.py` 实现 PostgreSQL（select 1）/ Milvus / Redis / Embedding 四项探活，每项 2s 超时，任一失败返回非 healthy；探测异常不抛 500
- [x] 3.3 **修正**：docker-compose healthcheck 依赖根路径 `/health`（`curl -f http://localhost:8000/health`），不能移除根端点；改为保留 `/health` 与 `/api/v1/health` 双路由，共用 `probe_health()` 单一探活实现
- [x] 3.4 健康检查测试：`test_api_health.py::test_health_all_dependencies_up`、`test_health_single_dependency_down_unhealthy`、`test_health_probe_timeout_not_500`

## 4. A 类：reindex 摘要一致性

- [x] 4.1 `services/ingest_service.py` reindex 流程从 PG 回读 `doc.summary` 并作为 summary 参数传入既有 ingest 入库路径
- [x] 4.2 验证 reindex 后 ES `doc_summary` 与 PG `summary` 一致（复用既有"先正文后摘要"写入顺序）
- [x] 4.3 测试：`test_summary_metadata.py::test_reindex_preserves_summary_both_ends`、`test_reindex_no_summary_doc_no_error`

## 5. 隐患修复

- [x] 5.1 `api/deps.py::save_upload_file` 临时文件在 finally 块显式 `os.unlink`（保留 delete=False，避免 Windows 二次打开限制）
- [x] 5.2 临时文件清理测试：`test_api_deps.py::test_upload_temp_file_cleaned_up`（上传后 /tmp 无残留）
- [x] 5.3 `services/asr_providers/whisper_provider.py` 模型懒加载加模块级 `threading.Lock` + 双重检查
- [x] 5.4 Whisper 并发测试：`test_whisper_provider.py::test_whisper_model_loaded_once_concurrent`（任务列名 test_asr_providers.py 为笔误，并发用例落在既有 test_whisper_provider.py）
- [x] 5.5 `services/asr_client.py` 与 `asr_providers/dashscope_provider.py` 的 `_is_degraded` 探测包 `try/except (RedisError, OSError)`，捕获记录 warning（含 trace_id）按未熔断处理
- [x] 5.6 Redis 容错测试：`test_asr_client.py::test_degraded_probe_redis_error_no_propagate`
- [x] 5.7 `services/ingest_service.py::delete_by_doc` 对 Milvus/ES 删除失败重试 2 次（退避 1s/3s），仍失败记录告警日志（doc_id、目标、异常），PG 软删除与业务返回不受影响
- [x] 5.8 删除重试测试：`test_ingest_service.py::test_delete_retry_then_success`、`test_delete_retry_fail_logs_alert`
- [x] 5.9 `_backfill_es_index` 待补 doc_id 写入 Redis 集合 `rag:backfill:pending`（TTL 30min），完成/放弃后移除；`main.py` startup 扫描恢复并重新调度
- [x] 5.10 补索引持久化测试：`test_ingest_service.py::test_backfill_pending_persisted_after_restart`
- [x] 5.11 `repositories/doc_repo.py::list_by_category` 改为分组聚合批量获取 chunk 计数，消除 N+1
- [x] 5.12 N+1 修复测试：`test_doc_repo.py::test_list_by_category_chunk_counts_batched`
- [x] 5.13 `services/search_service.py` 召回与回填处 `uuid.UUID` 转换统一经 `_safe_uuid`，单条解析失败跳过并记录 warning，不中断检索
- [x] 5.14 UUID 容错测试：`test_search_service.py::test_invalid_uuid_in_recall_skipped_with_warning`
- [x] 5.15 `main.py` 注册 FastAPI shutdown 事件，幂等关闭 embed/rerank/milvus/es/redis 客户端
- [x] 5.16 shutdown 测试：`test_main.py::test_shutdown_closes_clients`

## 6. 文档对齐（仅文档，DD-03）

- [x] 6.1 DD-03 §5.2 top_k 入参边界修订为 `[1,20]`；§10.3 删除"top_k 永不收紧"，保留 category 四值不收紧
- [x] 6.2 DD-03 §5.1 上传 file 标注"≤20MB 超限 B0001"；重复删除修订为"返回 B0004（非幂等）"
- [x] 6.3 DD-03 §6.4 闸门 1 修订为"degraded 保持 false（非基础设施降级）"
- [x] 6.4 DD-03 §3 目录结构更新至 V4 快照；health.py 职责改"依赖探活"
- [x] 6.5 DD-03 §9 测试用例数 77 → 209 passed / 1 skipped

## 7. 测试与冒烟

- [x] 7.1 新增/修改用例全部通过（test_api_upload / test_api_health / test_ingest_service / test_summary_metadata / test_asr_providers / test_asr_client / test_doc_repo / test_search_service / test_api_deps / test_main / test_settings）
- [x] 7.2 全量回归保持基线 209 passed / 1 skipped 不回退（实测 240 passed / 1 skipped，新增 31 用例全部通过）
- [x] 7.3 `scripts/smoke/smoke_rag.sh`、`scripts/smoke/smoke_rag_v4.sh` 增加：上传大小校验步骤、/health 探活步骤、reindex 摘要保持步骤
- [x] 7.4 `scripts/rag/check_error_codes.sh`、`check_nongoals.sh` 零违规（未新增错误码、未新增非目标能力）
- [x] 7.5 ruff / bandit / mypy 静态检查不劣于基线（ruff 清零；mypy 30 处为既有基线；bandit High 2 处为既有 MD5）

## 8. /health 降级模式开关（用户追加需求，2026-08-25）

- [x] 8.1 `common/config/settings.py` 新增 `KB_HEALTH_DEGRADED_MODE: bool = False`（默认 false = 严格探活，任一依赖失败 → unhealthy）
- [x] 8.2 `.env.example` 与 `services/rag-service/Dockerfile` ENV 同步新增 `KB_HEALTH_DEGRADED_MODE=false`
- [x] 8.3 `api/health.py::probe_health` 降级模式实现：任一依赖失败只记录聚合告警（含 trace_id），status 返回 degraded（契约三态之一，HTTP 200 不触发容器重启），checks 保留真实状态
- [x] 8.4 测试：`test_api_health.py` 新增降级模式用例（依赖失败 → degraded / 全 ok → healthy）；`test_settings.py` 新增默认值与环境覆盖用例
- [x] 8.5 DD-03 §8 配置项表同步新增 KB_HEALTH_DEGRADED_MODE 行
