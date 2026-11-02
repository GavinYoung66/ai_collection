# Tasks

> 编号规则：`N.M` 为执行单元编号（全局唯一，`1.1`–`10.5`），`[T0x]` 为《问题清单.md》原始编号（追溯标签），两者一一对应。批次颜色与色值见 `design.md` D1。

## 1. ✅🔴 密钥与身份红线（#DC2626）｜预期规模：T01=M（含仓库外人工动作）、T02=M、T03=S、T04=S（粗估）（2026-11-02 批次评审 + 修复复验通过）

- [x] ✅ 1.1 [T01] 真实 LangSmith API Key 明文泄漏进 git（`lsv2_pt_...`，注释行不被 sops 加密）｜位置：`.env.enc:31`｜整改：立即在平台轮换/吊销该 Key；删除注释行；接入 gitleaks/trufflehog 到 CI｜验证：`grep -nE 'lsv2_pt_' .env.enc` 零命中且 CI 密钥扫描对注入的假 Key 变红（吊销动作留存凭证编号，属仓库外人工动作）｜状态：仓库侧已完成（注释行已删、`.gitleaks.toml` + CI `secret-scan` 已接入并本地验证）；⚠ 平台侧轮换/吊销仍待人工执行并留存凭证编号
- [x] ✅ 1.2 [T02] 认证全局默认关闭，complaint-service / qc-service 核心接口（含 PII）无鉴权｜位置：`.env.example:134/258`、`complaints.py`、`qc-service/app/auth.py:3`｜整改：生产强制 `ENABLE_AUTH=true`；给 complaints / qc `records` 补 `require_roles`｜验证：生产配置下匿名请求返回 401；本地关闭认证时启动日志有显著告警｜状态：已完成（`Settings.auth_required` 生产强制；complaints 全路由 / qc records 三接口已挂角色校验；本地关闭打印 `auth_disabled` 告警；评审后已将模板默认值恢复为本地关闭、生产强制逻辑实测通过；冒烟补凭证已落地——`scripts/smoke/smoke_auth.sh` 自签 JWT 接入 complaint/qc 冒烟，签发→验签闭环验证通过）；生产匿名 401 的可运行实证随 T36 生产基线补做
- [x] ✅ 1.3 [T03] JWT 密钥默认 `change_me`，可被伪造 admin 令牌｜位置：`common/config/settings.py:70`｜整改：启动时 fail-fast 校验占位值；生产由 KMS 注入｜验证：`JWT_SECRET=change_me` 启动非零退出｜状态：已完成（`validate_required_secrets` 接入 complaint/qc/mcp-gateway/data-platform 的 lifespan；uvicorn 实测占位值启动退出码 3；评审复验占位值 RuntimeError / 合法值放行通过）；「生产由 KMS 注入」裁定：本项目的密钥注入体系为 sops 加密 `.env`（deploy 既有流程），按「密钥系统注入」认定满足，KMS 形态留待生产基线演进
- [x] ✅ 1.4 [T04] 种子脚本硬编码弱口令（`Admin@2026` 等）｜位置：`scripts/seed/seed_users.py:13-24`｜整改：密码改从环境变量/密钥读取；生产禁止执行 seed｜验证：`grep -rn 'Admin@2026' scripts/seed` 零命中；生产执行 seed 被拒并非零退出｜状态：已完成（口令改为 `SEED_*_PASSWORD` 注入；缺失/生产环境均以退出码 2 拒绝，`seed_all` 同样被拦截；评审后已补占位值拦截，四场景复验通过）

## 2. ✅🟠 并发与时序正确性（#EA580C）｜预期规模：T05=S、T06=M、T07=S、T08=L（2026-11-02 批次评审通过 + Q-001 裁决修复；fail-closed 新增单测待依赖环境复跑）

> 批次间依赖（design D6）：2.4（T08）的补偿验收依赖 3.2（T12）的「订阅循环不退出」前提（用 mock 事件源验收，不等 T12 合入）；2.2（T06）与 3.2（T10）同属 runtime-correctness「在途任务互斥与幂等落库」需求，批 2 验收不含「并发落库全部成功」场景（属批 3 的 T10）。

- [x] ✅ 2.1 [T05] 灰度分片用 `hash()`，跨进程/重启路由不稳定｜位置：`mode_service.py:73`｜整改：改用 `zlib.crc32` 或 `hashlib.md5` 确定性哈希｜验证：两个不同 `PYTHONHASHSEED` 的进程对同一标识算出同一分片｜状态：已完成（新增 `gray_bucket` 以 `zlib.crc32(session_id) % 100` 替换内置 `hash()`；`PYTHONHASHSEED=0/1/42/random` 四进程对同一标识均得 bucket=74，单测 6 passed）｜估算：S
- [x] ✅ 2.2 [T06] `_graph_inflight` check-then-act 竞态，Redis 锁 fail-open 时双跑图、checkpoint 分叉｜位置：`complaint_service.py:456-482`｜整改：先 `add` 再 finally `discard`，或用 `asyncio.Lock` 包住检查到入集｜验证：并发两请求仅一个进入图；Redis 不可用时第二次被拒｜状态：已完成（检查与 `_graph_inflight.add` 之间无 await，所有返回/异常路径 finally discard；并发双请求仅 1 次进图、Redis 不可用时第二次仍被拒；新增单测 5 passed，相关既有 31 passed）｜评审补充（S-002/Q-001 裁决）：检查与登记之间不得有 await（原子性硬约束）；Redis 锁不可用 MUST fail-closed 保守拒绝（judge 转 pending_review、resume 放弃本次待重触发），MUST NOT 退化双跑——judge 与 resume 两路径均已落地
- [x] ✅ 2.3 [T07] `create_task` 未持有引用，事件发布可能被 GC 静默丢弃｜位置：`publisher.py:98-100`｜整改：维护 `set[Task]` + `add_done_callback` 移除（参考 `main.py:91`）｜验证：任务完成后强引用集合为空；GC 压测下无事件丢失｜状态：已完成（`_bus_publish_tasks` 强引用 + done_callback(discard)；GC 压测 20 条事件全部送达且集合归零，单测 3 passed）｜估算：S
- [x] ✅ 2.4 [T08] 质检回写反查时序缺口：事件先于落库即 `skip`，不重试、不入队｜位置：`data-platform/.../subscriber.py:189`｜整改：改延迟重试队列 / 落 pending 表 + 定时补偿｜验证：模拟事件早于落库，补偿后回写成功且重试次数可观测｜状态：已完成（qc_record 不可见时入延迟补偿队列，3s 间隔/10 次上限，`enqueued/succeeded/exhausted/skipped_missing_session` 计数可观测，lifespan 新增补偿循环；新增单测 5 passed，data-platform 22 passed）｜估算：L

## 3. 🟡 消息可靠性与资源泄漏（#CA8A04）｜预期规模：T09=M、T10=M、T11=S、T12=M（粗估）

- [ ] 3.1 [T09] DLQ 无人消费；且"DLQ 生产失败仍 ACK"导致消息彻底丢失｜位置：`common/eventbus/rocketmq.py:366`｜整改：补 DLQ 消费/重放入口；DLQ 生产失败不 ACK、靠不可见超时重投｜验证：mock DLQ 写入失败时断言未 ACK；重放入口按 `event_id` 命中
- [ ] 3.2 [T10] 工单落库幂等非原子（SELECT-then-INSERT）；主键即 case_id，不会重复建单，但并发下后到请求报落库失败｜位置：`complaint_service.py:215-244`｜整改：改 `INSERT ... ON CONFLICT DO NOTHING` 或捕获 `IntegrityError` 降级为幂等成功｜验证：并发两请求均返回成功，数据库中仅一条记录
- [ ] 3.3 [T11] ASR 音频分段 `[open(p).read()...]` 不关闭，fd 泄漏｜位置：`dashscope_provider.py:117`｜整改：改用 `Path(p).read_bytes()` 或 `with open`｜验证：连续处理 100 段后进程 fd 数不线性增长
- [ ] 3.4 [T12] RocketMQ receive 异常 `raise` 冲出循环，订阅永久停止、无自动重启｜位置：`common/eventbus/rocketmq.py:194-208`｜整改：异常处 `continue` + 退避 sleep，或加看门狗重启｜验证：注入一条抛异常消息后，后续消息仍被消费

## 4. 🟢 时间、状态与资源生命周期（#16A34A）｜预期规模：以 S 为主（T13/T15/T16/T17/T18 单点修复、T14=M，粗估）

- [ ] 4.1 [T13] 外呼时段用 naive `datetime.now()`，容器 UTC 时整体偏 8 小时（合规风险）｜位置：`common/utils/time_guard.py:11`｜整改：显式 `datetime.now(timezone(settings.CALL_TIMEZONE))`｜验证：`TZ=UTC` 下时段判定与 Asia/Shanghai 一致（固定时钟单测）
- [ ] 4.2 [T14] SessionService 半锁：仅 `create_session` 加锁，其余读写裸操作｜位置：`session_service.py:111-235`｜整改：统一加锁，或确认单线程后移除锁并注释｜验证：并发读写会话无竞态；若移除锁则注释说明单线程前提
- [ ] 4.3 [T15] Pillow `Image.open` 未 close，大批量摄入内存累积｜位置：`asset_extractor.py:49-56`｜整改：用 `with Image.open(...) as img`｜验证：批量摄入内存水位稳定；裸 `Image.open` 调用零命中
- [ ] 4.4 [T16] 默认 LLM transport 每次调用新建 `httpx.AsyncClient`，无 keep-alive｜位置：`common/llm/transport.py:58`｜整改：复用共享 `AsyncClient`｜验证：50 次调用仅创建 1 个 client（计数断言）
- [ ] 4.5 [T17] `call_log_sink` 文件句柄永不关闭｜位置：`call_log_sink.py:39`｜整改：实现 `close()` + `__del__`，替换前关闭旧 sink｜验证：替换 sink 后旧句柄关闭，退出前 flush 完成
- [ ] 4.6 [T18] 熔断器 `record_success` 在 OPEN 态也清零失败计数（语义不严谨）｜位置：`common/resilience/breaker.py:168-175`｜整改：仅 CLOSED/HALF_OPEN 态处理 success｜验证：OPEN 态上报 success 后失败计数与熔断状态均不变

## 5. 🔵 安全加固（中危）（#2563EB）｜预期规模：T19=S、T20=L（依赖升级+CI 门禁）、T21=S、T22=M、T23=S（粗估）

- [ ] 5.1 [T19] 日志脱敏仅覆盖手机号，身份证/银行卡不脱敏｜位置：`common/config/logging.py:7-17`｜整改：复用 `security/mask.mask_text`，补齐正则｜验证：身份证号与银行卡号在日志中为掩码形式
- [ ] 5.2 [T20] 依赖含已知 CVE（aiohttp 3.9.1、python-multipart 0.0.6、python-jose、cryptography 旧版）｜位置：`requirements.txt`｜整改：升级到已修复版本，接入 pip-audit/safety｜验证：`pip-audit` 零高危，且 5 个服务测试矩阵与冒烟全绿（先升级、后开门禁）
- [ ] 5.3 [T21] RAG `/search` 显式 fail-open 无鉴权｜位置：`rag-service/app/api/deps.py:198`｜整改：按需改强制鉴权，`payload=None` 不放行敏感文档｜验证：未带身份的请求不返回受限文档
- [ ] 5.4 [T22] 可配置外部 URL 引入 SSRF 面（`base_url` 拼 path）｜位置：`mcp-gateway/app/bridge_http.py:77`｜整改：加 scheme/host 白名单，禁公网/元数据地址｜验证：`base_url` 指向 `169.254.169.254` 时出站前被拒并告警
- [ ] 5.5 [T23] `datetime.utcnow()` 已废弃（Python 3.12+），产出 naive｜位置：`models/state.py:248`｜整改：改 `datetime.now(timezone.utc)`｜验证：`grep -rn 'utcnow()' common services` 零命中

## 6. 🟣 架构收敛与进程边界（#7C3AED）｜预期规模：以 M 为主、T25=L（worker 剥离，粗估）

- [ ] 6.1 [T24] 8 服务中 3 个（call/case/review）是 health 空壳，过度前置拆分｜位置：`services/call-service`、`case-service`、`review-service`｜整改：用 `profiles` 标注非默认启动，或从编排移除｜验证：默认 `docker compose up` 不拉起这三个服务
- [ ] 6.2 [T25] complaint-service 大泥球：单目录 84 个 py 文件，3 个常驻后台循环 + 按需派发的研判任务都在 FastAPI 进程内｜位置：`complaint-service/app/main.py:139-272`｜整改：剥离为独立 worker/consumer 进程｜验证：FastAPI 进程内无常驻循环，worker 独立启动且冒烟通过
- [ ] 6.3 [T26] 架构图把进程内 `invoke_llm` 画成"大模型网关"，标题易被误读为独立进程（同格已标"编排器"，README:276 已说明暂不独立化）｜位置：`README.md:20-23/276`｜整改：图示改为"LLM 编排器（进程内）"，图注注明非独立进程｜验证：README 图示标注"进程内编排器"，无"独立进程"表述
- [ ] 6.4 [T27] 熔断器双实现并存（`circuit_breaker.py` 死代码 vs `resilience/breaker.py`）｜位置：`common/llm/circuit_breaker.py`｜整改：删除死代码及 `llm/__init__.py` 导出｜验证：全仓检索 `llm.circuit_breaker` 零引用
- [ ] 6.5 [T28] 重试四套并存，语义漂移｜位置：`utils/retry.py`、`llm/reliability.py`、`mcp/reliability.py`、`eventbus/rocketmq.py`｜整改：在 `common/resilience` 增加统一 `retry.py` 原语｜验证：四处重试收敛为单一原语，重试次数与退避配置只有一处定义

## 7. 🟤 重复实现收敛与质量门禁（#92400E）｜预期规模：T29=L、T30=S、T31=M、T32=M（粗估）

- [ ] 7.1 [T29] `llm/reliability.py` 570 行 god module，七种职责混装｜位置：`common/llm/reliability.py`｜整改：按重试/降级/限流/编排/埋点拆分｜验证：拆分后单文件职责单一，既有可靠性测试全绿
- [ ] 7.2 [T30] 双 Prompt 加载体系并存（迁移未完成）｜位置：`common/prompt/` vs `common/llm/prompt_files/`｜整改：删 `llm/prompt_files`，统一走 `common/prompt`｜验证：`common/llm/prompt_files` 已删除且全仓无引用
- [ ] 7.3 [T31] 死代码/空目录/过时 docstring（redis 双适配器、`llm/prompts/`、`_load_from_nacos` stub）｜位置：`common/eventbus/`、`common/llm/`、`common/prompt/`｜整改：清理归档，修正过时 docstring｜验证：死代码与空目录已移除，docstring 与实际行为一致
- [ ] 7.4 [T32] 静态检查形同虚设：无根 ruff/mypy 配置，CI 无 mypy/bandit，ruff 非阻塞｜位置：`Makefile`、`.github/workflows/ci.yml`｜整改：补 `pyproject.toml` 的 ruff+mypy 并真正接入 CI（去 `continue-on-error`）｜验证：CI 无 `continue-on-error`，基线外新增告警即阻断

## 8. ⚫ 配置治理与部署基线（#111827）｜预期规模：T33=M、T34=M、T35=M、T36=L（prod compose + RocketMQ 持久化，粗估）

- [ ] 8.1 [T33] 配置职责漂移：`settings.py` 塞业务参数，多份 `BaseSettings` 副本｜位置：`common/config/settings.py`｜整改：业务参数下沉各服务；统一从 common 继承｜验证：common 仅保留平台级参数，业务参数在服务级配置
- [ ] 8.2 [T34] 事件白名单硬编码 + 消费组"每服务×每事件"线性爆炸｜位置：`common/eventbus/envelope.py`、`base.py:18`｜整改：白名单配置化；改共享消费组 + tag 过滤｜验证：新增事件仅改配置生效，消费组数量不随事件数增长
- [ ] 8.3 [T35] MCP 工具契约双处静态镜像（`_LIST_SHAPE_TOOLS`）｜位置：`mcp-gateway/app/tools/__init__.py`｜整改：由网关响应带类型元数据，消除双处维护｜验证：契约由网关响应元数据提供，静态镜像点已删除
- [ ] 8.4 [T36] 部署缺生产基线：无 prod compose、RocketMQ 单节点且数据在容器内、端口裸暴露｜位置：`docker-compose*.yml`、`deploy/rocketmq/broker.conf`｜整改：建 `docker-compose.prod.yml` + RocketMQ 持久化卷 + Slave + 端口收敛｜验证：prod compose 存在，RocketMQ 数据落命名卷，对外端口收敛

## 9. 🔶 生命周期、测试与可观测性（#0891B2）｜预期规模：以 S 为主、T38=M（真实依赖集成测试，粗估）

- [ ] 9.1 [T37] `rag-service` 用已弃用 `@app.on_event`，生命周期风格漂移｜位置：`rag-service/app/main.py:275/342`｜整改：迁移到 `lifespan`｜验证：rag-service 内 `on_event` 零命中，启动/关闭钩子经 lifespan 生效
- [ ] 9.2 [T38] 缺少真实依赖的集成测试（`test_db.py` 仅做表结构契约断言、`test_speech.py` 仅测默认 mock provider，二者不是形式化测试，不应删除）｜位置：`common/tests/`｜整改：保留契约断言，另补真实数据库 / 真实 ASR 的集成测试｜验证：真实 DB 与真实 ASR 各有 1 条可执行集成测试，开关关闭时跳过
- [ ] 9.3 [T39] 时区处理不一致：`time_guard.py` naive 且 `import timezone` 未用｜位置：`common/utils/time_guard.py:2`｜整改：统一 UTC + 注入时钟｜验证：无未使用 import，全部时间判定时区感知
- [ ] 9.4 [T40] 部分 `except Exception: pass` 无日志（reconcile/stats_service）｜位置：`reconcile.py:32`、`stats_service.py:80`｜整改：统一加 `logger.warning(exc_info=True)`｜验证：`except Exception: pass` 零命中

## 10. ⬜ 余项优化与一致性收尾（#6B7280）｜预期规模：S 为主（粗估）

- [ ] 10.1 [T41] AES 密钥默认 `change_me` 非法 hex，运行时才抛错｜位置：`common/security/crypto.py:11`｜整改：启动时校验格式 fail-fast｜验证：非法或占位 AES key 启动即失败
- [ ] 10.2 [T42] Grafana 管理员密码未纳入 sops 加密体系｜位置：`.env.example:265`｜整改：纳入 `.env` 并确认被 sops 加密｜验证：示例文件为占位值，生产值经加密注入
- [ ] 10.3 [T43] `_probe_mcp_gateway` 在异步 lifespan 调阻塞 `urllib`｜位置：`complaint-service/app/main.py:44`｜整改：改 `httpx.AsyncClient` 或 `asyncio.to_thread`｜验证：并发健康检查响应时间不因探测劣化
- [ ] 10.4 [T44] `_DASHSCOPE_COMPAT_BASE` 等常量多文件重复｜位置：`provider.py:24`、`reliability.py:243`｜整改：抽到单一常量源｜验证：常量仅一处定义，其余位置改为导入
- [ ] 10.5 [T45] `metrics.py` 惰性模板缓存无锁 + 模块级默认实例｜位置：`common/observability/metrics.py:83`｜整改：可接受，加注释说明即可｜验证：缓存与全局实例处有并发假设注释

## 自检（design.md「批次重排可能漏项或重号」风险处置引用）

- 自检命令：`grep -o '\[T[0-9][0-9]\]' tasks.md | sort | uniq -d` 应为空（T 标签去重为 0）；`grep -o '\[T[0-9][0-9]\]' tasks.md | sort -u | wc -l` 应为 45
- 2026-11-02 实测：重复 T 标签 0 个，唯一 T 号 45 个（T01–T45 无重复无遗漏），执行单元 `1.1`–`10.5` 共 45 个一一对应
- 注意：批次间依赖说明等引用性文字一律使用不带方括号的 `T0x` 写法（如「3.2（T12）」），避免污染上述自检命令
