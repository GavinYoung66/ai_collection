## 1. 前置与运行时收口

- [x] 1.1 确认 `rocketmq-local-bus` 已 archive 且主 `platform/event-bus` 含信封白名单；记明主 min-loop 残留 POST/`JUDGE_BUDGET` 将由本 delta 替换。验证：主 `openspec/specs/platform/event-bus/spec.md` 存在
- [x] 1.2 修改 `common/eventbus/factory.py`：仅接受 `EVENT_BUS=rocketmq`；`redis`/`redis_streams`/空值抛 `ValueError`。验证：`pytest common/eventbus/tests/test_factory.py` 更新后绿
- [x] 1.3 `.env` / `.env.example` 与 `docker-compose.yml` / `docker-compose.dev.yml` / **`docker-compose.complaint.yml`**（及其它启动 dp/qc/complaint/rag 的 compose）：显式 `EVENT_BUS=rocketmq` 与 `ROCKETMQ_*`。验证：`docker compose ... config` 相关服务含该变量
- [x] 1.4 全仓扫除服务运行路径 `EVENT_BUS=redis`（脚本默认、文档示例、旁路 compose）；单测改 mock Protocol。验证：`rg` 无未注释的服务运行配置命中
- [x] 1.5 **新增测试基建**：`ai_collection_pytest_plugin.py` + `pytest.ini` 注册 `crossapp`，解决 rag+qc 联合跑时同名 `tests` 包 `ImportPathMismatchError`；仅多路径联合跑时触发接管，单套件运行不受影响。验证：`python -m pytest services/rag-service/tests services/qc-service/tests -q` 全绿

## 2. QC 全频道上总线

- [x] 2.1 qc `publisher.py`：六出站全部 `_BUS_CHANNELS` + `whitelist_data`；删业务跨服务 `_redis.publish`。验证：单测六事件走 EventBus mock
- [x] 2.2 qc `subscriber.py`：四入站一律 `eventbus.subscribe`（含 `kb.sensitive_updated` / `strategy.updated`），移除 Redis 旁路主路径。验证：subscriber 单测 mock EventBus 绿
- [x] 2.3 保留 `alert:stream` XADD 等非跨服务 Redis；注释标明。验证：无误删 XADD；跨服务无 PUBLISH

## 3. rag `kb.sensitive_updated` 上总线（方法 B）

- [x] 3.1 rag `sensitive_words_sync`：version 变更后 `eventbus.publish("kb.sensitive_updated")`；`data` 可空；`trace_id` 上信封；**不再** Redis PUBLISH 跨服务；失败 warning + version 轮询兜底。验证：单测/集成断言 EventBus 被调且无 Redis PUBLISH
- [x] 3.2 rag Dockerfile/compose：`EVENT_BUS=rocketmq`、`ROCKETMQ_*`、SDK pin 与 qc/dp 同策略。验证：容器 `import rocketmq` 成功
- [x] 3.3 联调：rag 发 `kb.sensitive_updated` → qc 订阅热刷新（60s 内）。验证：手动 refresh 或 seed 路径下 qc WordLoader/版本可观测刷新

## 4. complaint EventBus 接入（业务轨 + hooks 双轨）

- [x] 4.1 complaint 业务 publisher：既有业务 CHANNELS + `complaint.result.ready` 经 EventBus；白名单 `data`。验证：单测业务 topic 调 EventBus
- [x] 4.2 hooks 双轨：`csmp.*` **不上** `csmp_business_events`；保留进程内 tap + structlog。验证：既有 hooks 单测（`list_events`/`csmp.rate_limit.hit` 等）仍绿且不要求上总线
- [x] 4.3 complaint Dockerfile/compose：`EVENT_BUS`/`ROCKETMQ_*`/`EVENT_BUS_SVC=cs`。验证：容器 `import rocketmq`
- [x] 4.4 lifespan 注册业务订阅循环（含 `complaint.created`）。验证：启动日志含 subscribe；健康检查不阻塞

## 5. 投诉链事件化

- [x] 5.1 搬迁/抽取差异化 `complaint_text` 组装为投诉侧（或 `common` 纯函数）+ 金标单测（坐席违规/客户侧/干净合规）。验证：金标 pytest 绿；DP 不再作为闭环正文唯一实现
- [x] 5.2 订阅 `complaint.created`：幂等 create + `judge(run_graph=true)`；仅 `case_id`。验证：重复投递一单；停在 human_review；未调 write_session
- [x] 5.3 双入口：HTTP `POST /complaints` 只建单、不自动 judge。验证：单测 HTTP create 后无自动跑图；事件路径有 judge
- [x] 5.4 `SIM_AUTO_REVIEW`（默认 false）；触发复用既有断点守卫（judge 返回后同栈检查，仅 `next` 含 `human_review` 才 resume，不轮询、不起额外线程）。验证：true/false 两条单测 + 未入断点不 resume 一条
- [x] 5.5 write_session 成功后发 `complaint.result.ready`；失败不发。验证：桥成功/失败两条
- [x] 5.6 DP `publish_complaint_created` + 订阅 `complaint.result.ready`。验证：单元测信封与确认日志
- [x] 5.7 删除 DP `JUDGE_BUDGET` 及脚本依赖。验证：`rg JUDGE_BUDGET services/data-platform` 无业务引用

## 6. 脚本与客户端

- [x] 6.1 `run_min_loop.py`：投诉腿 publish created + 等快照/`result.ready`；`SIM_AUTO_REVIEW=true`；无 ComplaintClient 三步。验证：主路径无 create→judge→review 连环调用
- [x] 6.2 `run_batch_flow.py`：质检列回流后批量发 created；事件条数即预算。验证：日志/帮助文案对齐
- [x] 6.3 `ComplaintClient` 标注测试/逃生舱；闭环禁用。验证：min_loop/batch 主路径无三步引用

## 7. 回归与验收

- [x] 7.1 单测：factory、rag kb 事件、双轨 hooks、created 幂等、HTTP 不自动 judge、SIM_AUTO_REVIEW、result.ready、QC 六事件、DP 不因 qc.check 建投诉。验证：相关 pytest 绿
- [x] 7.2 `EVENT_BUS=rocketmq` 跑 `run_min_loop.py`（SIM_AUTO_REVIEW=true）。验证：主项 PASS；快照两列非空
- [x] 7.3 幂等抽检：重复 `complaint.created`。验证：一单、无双跑图
- [x] 7.4 信封非法字段双侧拒绝抽检。验证：拒发/拒消有审计字段
- [x] 7.5 残留扫描：业务跨服务无 `_redis.publish`（含 rag）；无服务运行 `EVENT_BUS=redis`；hooks 未误上业务 Topic。验证：`rg` 符合双轨与 Non-Goals 例外
