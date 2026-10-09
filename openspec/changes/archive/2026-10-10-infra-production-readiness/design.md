## Context

当前工程基线（已核对仓库现状）：

- CI 只有 [ci.yml](file:///Users/ellisguo/myproject/my_work/ai_collection/.github/workflows/ci.yml) 一个写死 `working-directory: services/complaint-service` 的 job；根 [pytest.ini](file:///Users/ellisguo/myproject/my_work/ai_collection/pytest.ini) 的 `testpaths` 同时含 common 三个测试目录与 qc-service 测试，`addopts` 挂了自定义插件 `ai_collection_pytest_plugin`（仅在 rag+qc 同进程联跑时分拆子进程）；mcp-gateway 有独立 [pytest.ini](file:///Users/ellisguo/myproject/my_work/ai_collection/services/mcp-gateway/pytest.ini)。
- 依赖分布：根 [requirements.lock](file:///Users/ellisguo/myproject/my_work/ai_collection/requirements.lock) 是公共底座；仅 complaint-service 与 mcp-gateway 另有 `requirements.runtime.txt` + 含 `[tool.ruff]` 的 `pyproject.toml`；其余服务无服务级依赖文件。8 个服务 Dockerfile 全部存在，均以**仓库根为 build context**（`COPY requirements.lock`/`common/`/`services/...`），Dockerfile 路径统一为 `services/<svc>/Dockerfile`。
- 测试存量：data-platform(4)、rag-service(50+)、qc-service(40+)、mcp-gateway(3) 均有实质测试；common 有 `common/tests`、`common/eventbus/tests`、`common/security/tests` 三个目录；call/case/review 仅 `tests/test_health.py`。
- 密钥：根 [.env.example](file:///Users/ellisguo/myproject/my_work/ai_collection/.env.example) 已全占位，但明文 `.env` 在本地存在；`.gitignore` 已有 `.env`/`.env.*` + `!.env.example`，且误写了 `.github/` 忽略行（现存 ci.yml 靠 force-add 跟踪）。
- 有状态服务（[docker-compose.yml](file:///Users/ellisguo/myproject/my_work/ai_collection/docker-compose.yml)）：postgres:15-alpine、milvus v2.4.13 standalone（依赖 etcd v3.5.16 + minio，minio 凭证 minioadmin/minioadmin，Milvus 默认 bucket `a-bucket`）、ES 8.11.3 单节点（xpack security 关闭，**未配 path.repo**）；均为 compose 命名卷，无显式 container_name（真实容器名带 compose 项目前缀）。
- CD/镜像仓库不存在；阿里云 ACR 目标 `crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection`（2024-09-04 后新个人版实例独立域名，官方限制只能通过该域名访问；旧通用域名 `registry.cn-hangzhou.aliyuncs.com` 属旧版实例，不适用，2026-10-07 apply 时按实测开通结果修正）。

## Goals / Non-Goals

**Goals**

- 一次 PR 能让所有服务的存量测试在 CI 并行执行，失败精确定位到服务；本地各服务目录可独立复现 CI 测试命令。
- 密钥「密文入库、明文离线」，新成员按文档可完成加解密，启动方式只增加一层 sops 包装。
- 备份脚本零宿主安装（全部经 `docker compose exec/run` 在容器内执行），备份/恢复成对交付并真实演练一次恢复。
- CD 复用 CI 门禁定义而非复制一份，保证 tag 制品与测试通过的提交严格对应。

**Non-Goals（设计级补充）**

- 不在 CI 中起真实 PG/Redis/Milvus/ES 做服务集成测试（各服务现有测试默认 Fake/本机自负责，CI 沿用现状，不补 services）。
- 不引入 sops 密钥分发服务、不做多 age recipient 的分组权限（单 recipient 列表，≥2 人持同一私钥副本）。
- 备份不落对象存储/异地、不做增量与 PITR、不做备份加密（宿主机本地目录，安全靠主机权限）。
- CD 不做 sbom/镜像漏洞扫描、不做多架构构建（仅 linux/amd64）、不做 image 签名。

## Decisions

### D1：CI 拓扑——5 服务 matrix + 1 个 common/脚手架 job，共 6 job

- `service-test`（strategy.matrix 5 维：data-platform/rag-service/qc-service/mcp-gateway/complaint-service）：每个 job `working-directory: services/<svc>`，`PYTHONPATH=../.. pytest tests/ -q`。
- `common`：仓库根显式指定三个 common 测试目录执行（**不**裸跑 `pytest`，否则根 pytest.ini 的 testpaths 会把 qc-service 测试一并拉进来）；随后循环 call/case/review 三个目录执行 `PYTHONPATH=../.. pytest tests/ -q`（只有 health）。
- 备选「matrix 扩到 8 服务（3 个 health-only）」：会产生 9 个并行检查，与验收「六 job」冲突，且为秒级 health 测试多占 runner；call/case/review 未来有业务实现时，把它们加入 matrix 即可（一行配置），不构成债。
- mcp-gateway 目录自带 pytest.ini，pytest rootdir 就近采用它；其余服务采用根 pytest.ini，显式传入 `tests/` 路径覆盖 testpaths；自定义插件仅单服务运行时不触发分拆逻辑，无副作用。

### D2：依赖安装按「根 lock 兜底 + 服务文件增量」，不做 pyproject 打包安装

统一基线（与现 ci.yml 一致）：pip 升级 + `pytest pytest-asyncio pytest-cov httpx nest-asyncio fastapi uvicorn`（locust 仅 complaint 需要，移到 complaint 条件步骤）+ 根 `requirements.lock`。然后：存在 `requirements.runtime.txt` 的服务（complaint、mcp-gateway）追加安装该文件。两个 `pyproject.toml` 仅承载 ruff 等工具配置，不执行 `pip install -e .`（避免引入打包副作用；apply 时若发现其中声明真实依赖再据实安装）。
- 备选：每服务精确最小依赖集。省 CI 时长，但需要逐服务审计依赖、易漏，违背「不改测试、只搬运」的低风险目标；CI 时长非本期货瓶颈。

### D3：complaint 既有测试阶段整体迁入其 matrix 条目，覆盖率门禁只属于它

complaint 条目不跑通用 `pytest tests/ -q`，而是保留现有四个阶段：unit+integration（沿用精确 `--cov` 模块列表与 `--cov-fail-under=85`）、e2e、regression、smoke（后台 uvicorn + `scripts/smoke/smoke_complaint_service.sh`）。其余 4 个服务与 common job 不安装 pytest-cov 门禁、不统计覆盖率。PR 自检清单 step 原样保留在 complaint 条目。
- 备选：所有服务统一一刀切 `pytest tests/`。会丢掉投诉的覆盖率/回归/冒烟资产，不可接受。

### D4：lint 维持 advisory 非阻塞，仅 ruff 且仅有配置的两个服务执行

现状 lint step 是 `continue-on-error` + `|| true`，且 CI 根本没装 ruff（一直 skip）。本次在 complaint 与 mcp-gateway 两个 job 中显式 `pip install ruff` 并执行 `ruff check app tests`，保持 `continue-on-error: true`（展示结果、不阻塞）。mypy/bandit 无配置，继续 skip。
- 备选：lint 改阻塞。存量告警数未知，会让所有 PR 立即变红，逼迫改业务代码，违反「不改业务代码」约束；留待后续专门治理 change。

### D5：sops/age——后缀子串匹配 + dotenv 显式类型

- 规则：`.sops.yaml` 的 creation_rules 用 `path_regex: '\.env(\.enc)?$'` 匹配 `.env` 与 `.env.enc` 两个文件名；字段匹配用 `encrypted_regex: '(KEY|SECRET|PASSWORD|TOKEN)'`（子串而非严格后缀）。理由：`CCC_ACCESS_KEY_ID` 以 `_ID` 结尾，严格 `*_KEY` 后缀会漏加密；现有非密钥字段均不含这四个子串，无误伤。
- 加密命令显式指定 `--input-type dotenv --output-type dotenv`（`.enc` 扩展名 sops 无法推断类型）：`sops -e --input-type dotenv --output-type dotenv .env > .env.enc`；密文值形如 `KEY=ENC[AES256_DATA,...]`，满足 `grep 'PG_PASSWORD=' .env.enc` 命中 ENC。
- 解密注入（sops 3.13.3 实测修正 2026-10-06）：`exec-env` 不识别 `--input-type`（全局 flag 在子命令上下文不生效），dotenv 类型仅按**文件扩展名**推断，且要求恰 2 个位置参数（文件 + 单条命令串，由 shell 整体执行）——原拟 `sops exec-env --input-type dotenv .env.enc docker compose up -d` 实测报 `missing file to decrypt`，不可用。实际形态：以 `.env` 后缀符号链接指向 `.env.enc` 后包装启动命令（仓库内零新增文件，链接建在 /tmp）：`SOPS_AGE_KEY_FILE=<成员本地私钥> sops exec-env /tmp/exec.env 'docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d'`。已全链路验证：进程注入 12 个敏感变量长度与解密源逐项一致；全栈启动 exit 0，容器内 7/7 关键键为真实值，5 个健康端点 200。私钥不进任何 CI 环境变量。
- 注入层级现状（2026-10-06 验证发现，经用户决议增补）：`docker-compose(.dev).yml` 各应用服务硬编码 `env_file: .env`，容器内密钥由 compose 启动时从磁盘明文 `.env` 读取，exec-env 进程注入仅覆盖 compose 级 `${VAR}` 插值（如 mcp-gateway 的 MCP_GATEWAY_* 默认值插值）。因此「成员删除本地明文 `.env`」**暂不可行**（删除后 `up` 因 env_file 缺失直接失败）：持有者 SHALL 暂保留本地 `.env`（gitignore 已防回流）；compose 注入链改造（去 env_file 改全量 environment 透传，或部署文档固化符号链接方案）列为**后续独立 change**，不在本 change 范围。本批安全价值不受影响：明文永不入库、仓库唯一密钥形态为 `.env.enc`、解密能力已全链路验证。
- `.gitignore` 三处变更：新增 `!.env.enc`（被现有 `.env.*` 通配命中需显式反忽略）、删除误写的 `.github/` 行（否则 cd.yml 又要 force-add）、新增 `/backups/`。
- 备选：git-crypt（权限粒度粗、无 per-field 加密、密钥管理弱）、Vault/KMS（有服务端依赖，违背景）；均不取。

### D6：备份脚本全部走 docker compose，不依赖宿主安装客户端

脚本放在 `scripts/backup/`，统一从仓库根执行，用 **compose 服务名**（`postgres`/`etcd`/`minio`/`elasticsearch`）经 `docker compose exec -T` 或一次性 `docker compose run --rm` 调用，避免硬编码带项目前缀的容器名；连接参数从 `.env`/compose 环境读取（PG_USER/PG_DB/PG_PASSWORD、minioadmin）。

- **PG**：`pg_dump -U $PG_USER -d $PG_DB` 经 gzip 输出 `backups/pg/pg_<YYYYmmdd_HHMMSS>.sql.gz`；恢复 `gunzip -c | docker compose exec -T postgres psql`（恢复前由执行者手工或脚本确认目标库已清空，脚本 echo 风险提示）。
- **Milvus**：先探测 backup sidecar（`http://milvus-backup:8080` 健康即走 2.4 backup API，产物落 `backups/milvus/<ts>/api/`）；不可用则降级——etcd 容器内 `etcdctl snapshot save` 后 `docker compose cp` 到宿主，另起一次性 `minio/mc` 容器加入 compose 网络 `mc mirror local/minio a-bucket` 打包为 tar，落 `backups/milvus/<ts>/fallback/`，日志显式记录降级。恢复脚本两条路径各自对应。当前部署无 milvus-backup sidecar，首次验收预期走降级路径；未来加 sidecar 后脚本零改动自动升级。
- **ES**：给 elasticsearch 服务加 `-Epath.repo=/usr/share/elasticsearch/backup` 并 bind mount `./backups/es`；脚本 `PUT _snapshot/ai_collection {"type":"fs","settings":{"location":"backup"}}`（幂等），`PUT _snapshot/ai_collection/snap_<ts>?wait_for_completion=true`，恢复 `POST _snapshot/.../snap_<ts>/_restore`。这是对 docker-compose.yml 的唯一改动，仅加一个参数一行挂载，不动镜像/数据卷/网络。
- **编排**：`backup_all.sh` 串行 pg→milvus→es，每步 `test -s` 非空校验，失败即 exit 非零并标明存储类型；日志 `backups/logs/backup_<ts>.log`；`find backups/{pg,milvus,es} -mtime +7 -delete`；文件带秒级时间戳保证同日重复不覆盖。

### D7：CD 门禁前置——ci.yml 暴露 workflow_call，cd.yml 以 reusable workflow 调用

GitHub Actions 不支持跨 workflow 的 `needs`，且 `workflow_run` 不在 tag ref 上触发。方案：ci.yml 触发器增加 `workflow_call`（不改变 push/PR 行为）；cd.yml 两个 job——`gate`（`uses: ./.github/workflows/ci.yml`，对 tag 同一提交执行全部 6 门禁）→ `build-push`（`needs: gate`）。这样「CD 依赖 CI 通过」是流水线内强约束，不是约定。
- 备选：只依赖 branch protection 上的 required checks。tag push 不经过 PR，required checks 对 tag 场景不生效，会留下绕过窗口，不取。

### D8：CD 构建矩阵与 tag 规则

- build-push 内部再用 matrix 列出 8 个服务（字符串即 `services/<svc>/Dockerfile` 的 `<svc>`），`docker/build-push-action` 以仓库根为 context、`services/${{ matrix.svc }}/Dockerfile` 为 dockerfile；`docker/metadata-action` 产出两个 tag：`.../ai-collection/<svc>:${GITHUB_REF_NAME}` 与 `:latest`。
- 登录用 `docker/login-action`，registry `crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com`（新个人版实例独立域名，见 Context 修正），用户名/密码取 secrets `ACR_USERNAME`/`ACR_PASSWORD`；push 内容用 `***` 遮蔽，workflow 不 echo 凭证。
- 触发：`on: push: tags: ['v*']`（另可加 `workflow_dispatch` 手工补跑，输入 tag 参数）。普通 main 推送不触发。
- 仅 linux/amd64（GitHub runner 原生 amd64，不显式 setup-qemu/buildx 多平台）；不构建 gradio（`deploy/gradio/Dockerfile`）、ES 自建镜像、ocr、embedding/rerank mock、auth 等 services/ 之外制品。
- 备选：把 gradio 凑成「第 9 个」。验收明确 8 服务且 gradio 属 rag 演示前端、发版节奏独立，后续可单独加 workflow。

### D9： secrets 与备份目录的生命周期

- age keypair 用 `age-keygen` 在成员本机生成；公钥贴入 `.sops.yaml`，私钥二维码/密码库分发至少 2 人；`.env.enc` 首次生成后：`.env.bak` 即刻删除；本地明文 `.env` 因 compose `env_file: .env` 硬编码依赖由持有者**暂保留**（gitignore 防回流），待注入链改造（后续 change，见 D5 注入层级现状）完成后再删（脚本不删文件，只给 checklist），CI/PR 靠 gitignore 保证不回流。
- `backups/` 整体 gitignore（含 `logs/`）；脚本 `mkdir -p` 自愈创建，不需要入库占位文件。

## Risks / Trade-offs

- [5 个服务测试在 CI 一次性全亮红，暴露存量本地未跑测试的失败] → 预期内风险；matrix 的价值正是暴露问题。apply 时按服务逐个点亮（先 data-platform/mcp-gateway 等小集，再 rag/qc 大集），失败只修「CI 环境适配」（依赖/路径/PYTHONPATH），不改测试断言；若某服务因真实测试缺陷失败，停下报用户，不在本 change 内修业务。
- [`sops exec-env` 对 dotenv 的 KEY=value 解析与 compose 插值差异] → 验收项已覆盖「容器内 env 含真实值」；对含空格/特殊字符的密钥（AES_KEY hex 无特殊字符）天然安全；落地时用 `.env.enc` 全字段 diff（解密后与原文件 diff）兜底。
- [ES 加 path.repo 参数需 recreate elasticsearch 容器] → 单节点数据在命名卷 `es_data` 中，recreate 不丢数据；安排在备份任务前、低风险窗口执行，并先做一次 ES 快照之外的数据确认（索引可查）。
- [Milvus 降级备份（etcd snapshot + minio mirror）非官方在线一致性备份，恢复时需停 milvus] → restore 脚本明确「停 milvus→恢复 etcd/minio→启动」流程；本期定位为可接受的冷备；启用官方 backup sidecar 后自动升级为在线一致备份。
- [ACR 凭证为 RAM 子账号长期密钥] → 仅授该命名空间 push/pull 最小权限；后续可换 OIDC 短期凭证（OpenID Connect 联合登录），不在本期。
- [`latest` tag 被覆盖影响拉 latest 的环境] → 本期无自动部署、没有环境跟 latest，风险窗口为零；规范要求部署一律 pin git tag，latest 仅作便利指针。
- [备份脚本依赖 compose 项目名与 .env 内连接参数] → 全部从仓库根、经 compose 运行，参数读环境；脚本头部 `set -euo pipefail` 并校验关键变量非空。

## Migration Plan

1. CI：先以新 ci.yml 在一个分支上跑全矩阵，逐服务点亮至 6 job 全绿，再合主干；回滚为 revert 单个 workflow 文件（旧 ci.yml 行为在 git 历史中）。
2. 密钥：生成 age 对→分发私钥→生成并入库 `.env.enc`→本地切换启动方式验证（sops exec-env 以 `.env` 后缀符号链接包装，见 D5 实测修正）→成员**暂保留**本地明文 `.env`（compose env_file 依赖，注入链改造为后续 change，届时再删）；回滚=本地保留明文副本期间可直接 `docker compose up -d`（.env.enc 入库不影响旧方式），该回滚窗口在注入链改造完成前持续可用，关闭动作随改造一并执行。
3. 备份：先在 dev compose 环境跑 backup_all + 三次恢复演练（PG count/抽样、ES 快照恢复、Milvus 停服恢复），通过后再挂人工 cron。
4. CD：在 GitHub 配好 ACR secrets 后打 `v0.1.0-rc1` 预演 tag 验证 8 镜像可 pull，再打正式 `v0.1.0`；回滚=删除 tag 停止触发，ACR 中镜像可保留或手动删除。

## Open Questions

- Milvus 官方 backup sidecar 是否在生产部署时一并加入 compose（加入则主路径自动生效），可在部署阶段定，不影响本 change 脚本结构与任务拆分。
- ACR RAM 账号具体由谁创建、授予何种最小权限策略，属组织流程，apply 时由用户提供 secrets 即可。
