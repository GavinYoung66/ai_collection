# Design

> 源清单：`问题清单-2026-11-05.md`（41 项，T46–T86）。本文件记录批次重排、编号体系与关键技术决策；任务内容以源清单为准，零改写迁移。

## D1. 编号体系与批次重排

- **双编号**：沿用第一期 `N.M [Txx]` 形态——`N.M` 为执行单元编号（全局唯一，`1.1`–`10.5`），`[Txx]` 为第二期报告原始编号（追溯标签）。每个任务行额外标注报告原批次（如 `（报告 B7）`），三份文件（报告 → tasks → specs delta）可逐行对照。
- **重排原则**：报告批次按「发现来源」分组（B7 高危 / B8 回归 / B9 遗漏 / B10 生命周期 / B11 正确性 / B12 安全 / B13 低危），B13 单批 16 项不可执行。执行批次按「主题同批、严重程度降序、每批 2–6 项一次可验证交付」重排为 10 批；跨报告批次的同族任务合并（如 T50 从 B7 并入批次 5 生命周期族），对照关系在任务行与下方映射表标注。
- **修复顺序裁决**：清单「修复顺序建议」为扫描侧初排（休眠炸弹 T51/T52 最前），本 change 执行顺序以批次 1–10 为准（严重程度降序）。差异点：T51/T52 因清理任务当前未接线、属休眠态而后移至批 3，T46–T50 高危前移至批 1/2/5——若执行方倾向清单初排节奏，可将批 3 与批 1/2 并行或提前执行，不影响批次内验收边界。

| 执行批次 | 主题                             | 任务（报告批次对照）           |
| -------- | -------------------------------- | ------------------------------ |
| 1        | 后台任务逃逸族                   | T46/T47（B7）、T53（B9）       |
| 2        | 安全红线补漏                     | T48/T49（B7）                  |
| 3        | 整改回归休眠炸弹                 | T51/T52（B8）                  |
| 4        | 第一期遗漏收口（T07/T10/T40 族） | T54/T55/T56（B9）              |
| 5        | 生命周期释放闭环                 | T50（B7）、T57–T61（B10）      |
| 6        | 并发与时序正确性                 | T62–T65（B11）                 |
| 7        | 安全加固                         | T67–T70（B12）、T79（B13）     |
| 8        | 静默异常批量收口                 | T71–T74（B13，含 T40 收口）    |
| 9        | 数据一致性与防护                 | T66（B11）、T75/T80–T84（B13） |
| 10       | 低危收尾                         | T76–T78（B13）、T85/T86（B13） |

- **颜色标识**：每批次一个 emoji + 十六进制色值（红→灰按严重程度递降），纯文本标识，不引入 HTML/自定义语法（沿用第一期 D1 惯例）：1🔴#DC2626、2🔴#B91C1C、3🟠#EA580C、4🟠#D97706、5🟡#CA8A04、6🟣#7C3AED、7🔵#2563EB、8🟢#16A34A、9🟤#92400E、10⚫#374151。

## D2. 系统性缺口以公共原语落地（报告结论 1/3）

报告识别的三个系统性缺口，本 change 以原语/清单方式一次性落地，避免逐点打补丁、防止下一轮再犯：

1. **`common.spawn_tracked(coro)` 统一原语**（修复缺口 1）：模块级 `set[asyncio.Task]` 强引用 + `add_done_callback(set.discard)`，形态对齐 complaint-service `publisher.py:36-39` 已验证实现。批次 1 的 1.1 先落原语与单测，1.2/1.3 及 rag-service 全部 4 处调用点、complaint `_schedule_graph_resume` 统一改用；全仓裸 `create_task` 丢弃返回值以源码扫描守护测试钉死（豁免点显式白名单）。
2. **shutdown 资源清单**（修复缺口 3）：不引入抽象 Registry 类——各服务资源 4–8 个且类型各异，统一做法为：每服务 lifespan shutdown 尾部维护**逐项注释化清单**（资源 → 释放入口），并以守护单测断言「资源清单中每一项的 aclose/dispose 在 shutdown 路径被调用」（monkeypatch 计数）。覆盖 T50/T57/T58/T59/T60/T61。
3. **时间解析单一事实源**（修复缺口 2 的时间面）：新增 `common/utils/time_parse.py` 的 `parse_naive_or_aware(dt_str, default_tz=Asia/Shanghai) -> aware datetime`（收敛 T64/T82 的 naive 解释；与 `rule_engine._parse_dt`、`llm_usage` 的分歧在批 3 决策后统一），配合清理任务 aware 化（T51/T52）。`llm_usage`（naive=UTC）与 `rule_engine`（naive=Shanghai）的约定冲突由 9.4 统一为 Shanghai 后消除双源。

## D3. 关键技术决策

- **D3.1 清理任务时间域统一（T51/T52）**：`RecoveryService._clock` 保持可注入但默认改为返回 `datetime.now(timezone.utc)`（aware）；比较改为 `(now - updated).total_seconds()`；对残留 float 旧数据加防御分支（`isinstance(updated, (int, float))` 时走 epoch 比较）。`cleanup_archived_90d` 缺 `updated_at` 改为 `continue` 保守跳过。两个旧单测（test_batch9:221、test_batch10:279）的 float 构造数据随整改更新为 aware。
- **D3.2 幂等写入对齐（T54）**：`aensure_persisted` 对齐同文件 `_persist_complaint_row`（T10 已验证形态）：`pg_insert(...).on_conflict_do_nothing(index_elements=[session_id])`；`IntegrityError` 单独捕获 → `_mark_persist_ok()` + return True；其余异常才走 fail-open 降级。
- **D3.3 状态机原子迁移（T63）**：采用条件更新（`UPDATE ... WHERE qc_status='PENDING_REVIEW'`，rowcount=0 抛 A2007），不引入 SELECT FOR UPDATE（避免与 `insert_review` 的事务边界纠缠）；`alert_repo.ack`（T80）同模式条件更新 + 幂等保留首个审计字段。
- **D3.4 脱敏递归（T49）**：`_mask_processor` 递归处理 dict/list 的字符串叶子（深度上限 8，防循环引用用 id 集合守卫）；`audit.summarize_params` 截断逻辑不变，脱敏交给统一 processor；call_log_sink 落盘路径经同一 processor 覆盖。T70 正则补齐与 T76 收敛到 `common/security/mask` 单一事实源。
- **D3.5 生命周期释放（T57–T61）**：T16 的 `aclose_shared_client()` 已存在，仅需接入各服务 shutdown；DB engine `dispose()` 接入 4 个生产服务；mcp-gateway 为 4 个 toolset 提供连接池关闭出口；rag-service shutdown 清单补 `_ocr_client`/`_asr_client`/LLM provider；qc/dp 关停 `except Exception` 前置 `asyncio.CancelledError` 分支（对齐 complaint worker `contextlib.suppress` 形态）。
- **D3.6 安全收紧的最小侵入（T48/T67/T68/T69）**：T48 仅在 rag-service lifespan 接入既有 `validate_required_secrets()`（一行）；T67 矩阵加载失败时登录态复用 `KB_ANONYMOUS_CATEGORIES` 白名单集合（不新增配置）；T68 限流 key 改 `jwt_payload.sub` 优先、匿名回退 IP 并对 `X-Real-IP` 加 IPv4/IPv6 格式校验 + `TRUST_PROXY_HEADERS` 开关（默认 false）；T69 `verify_token_with_reason` 增加 `payload.get("type") == "access"` 校验。
- **D3.7 QC 事件循环与线程池（T65）**：Whisper 模型加载改 `await asyncio.to_thread(WhisperModel, ...)` + `asyncio.Lock` 门控（替代永不争用的 threading.Lock 双重检查）。
- **D3.8 T40 守卫测试真实性核查（T56 附带）**：检查 pytest.ini `testpaths`、Makefile、`.github/workflows/ci.yml` 中守卫测试的收集路径；若未注册则补齐——本 change 唯一 CI 配置动作。

## D4. 验证策略

- **单测为主**：每项任务附守护/回归单测（同第一期惯例）；`spawn_tracked`、时间解析、递归脱敏、条件更新均有独立用例。
- **环境口径**：本地开发环境无 ruff/pytest 直跑通道时，验证以「CI 门禁（static-analysis + service-test + dependency-audit）push 实跑」为最终证据，本地以聚焦单测结果留痕（沿用第一期 R-001 降级口径先例）。
- **行为收紧的灰度标注**：T69（refresh token 禁用）与 T48（rag 占位密钥拒启）属部署期行为收紧，随任务完成在灰度说明标注（见 proposal Impact）。

## D5. 风险与依赖

- **归档顺序**：本 change 的 `platform/runtime-correctness` delta 假定第一期同名 capability 需求集已存在（第一期 change 尚未 archive）——归档 MUST 先一期后二期，避免同一 capability 双 ADDED 冲突。
- **T52 语义反转风险**：缺时间戳记录从「立即删除」改为「保守跳过」是行为变更，单测钉死两种场景（缺 key 保留 / 90 天前删除）。
- **T63 并发验证环境**：真实 PG 并发用例沿用第一期 Q-002 口径——`COMPLIANCE_DB_TESTS=1` 待环境复跑可接受，本地以 mock repo 并发单测为验收边界。
- **回归面**：批次 5 涉及 4 个服务 lifespan 改动，关停序列变化须以「启动→SIGTERM→exit 0 + 连接归零」集成冒烟验证；单服务改动不跨批合并提交。
