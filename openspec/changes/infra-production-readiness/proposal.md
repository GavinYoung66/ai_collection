## Why

四服务事件闭环已跑通，但上生产的工程基础设施缺失：CI 只对 complaint-service 单服务生效，其余服务测试无人执行；所有密钥明文存放在 `.env`（且历史明文文件残留）；PostgreSQL/Milvus/ES 等有状态存储无任何备份与恢复手段；镜像只能本地手工构建、无标准化产出与仓库分发。任一问题在生产环境都会造成不可接受的交付与安全风险，需要在业务上线前补齐这条最低工程基线。

## What Changes

- **CI 全服务覆盖**：`.github/workflows/ci.yml` 由「写死 complaint-service 单 working-directory」改为 matrix 多 job：5 个有实质测试的服务（data-platform / rag-service / qc-service / mcp-gateway / complaint-service）各自按本服务依赖文件安装并独立跑测试；common/ 测试独立 job，并在该 job 内附加 call/case/review 三个脚手架服务的 health 测试。PR 触发后共 6 个 job 并行，任一失败整体失败、PR 不可合并。complaint-service 保留核心模块 ≥85% 覆盖率门禁，其余服务不设覆盖率门禁；lint 沿用「有配置则启用」；不新增任何测试用例。
- **密钥管理（sops + age）**：引入无服务端依赖的 sops + age 加密体系。新增 `.sops.yaml`（age 公钥 + 键名包含 `KEY`/`SECRET`/`PASSWORD`/`TOKEN` **子串**的加密规则，确保 `CCC_ACCESS_KEY_ID` 这类不以敏感后缀结尾的凭证也被加密）；根 `.env` 加密为入库的 `.env.enc`，明文密钥文件不再入仓；`.env.example` 与解密后的字段名集合对齐，仅保留字段名与 `<change_me>` 占位；启动方式统一为经 sops 解密注入环境后再拉起 compose。age 私钥由 ≥2 名核心成员离线各自持有，不入仓库。
- **数据库备份与恢复**：新增 `scripts/backup/` 脚本集，覆盖 PostgreSQL（pg_dump 全量）、Milvus（优先 2.4 backup API，不可用降级 etcd snapshot + minio cp）、Elasticsearch（snapshot API）三类存储的备份与对应恢复脚本；`backup_all.sh` 串行编排、记日志、备份后非空校验、按日期命名、保留 7 天自动清理；输出到宿主机 `./backups/{pg,milvus,es}/`。提供 cron 示例但不自动注册定时任务。
- **CD Level 1（build → push）**：新增 `.github/workflows/cd.yml`，推送 `v*` tag 时触发，在等价 CI 门禁通过后构建 8 个服务镜像（`services/*` 下全部服务）并 push 到阿里云 ACR，双 tag（git tag + latest），凭证由 GitHub secrets 注入。仅产出镜像，不做任何服务器侧部署。
- **complaint-service/ci-gate 需求迁移**：其「CI workflow」需求被平台级 `platform/ci-matrix` 取代（complaint-service 降级为 matrix 中的一个目标，其特有门禁在平台 spec 中保留），「PR 自检清单」需求维持不变。

## Capabilities

### New Capabilities

- `platform/ci-matrix`: 全仓库 CI 矩阵门禁能力。6 job 并行拓扑（5 服务独立测试 job + common/脚手架 job）、按服务依赖文件安装、complaint 核心覆盖率 ≥85% 门禁保留、lint 按需启用、任一失败阻塞合并。
- `platform/secrets-sops`: 基于 sops + age 的仓库密钥管理能力。加密规则、密钥对持有约定、明文/示例文件边界、解密注入启动方式。
- `platform/db-backup`: 有状态存储的备份与恢复能力。PG/Milvus/ES 三类备份、恢复脚本、编排与保留策略、备份有效性校验。
- `platform/cd-build-push`: 镜像构建与分发能力（CD Level 1）。tag 触发、门禁前置、8 服务镜像构建并推送 ACR、双 tag、凭证管理。

### Modified Capabilities

- `complaint-service/ci-gate`: 「CI workflow」需求被 `platform/ci-matrix` 取代并从本 capability 移除（投诉服务成为矩阵目标，其 ≥85% 核心覆盖率等特有要求并入平台 spec）；「PR 自检清单」需求不变。

## Impact

- **新增文件**：`.github/workflows/cd.yml`、`.sops.yaml`、`.env.enc`、`scripts/backup/backup_pg.sh`、`scripts/backup/backup_milvus.sh`、`scripts/backup/backup_es.sh`、`scripts/backup/backup_all.sh` 及对应 `restore_*.sh`
- **修改文件**：`.github/workflows/ci.yml`（重构为 matrix，并补充 `workflow_call` 供 CD 复用同一门禁）、`.gitignore`（放行 `.env.enc`、新增 `backups/` 忽略、清理与现状冲突的 `.github/` 误忽略规则）、`docker-compose.yml`（仅 elasticsearch 服务增加 `path.repo` 参数与 `./backups/es` bind mount，为 ES snapshot 仓库提供落盘路径；其余服务不动）
- **停止入仓/本地保留**：明文 `.env` 与历史明文备份（如 `.env.bak`）；宿主机 `./backups/` 目录不入仓；`complaint-service/config/{dev,test,pre,prod}.env` 维持现状不动
- **外部依赖与前置条件**：阿里云 ACR 仓库 `crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection`（新个人版实例独立域名）及推送账号；GitHub secrets `ACR_USERNAME`/`ACR_PASSWORD`；CI 运行环境需具备 sops/age（密钥加密步骤为本地操作，CI 不持私钥）；age 密钥对需 ≥2 名核心成员离线保管
- **运维方式变化**：本地/部署启动命令由直接 `docker compose up -d` 改为经 `sops exec-env` 包装；备份需人工挂 cron（脚本仅给示例）
- **不受影响**：全部业务代码与测试用例（只运行、不新增不修改）；四环境 env 文件；除 elasticsearch 备份挂载外的 compose 服务定义与全部数据卷；8 个服务 Dockerfile 均已存在、CD 直接复用——apply 实测发现 call/case/review 三个脚手架 Dockerfile 裸装全量 lock 在 arm64/amd64 均确定性构建失败（rocketmq 5.1.1 与 langfuse 4.15.2 的 opentelemetry 约束互斥，且 grpcio-tools 1.51.3 无 aarch64 wheel），经用户确认按 mcp-gateway 同款「排除 rocketmq 行」最小修复（app 源码零引用），不涉业务代码

## Non-Goals

- 不做服务器自动部署、滚动更新、蓝绿、灰度发布（CD 止步于镜像 push）
- 不做 Prometheus / Grafana / 指标采集 / LLM 用量观测 / 告警（下一个 change）
- 不修改任何业务代码、不新增/重构测试用例
- 不做 K8s 迁移、日志采集体系、集中式密钥服务（Vault/KMS 等）
- 不实现 call/case/review 的业务能力，三者只跑 health 测试
- 不做 Redis 备份（数据可重建）；不自动注册 crontab；不做备份异地/对象存储上传
- 不构建与推送 services/ 之外的镜像（gradio、infra 及 mock 镜像不在本期 CD 范围）
