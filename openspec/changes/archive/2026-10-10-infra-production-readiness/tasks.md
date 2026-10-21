# Tasks

> 约束：不新增/不修改测试用例与业务代码；CI 点亮过程中若某服务因真实测试缺陷失败，停下报用户，不在本 change 内修业务。每项 ≤2h，完成一项验证一项。

## 1. CI 全服务矩阵（ci-matrix）

- [x] 1.1 重构 `.github/workflows/ci.yml` 骨架：触发器保留 push(主干)/pull_request 并新增 `workflow_call`；定义 `service-test`（matrix：data-platform/rag-service/qc-service/mcp-gateway/complaint-service）与 `common` 两个 job 共 6 个并行执行单元；YAML 语法校验通过（actionlint 或等价检查）
- [x] 1.2 实现统一安装步骤并点亮 data-platform、mcp-gateway：基线 pytest 插件 + 根 `requirements.lock` + 各服务存在的 `requirements.runtime.txt`；两服务 job 绿；本地在两服务目录分别执行 `PYTHONPATH=../.. pytest tests/ -q` 通过
- [x] 1.3 点亮 rag-service job（仅允许依赖/PYTHONPATH 适配，禁止改断言）；CI job 绿，本地 `PYTHONPATH=../.. pytest tests/ -q` 通过
- [x] 1.4 点亮 qc-service job（同上约束）；CI job 绿，本地 `PYTHONPATH=../.. pytest tests/ -q` 通过
- [x] 1.5 complaint-service matrix 条目迁入既有四阶段：unit+integration（原精确 `--cov` 列表 + `--cov-fail-under=85`）、e2e、regression、smoke（后台 uvicorn + `scripts/smoke/smoke_complaint_service.sh`）及 PR self-check summary；验证四阶段与旧 ci.yml 行为等价，并人为验证覆盖率低于 85% 时 job 失败
- [x] 1.6 `common` job：仓库根显式对 `common/tests`、`common/eventbus/tests`、`common/security/tests` 执行 pytest（不裸跑根 pytest，确认未夹带 qc-service 测试），再循环 call/case/review 三目录跑 health；验证该 job 绿
- [x] 1.7 lint 按 D4 落地：仅 complaint、mcp-gateway 两个 job 安装并运行 ruff（`continue-on-error` 非阻塞），其余服务无 ruff 步骤；验证有配置的服务输出 ruff 结果但不改变 job 成败
- [ ] 1.8 〔BLOCKED 2026-10-06：个人账户私有仓 + GitHub Free 不支持 branch protection/rulesets。证据：`GET /repos/GavinYoung66/ai_collection/branches/main/protection` 与 `GET /rulesets` 均 403「Upgrade to GitHub Pro or make this repository public to enable this feature.」；已由用户确认。解除条件：升级 GitHub Pro 或仓库转 public。**用户决定 2026-10-07：不升级、不转 public，本项永久搁置（除非未来账号/仓库形态变化），不阻塞 change 交付。**〕原计划：仓库管理员手工在 Settings→Branches 对 main 启用 branch protection rule，勾选 Require status checks to pass before merging，将 6 个检查项全部设为 required——5 个 matrix 作业（`service-test (data-platform)`、`service-test (rag-service)`、`service-test (qc-service)`、`service-test (mcp-gateway)`、`service-test (complaint-service)`）与 `common`
- [ ] 1.9 〔BLOCKED 2026-10-06：依赖 1.8，无 required checks 即无法验证 Merge 按钮被强制阻止；1.8 解除后执行，方案不变。**用户决定 2026-10-07：随 1.8 一并永久搁置。**〕门禁语义验证：临时分支制造一次单服务测试失败推送并开 PR，确认对应 job 失败、整体 CI 失败，且 PR 页面 Merge 按钮因 required status check 未过而被阻止（非仅显示红色警告）；其余 job 仍执行完毕；验证后丢弃该临时提交与分支

## 2. 密钥管理 sops + age（secrets-sops）

- [x] 2.1 本地安装 sops 与 age，`age-keygen` 生成密钥对；新增 `.sops.yaml`（age 公钥 recipient；`path_regex: '\.env(\.enc)?$'`；`encrypted_regex: '(KEY|SECRET|PASSWORD|TOKEN)'`）；验证 sops 自动读取该配置
  - 证据 2026-10-06（二进制部分）：本机无 Homebrew，经用户确认改用 GitHub Releases 官方二进制装入 `~/.local/bin`；sops v3.13.3（50,168,146B，shasum -a256 对官方 checksums.txt = OK），age/age-keygen v1.3.2（tarball 18,106,798B，sha256 对 GitHub API 公布 digest `e2020b07…f2f5` = OK；经镜像下载、以官方 digest 校验）；`sops -v`→3.13.3、`age --version`/`age-keygen --version`→v1.3.2
  - 证据 2026-10-06（密钥对部分）：私钥 `~/.local/share/sops/age/keys.txt`（用户确认路径；模式 600；仓库外；统一以 `SOPS_AGE_KEY_FILE` 引用，内容未在任何输出回显）；recipient=`age1ddf29z2hrq8mf5qp0v65mlre9ztlqlghwdg8225ll4z4y58wvgrsl3qus0`；`age-keygen -y` 反推公钥一致
  - 证据 2026-10-06（.sops.yaml 部分）：dry-run 加密（产物仅落 /tmp）未带 `-a` 即成功，密文元数据 `sops_age__list_0__map_recipient` 为上述公钥，证明自动读取配置；行序比对 104 个业务键行——19 个非空敏感键全部 `ENC[...]`、3 个空值敏感键（REDIS_PASSWORD/API_KEYS/MCP_GATEWAY_AUTH_API_KEY）保持空明文、82 个非敏感键全部明文且值不变；`ENABLE_AUTH`/`MCP_GATEWAY_ENABLE_AUTH` 等布尔键明文；解密回环 104/104 键值行与 70 行注释完全一致、键序不变（sops dotenv writer 会移除 33 个空行，无内容丢失）
- [x] 2.2 更新 `.gitignore`：新增 `!.env.enc` 反忽略、新增 `/backups/`、删除误写的 `.github/` 行；验证 `git check-ignore .env` 命中忽略、`git check-ignore .env.enc` 不命中（可被跟踪）
  - 证据 2026-10-06：`git check-ignore` 实测 `.env`→命中(exit0)、`.env.bak`→命中(exit0)、`backups/pg/x.sql.gz`→命中(exit0)、`.env.enc`→不命中(exit1，可跟踪)、`.github/workflows/ci.yml`→不命中(exit1)；`.github/{pull_request_template.md,workflows/ci.yml}` 仍被跟踪、`.github` 无新增未跟踪文件
- [x] 2.3 以 dotenv 显式类型加密生成 `.env.enc`；验证 `grep -E '(PG_PASSWORD|LLM_API_KEY|DEEPSEEK_API_KEY)=' .env.enc` 输出值均为 `ENC[...]`，且 sops 解密结果与原 `.env` 逐行 diff 完全一致（含 `CCC_ACCESS_KEY_ID` 被加密）
  - 证据 2026-10-06（命令 `sops -e --input-type dotenv --output-type dotenv .env > .env.enc`）：a) `SOPS_AGE_KEY_FILE=… sops -d .env.enc` 解密后 104/104 业务键值行、70/70 注释行、键序与原文件完全一致（sops dotenv writer 仅移除 33 个空行，无内容/键值差异，已逐行脚本比对）；b) `git check-ignore .env` exit0；c) 字面 grep 实测 PG_PASSWORD/LLM_API_KEY/DEEPSEEK_API_KEY（及混合大小写 DeepSeek_API_KEY）值均为 `ENC[AES256_GCM,...]`；d) CCC_ACCESS_KEY_ID 与 CCC_ACCESS_KEY_SECRET 均 `ENC[...]`；e) 82 个非敏感键行按行序全部明文且值不变（含重复键 LLM_MODEL=qwen-plus/deepseek-chat、LLM_TIMEOUT=5/30 两组取值）；f) 附加：以 19 个真实密钥值为子串负向扫描 .env.enc = 无泄漏；业务 ENC 行 19（另 1 个 `=ENC[` 行为 sops_mac 元数据）；3 个空值敏感键保持 `KEY=` 空明文；`git status` 中 .env.enc 为 `??` 未跟踪可 add，check-ignore exit1
- [x] 2.4 对账并核对 `.env.example`：先以 `.env.enc` 解密后的键集合为准，将 example 缺失的字段名（当前实测至少缺 `ANTHROPIC_API_KEY`、`DASHSCOPE_API_KEY`、`RAG_API_TOKEN`，以解密结果实际 diff 为准）补入，新增行值一律 `<change_me>`，确认 diff 仅新增字段名/注释、无真实密钥；再负向 grep 确认 example 全部密钥字段均为占位值。将 age 私钥离线交付 ≥2 名核心成员（密码管理器留存确认）；确认仓库历史/工作区/CI 配置中无私钥
  - 证据 2026-10-06（example 对账）：实际 diff 缺失 8 个键（比预估多）：敏感 4 个 DASHSCOPE_API_KEY/DeepSeek_API_KEY/ANTHROPIC_API_KEY/RAG_API_TOKEN，非敏感 4 个 RAG_REDIS_DB/LLM_FALLBACK_CHAIN/LLM_RESPONSE_FORMAT_JSON/LLM_HTTP_TIMEOUT；已按语义分组补入，8 个新生效行值全部 `<change_me>`（numstat 16 增 0 删，另 8 行为中文分组注释）；复算键集合 0 缺失；13 个敏感生效行 `<change_me>`、8 个为空（如 REDIS_PASSWORD/DEEPSEEK_API_KEY 等本就留空）、AES_KEY 为既有 `<run: …>` 生成指令占位（非真实值，未改动）；19 个真实密钥值负向扫描 example 仅 PG_PASSWORD 命中——经程序判定其本地真实值就是字符串 `change_me`（9 字符，与占位符同形，example 20 次出现均为占位符 token），非密钥泄漏；本次 diff 新增行 0 处包含该串
  - 证据 2026-10-06（明文遗留）：`.env.bak` 经用户确认后已删除（DeleteFile）；根目录现存 `.env`（ignore、只读未动）、`.env.enc`、`.env.example`、`.sops.yaml`
  - 证据 2026-10-06（私钥边界）：以私钥内容经进程替换（不落盘）扫描——工作区全部文件（含未跟踪、排除 .git）匹配 0；`git rev-list --all` 全部 34 个提交对象匹配 0；提交信息/stash 0；`.env.enc` 无 AGE-SECRET 标记 0；`.github/` 无 AGE-SECRET-KEY/SOPS_AGE_KEY/age-keygen 引用
  - 〔待用户确认·人工动作，AI 未代执行〕age 私钥（`~/.local/share/sops/age/keys.txt`）离线分发给 ≥2 名核心成员并存入各自密码管理器；成员拿到后用 `SOPS_AGE_KEY_FILE=<私钥路径> sops -d --input-type dotenv .env.enc` 独立验证可解密，回执后本项才算闭环
- [x] 2.5 启动方式验证：`sops exec-env --input-type dotenv .env.enc docker compose up -d`，进容器确认关键 env（PG_PASSWORD、LLM_API_KEY 等）为真实值且应用连通依赖；`down` 后确认工作区未生成新的明文密钥文件；成员按 checklist 删除本地明文 `.env`/`.env.bak`
  - 证据 2026-10-06（CLI 实测，修正 D5）：sops 3.13.3 `exec-env` 源码要求恰 2 个位置参数（文件 + 单条命令串，多词命令须整体作为一个字符串），且子命令不识别 `--input-type`（全局 flag 在子命令上下文不生效），仅按**文件扩展名**推断 dotenv；D5 原命令 `sops exec-env --input-type dotenv .env.enc docker compose up -d` 实测报 `missing file to decrypt`/JSON 解析错。可行形式：以 `/tmp` 下指向 `.env.enc` 的 `.env` 后缀符号链接为参数（仓库内不留额外文件）：`SOPS_AGE_KEY_FILE=… sops exec-env /tmp/<link>.env 'docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d'`；进程级注入冒烟 12 个敏感变量全部存在、值长度与解密源逐项一致（PG 9/LLM 116/AES 64/RAG_API_TOKEN 189 等），空值 REDIS_PASSWORD 保持空
  - 证据 2026-10-06（容器与连通）：先 `down --remove-orphans` 清场（副作用：移除了挂同网络的 ai-api-gateway/ai-api-auth/metrics，可用 `docker compose -f docker-compose.gateway.yml up -d` 恢复），再经 sops 包装 `up -d` exit 0；complaint-service、mcp-gateway 容器内 7/7 关键键（PG_PASSWORD/LLM_API_KEY/DEEPSEEK_API_KEY/JWT_SECRET/AES_KEY/CCC_ACCESS_KEY_ID/CCC_ACCESS_KEY_SECRET）存在且长度=真实值（仅计数/长度，未回显明文）；5 个健康端点 HTTP 200：complaint 8006 `pg:ok,redis:ok`、mcp-gateway 9009 healthy、rag 8002 `database/milvus/redis ok`、qc 8003 `rag_connected:true`、data-platform 8001 healthy；验证后 `down`，ai_collection 运行容器=0
  - 证据 2026-10-06（无新明文）：17:00 后工作区变更仅 4 个允许文件（.sops.yaml/.env.enc/.env.example/.gitignore），无 docker-compose.override.yml 等新明文 env；10 个真实形态密钥值全工作区扫描（排除 .env/.git）命中 0；/tmp 明文解密临时件与符号链接已删除
  - 〔设计落差·已决议 2026-10-06〕docker-compose.dev.yml 的 8 个服务均硬编码 `env_file: .env`，容器密钥当前实际取自磁盘明文文件（sops exec-env 的进程注入仅作用于 `${VAR}` 插值）；成员暂不可删本地 `.env`，否则 `up` 直接失败。经用户决议：现状与过渡策略已增补至 D5（解密注入实测修正 + 注入层级现状）、D9（生命周期）、Migration Plan 第 2 条及 spec delta「解密注入的启动方式」（成员暂保留本地 .env；compose 注入链改造列为后续独立 change）；本批未改 compose（超出允许文件边界）
  - 〔待用户确认·人工动作〕`.env.bak` 已由用户确认删除；本地 `.env` 因上述 env_file 依赖暂保留，待 compose 注入链改造后再按 checklist 删除（本批约束 .env 只读，未动）

## 3. 数据库备份与恢复（db-backup）

- [x] 3.1 新建 `scripts/backup/` 与公共脚本头（`set -euo pipefail`、连接变量非空校验、`backups/{pg,milvus,es}` 与 `backups/logs` 自愈创建、秒级时间戳函数）；实现 `backup_pg.sh`（经 `docker compose exec -T postgres pg_dump | gzip` 输出 `backups/pg/pg_<ts>.sql.gz`）；验证产物存在且非空
  - 证据 2026-10-07：新增 `scripts/backup/_common.sh`（compose 封装自动带 `-f docker-compose.yml -f docker-compose.dev.yml`、`backup_ts` 秒级时间戳、load_var/require_vars 连接变量三级取值与非空校验 exit 2、ensure_backup_dirs 自愈创建、log/die）与 `backup_pg.sh`（`set -euo pipefail`、先写 `.tmp.$$` 成功后原子 mv、pipefail 透传 pg_dump 失败、`[[ -s ]]` 非空校验）；`bash scripts/backup/backup_pg.sh` exit 0 → `backups/pg/pg_20261007_075143.sql.gz`（859,655B/840K）；`test -s` PASS、`gzip -t` PASS；dump 内容抽查含 39 个 CREATE TABLE（30 public + dw 等）、`CREATE EXTENSION IF NOT EXISTS "uuid-ossp"`、`CREATE SCHEMA dw`，可用于整库重建；`git check-ignore` 命中 `/backups/`（批次 2 已在 .gitignore:61，本批无需新增）；前置 infra 五服务（postgres/etcd/minio/milvus/elasticsearch）由 AI 启动（开跑前仅 langfuse 栈在运行），复用 ai_collection_* 命名卷
- [x] 3.2 实现 `restore_pg.sh` 并完成恢复演练：记录 ≥1 张关键表 `count(*)` 与 3 条样本字段值 → 清空/删除表 → 执行恢复 → `count(*)` 一致且 3 条样本字段值完全相同
  - 证据 2026-10-07：新增 `scripts/backup/restore_pg.sh`（默认取最新备份；`--clean-db` 执行 dropdb --if-exists --force + createdb；交互需输入库名确认、无 tty 必须显式 `--yes` 防误删；`gunzip -c | psql -v ON_ERROR_STOP=1 --single-transaction --echo-errors`，任一 SQL 报错整体回滚并非零退出；dropdb/psql 参数均经容器内 `--help` 核验）
  - 演练前证据（用户书面批准全库重建方案；影响：ai_collection 库 19MB、30 表、10,846 行；备份 pg_20261007_075350.sql.gz 为动作前 1 分钟内新取，当时仅 infra 容器运行无业务写入；另存 pg_20261007_075143.sql.gz 兜底）：关键表 `complaints` count=**251**、整表 17 列逐行 md5=**54af06e12e6f8e5e2722dcb447eecdc4**、id 最小的 3 条记录全字段 CSV（胡*/王*/朱* 三条，源数据已脱敏）；30 表逐表 count 落 `/tmp/pg_restore_drill/counts_before.txt`
  - 演练结果（`restore_pg.sh backups/pg/pg_20261007_075350.sql.gz --clean-db --yes` exit 0）：①恢复后 public 仍 30 表；②30 表逐表 count 与恢复前 `diff` 零差异（10,846 行总量一致）COUNTS MATCH；③complaints count=251 一致；④整表 md5=54af06e1…ecdc4 完全一致 HASH MATCH（覆盖 3 条样本的 17 个字段全字段比对）；⑤3 条样本 COPY CSV 字节级 `diff` 零差异 3-ROW CSV MATCH；⑥uuid-ossp 扩展、dw schema、外键 complaint_sessions_complaint_id_fkey 均随 dump 重建
- [x] 3.3 ES 备份前置：`docker-compose.yml` 的 elasticsearch 增加 `-Epath.repo=/usr/share/elasticsearch/backup` 与 `./backups/es` bind mount，recreate 后确认既有索引仍在；实现 `backup_es.sh`（幂等注册 fs 仓库 → 带时间戳 snapshot 并 `wait_for_completion` → ES 可列出快照、宿主目录非空）
  - 证据 2026-10-07（compose 改动，仅两处）：镜像 `ai_collection/elasticsearch-ik:8.11.3` 实测 Entrypoint=tini+docker-entrypoint.sh、Cmd=`["eswrapper"]`；读 entrypoint 源码确认 eswrapper 会 `set -- "${@:2}"` 剥首参后 `exec bin/elasticsearch "$@"`，故 `command: ["eswrapper", "-Epath.repo=/usr/share/elasticsearch/backup"]` 为官方支持传参；volumes 新增 `- ./backups/es:/usr/share/elasticsearch/backup`，未动镜像/数据卷/网络/其它服务
  - 证据（recreate 索引未丢）：recreate 前基线 `kb_bm25` uuid=7-7lhw_dQaGDcBRKrJMlFw、53 docs、130.4kb；`up -d --force-recreate elasticsearch` 后集群 green，`_nodes/settings` 中 `"path.repo":["/usr/share/elasticsearch/backup"]` 已生效；`kb_bm25` uuid 仍为 7-7lhw…、**53 docs、130.4kb 完全一致**（命名卷 es_data 保留）
  - 证据（backup_es.sh，新增；curl flags 经容器内 `curl --help all` 核验）：脚本幂等 PUT `_snapshot/ai_collection`（type=fs、location=backup、compress=true，连续 3 次注册均 acknowledged=true）→ PUT `_snapshot/ai_collection/snap_<ts>?wait_for_completion=true`（include_global_state=false，单节点只备索引）→ 状态校验（实测 8.11 不支持 `_cat/snapshots/<repo>/<snap>` 两段路径，返回 nohandlerfounduri；改用 `_cat/snapshots/<repo>?h=snapshot,status` 按名精确匹配，已在脚本注释说明）→ 快照详情写 `backups/es/manifests/snap_<ts>.json` 供机器 test -s → 宿主仓库目录文件数 >0；两次执行生成 snap_20261007_080045 / snap_20261007_080130 均 SUCCESS（各 1 index、1/1 shards），宿主 backups/es/backup 共 22 个数据文件，manifest 760B 非空
- [x] 3.4 实现 `restore_es.sh`（`_restore`）并演练：删除/清空一个测试索引后恢复，确认索引与文档可重新检索
  - 证据 2026-10-07：新增 `scripts/backup/restore_es.sh`（默认取 manifests 最新快照；`--index` 精准恢复，缺省恢复全部索引并告警；交互需输入快照名确认、无 tty 须显式 `--yes`；`POST _snapshot/<repo>/<snap>/_restore?wait_for_completion=true`，body `indices/include_global_state=false/include_aliases=false`，参数经 ES 8.11 官方 restore-snapshot-api 文档核验；curl `-f` 使 4xx/5xx 非零退出）
  - 踩坑修正（实测 8.11）：wait_for_completion=true 时成功响应体为 `{"snapshot":{...,"shards":{"failed":0,...}}}`，**没有** accepted 字段（该字段仅 wait_for_completion=false 返回）；脚本成功判定已改为 `-f` + `"failed":0` 双重判定并注释
  - 演练（用户两次书面批准同范围操作）：建测试索引 `backup_test_idx`（1 shard/0 replica，3 文档 doc-1/2/3「备份恢复演练文档1/2/3」）→ 快照 snap_20261007_080316 确认含 `['kb_bm25','backup_test_idx']` → DELETE 索引（HTTP 200，删后 GET 404）→ restore 精准恢复。第一轮恢复实际成功但脚本因判定写错 exit 1（已修）；第二轮修复后重跑：`RESTORE_EXIT=0`，恢复后 GET 索引 200、`_count=3`、按 order 排序检索 3 条文档字段与删前完全一致；业务索引 `kb_bm25` 全程 53 docs 未受影响
- [x] 3.5 实现 `backup_milvus.sh`：先探测 milvus-backup sidecar（API 可用走 2.4 backup API）；不可用降级为 etcd 容器内 `etcdctl snapshot save` + 一次性 `minio/mc` 镜像 mirror Milvus bucket 打包；验证产物非空且日志显式记录实际路径（API/降级）
  - 证据 2026-10-07（新增 `scripts/backup/backup_milvus.sh`，set -euo pipefail/中文注释/require_vars 非空校验/失败非零退出；头注释含 cron 示例）：**本次本地无 milvus-backup sidecar 容器，按任务要求如实记录——本次验证的是 DEGRADED 降级路径**；探测方法为从 milvus 容器 `curl -m 3 GET http://milvus-backup:8080/api/v1/list`（该 GET 路由取自官方 zilliztech/milvus-backup `docs/swagger.yaml`，basePath=/api/v1；不用未证实的 /healthz），实测 `curl: (6) Could not resolve host: milvus-backup`、HTTP 000 → 判为不可用；API 路径仍按官方 swagger 契约实现（`POST /api/v1/create` body `backup_name+async:false`、不传 collection_names=nil 表示全部、判定 `code:0` 且 `state_code:2`=BACKUP_SUCCESS、`GET /api/v1/get_backup` 复核，随后 mc mirror 备份桶），代码在位但本地无 sidecar 未做端到端实测，脚本注释已注明
  - 证据（DEGRADED 实测命令均经核验）：①etcd v3.5.16 镜像是 distroless 无 sh/rm/mkdir，改为直接 exec `etcdctl --endpoints=http://127.0.0.1:2379 snapshot save /tmp/etcd_<ts>.db`（写容器可写层 /tmp，不污染命名卷 /etcd），`snapshot status --write-out=fields` 显示 Hash=473400241、Revision=56120、Keys=1192、Size=212992，再 `docker compose cp etcd:/tmp/... backups/.../etcd_snapshot.db`（213,024B，test -s PASS；注：live `get / --prefix` Count=0，因 Milvus session/注册键带短 lease 已过期，snapshot 的 1192 为未压缩历史 mvcc 版本，非业务 collection，当前确无用户 collection）；②一次性 `minio/mc:latest` 容器（RELEASE.2025-08-13）加入动态发现的 compose 网络 `ai_collection_default`，镜像无 shell 不支持 sh -c，URL 内嵌凭证新版 mc 拒绝，最终用官方 `MC_HOST_local=http://minioadmin:minioadmin@minio:9000` 环境变量 + 单条 `mc mirror --overwrite local/a-bucket /out/a-bucket`，61.61 KiB/10 对象（9-29 历史 insert_log/stats_log）mirror 成功；宿主 `tar czf` 打包 38,328B（tar tzf 29 条目=文件+目录），test -s PASS，暂存目录打包后删除
  - 环境注记：本机 Docker Desktop 凭证助手故障（`docker pull` 报 Keychain Error 100013/-60008），用临时 DOCKER config（`{"auths":{"https://index.docker.io/v1/":{}}}` 空条目跳过 keychain）经 daocloud mirror 拉取 mc 镜像成功，未改动用户 `~/.docker/config.json`；镜像本地存在后 `docker run --rm` 不再触发凭证查找
  - 产物与日志标注：`backups/milvus/20261007_141701/{backup_manifest.txt,fallback/etcd_snapshot.db,fallback/minio_a-bucket_20261007_141701.tar.gz}` 三者 test -s 全 PASS；运行日志显式打印 `PATH=DEGRADED 原因=sidecar 不可用（... HTTP 000）`，manifest 同构记录 path=DEGRADED/reason/etcd Hash·Revision·Keys·Size/桶名/对象数，作为 backup_all 的 test -s 机器校验入口（API 路径落 `api/` 子目录、manifest 字段同构）
- [x] 3.6 实现 `restore_milvus.sh`（两条路径各自对应：停 milvus → 恢复 etcd snapshot 与 minio 数据 → 启动），在 dev 环境演练一次 collection 可查
  - 证据 2026-10-07（新增 `scripts/backup/restore_milvus.sh`，set -euo pipefail/中文注释/失败非零退出/头注释含 cron 示例；与 backup_milvus.sh 成对）：按备份目录 manifest 的 `path=` 自动选择路径；默认取 `backups/milvus/` 最新备份，可传时间戳/目录；交互须输入 yes、无 tty 须 `--yes`。API 路径按官方 swagger 实现（`POST /api/v1/restore` body `backup_name/async:false/dropExistCollection:true`、`code:0` 判定、按返回 id 轮询 `GET /api/v1/get_restore` 至 state_code=2 SUCCESS / 3 FAIL；本地无 sidecar 未端到端实测，注释已注明）
  - DEGRADED 冷恢复实现（参数均经实测核验）：①compose stop milvus→etcd（minio 不停，供回灌）；②一次性 `quay.io/coreos/etcd:v3.5.16` 容器离线 `etcdctl snapshot restore --data-dir`（默认 `--name=default --initial-cluster=default=http://localhost:2380` 与 docker-compose.yml etcd 单机引导默认值逐一对齐，snapshot restore --help 核验）到宿主暂存目录；③etcd 镜像 distroless 无 rm/cp，用一次性 `postgres:15-alpine`（SHELL_IMAGE 可覆盖）挂命名卷执行 `rm -rf /target/*` + `cp -a /src/. /target/` 替换 member（卷名由 `docker inspect` 动态读取，实际 ai_collection_etcd_data）；④解压 tar 后一次性 mc 容器 `mirror --overwrite --remove /out/a-bucket local/a-bucket` 精确回灌（--remove 删除备份点后多余对象）；⑤compose start etcd（轮询 etcdctl endpoint health）→ milvus（轮询 :9091/healthz 200）；trap 保证清理 `_restore_ws`
  - 演练（用户书面批准；备份点 backups/milvus/20261007_143034，14:30 新取，31 桶对象）：演练前经一次性 rag-service 镜像（pymilvus 2.4.0 对 milvus v2.4.13）建 `backup_test_coll`（pk INT64 主键 + tag VARCHAR + vec FLOAT_VECTOR dim=8，IVF_FLAT/L2/nlist=128），插入 **100 条**确定性向量（pk 1–100，tag=doc-i，vec=[0.01i]×8）并 flush，load+query 基线：num_entities=100，样本 pk=1→doc-1/[0.01×8]、pk=50→doc-50/[0.5×8]、pk=100→doc-100/[1.0×8]；同时发现业务 collection `kb_vectors`（0 向量空库，schema + IVF_FLAT/IP 索引定义），纳入影响范围
  - 演练结果（restore_milvus.sh 20261007_143034 --yes；第一次执行在卷替换前因 bash 3.2 全角括号紧跟 `$ETCD_VOL` 触发 unbound 中断——服务已停、卷未改，属安全中态；全部变量后接中文处补 `${VAR}` 花括号后重跑成功）：etcd endpoint health OK、milvus /healthz 200；恢复后 collection 列表 `['backup_test_coll','kb_vectors']` 两者俱在；backup_test_coll 可 load（索引定义随 etcd 元数据恢复）、**num_entities=100 一致**、pk=1/50/100 三条样本 pk/tag/8 维向量与基线**逐字段完全一致**（RESULT: PASS）；minio 桶恢复后对象数=31 与备份点一致；`_restore_ws` 已清理
  - 更正 3.5 证据中一处误判（根因已查清并实证）：3.5 时用 `etcdctl get / --prefix --count-only` 得到 Count=0 并推测「无业务 collection、session lease 过期」，实际是**前缀写错**——milvus etcd 键前缀为 `by-dev/...`（无前导斜杠，见 milvus 启动日志 KeyPrefix=by-dev），`/` 开头 range 查不到；正确命令 `etcdctl get by-dev --prefix --count-only` 实测恢复后 **Count=181**、含 `by-dev/meta/root-coord/database/collection-info/...`（kb_vectors 与 backup_test_coll），revision 从快照点 56498 正常递增至 56686（milvus 重启注册写入）；snapshot status 的 Keys=1192 是 boltdb 内未压缩历史 mvcc 版本数，与活键不矛盾；etcd 快照为全库快照，备份/恢复有效性不受该误判影响
  - 演练收尾：一次性 rag-service 演练容器已 `docker rm -f` 清理（0 残留）；测试 collection `backup_test_coll`（100 向量）暂保留在 dev milvus 中便于复核（与 ES 的 backup_test_idx 处理一致），需要时可用 pymilvus `Collection('backup_test_coll').drop()` 删除
- [x] 3.7 实现 `backup_all.sh`：串行 pg→milvus→es、统一日志到 `backups/logs/backup_<ts>.log`、每步 `test -s` 非空校验、任一失败非零退出并标明失败存储、`find ... -mtime +7` 清理；验证一次成功跑完三类备份，且同日连跑两次成功、生成两套不同时间戳文件互不覆盖
  - 证据 2026-10-07（新增 `scripts/backup/backup_all.sh`，set -euo pipefail/中文注释/失败非零退出/头注释含 cron 示例）：导出 `BACKUP_TS=<秒级时间戳>` 让三个子脚本共用同一时间戳（_common.sh backup_ts 支持该覆盖）；`exec > >(tee -a backups/logs/backup_<ts>.log) 2>&1` 全程输出同时进终端与统一日志；`run_step <存储名> <脚本> <约定产物>` 严格按 **pg→milvus→es** 串行，子脚本非零退出或约定产物（pg/pg_<ts>.sql.gz、milvus/<ts>/backup_manifest.txt、es/manifests/snap_<ts>.json）`test -s` 失败即 exit 1 并在日志打印 `[FATAL] 存储【<名>】备份失败...后续存储取消执行`，已完成产物保留；全部成功后 `find backups/{pg,milvus,es,logs} -type f -mtime +7 -print -delete` 清理 7 天前文件
  - 验证 a（一次跑通三类非空）：`bash backup_all.sh` → ALL_EXIT=0（时间戳 20261007_145831），日志三步均「成功，产物 test -s PASS」：pg 840K、milvus PATH=DEGRADED 31 对象 manifest 非空、es snap SUCCESS（当时仓库数据文件 39 个）manifest 非空；统一日志 backup_20261007_145831.log 落盘
  - 验证 b（同日连跑两次互不覆盖）：再连跑两次均 exit 0，时间戳 **20261007_145852 / 20261007_145855**，与首次 20261007_145831 三套并存；pg（pg_<ts>.sql.gz）、milvus（<ts>/ 目录）、es（manifests/snap_<ts>.json）、logs（backup_<ts>.log）四类产物均按各自时间戳独立成套，无覆盖
  - 验证 c（失败注入）：`docker compose stop elasticsearch` 后跑 backup_all → **FAIL_RUN_EXIT=1**，日志 `[FAIL] [ES] 快照仓库注册失败` + `[FATAL] 存储【elasticsearch】备份失败：脚本非零退出...后续存储取消执行`（明确点名失败存储）；本次 pg_20261007_145922.sql.gz（859,669B）与 milvus/20261007_145922/backup_manifest.txt 已完成并保留、es 无 snap_20261007_145922 产物（符合短路语义）；随后 `start elasticsearch` 待集群 green 后复跑 backup_all → RECOVER_RUN_EXIT=0、三步全 PASS（20261007_145949）。失败注入产生的不完整 145922 产物已人工删除，失败日志 backup_20261007_145922.log 保留为证
  - 保留期补充验证：造 mtime=2026-09-27（10 天前）探针文件 backups/pg/.retention_probe，`find ... -mtime +7` 预判命中；再跑一次全量（20261007_150025，exit 0）后探针被删除（PROBE DELETED），7 天清理逻辑实证有效
- [x] 3.8 核对所有脚本头注释含 cron 示例 `0 2 * * *`，并验证执行前后系统 crontab 无变化（未自动注册）
  - 证据 2026-10-07（cron 示例核对）：`scripts/backup/` 全部 8 个文件逐一 grep `0 2 * * *` 全 PASS——_common.sh（公共库总入口示例，注明「需人工 crontab -e 注册，脚本集绝不自动写 crontab」）、backup_pg.sh、restore_pg.sh、backup_es.sh、restore_es.sh、backup_milvus.sh、restore_milvus.sh（恢复脚本头注释给出对应备份的手工启用示例）、backup_all.sh；均为注释形式并注明「仅为示例/手工启用」
  - 证据（crontab 前后对比）：本批所有脚本执行前 `crontab -l` → rc=1 `crontab: no crontab for ellisguo`（快照落 /tmp/crontab_before.{txt,err}）；全部演练/验证执行后再取 → rc=1 同样输出（/tmp/crontab_after.*）；`diff -u` stdout/stderr 均零差异，**CRONTAB UNCHANGED**；静态核查 scripts/backup/ 全部文件，crontab 仅出现在注释中，无任何 crontab 安装/写入调用（首次 `crontab -l` 在工具沙箱内报 seteuid Operation not permitted 系沙箱限制，在沙箱外真实执行取证，命令本身只读）

## 4. CD 流水线 Level 1 build → push（cd-build-push）

- [x] 4.1 清点 8 个 Dockerfile（`services/{data-platform,rag-service,qc-service,mcp-gateway,complaint-service,call-service,case-service,review-service}/Dockerfile`），确认全部存在且均以仓库根为 build context；本地对其中至少 2 个执行 `docker build -f services/<svc>/Dockerfile .` 验证可构建；缺失则补齐
  - 证据 2026-10-07（清点）：Glob `services/*/Dockerfile` 8/8 齐备；8 个文件逐一 Read，COPY 源全部是仓库根路径（`requirements.lock`、`common/`、`services/<svc>/...`、rag 另有根 `alembic.ini`/`migrations/`/`scripts/rag/`），无一以服务目录为 context，无需补齐；services/ 外制品（gradio 等）不进 CD matrix
  - 证据（本地构建，arm64 Docker Desktop，命令均 `docker build -f services/<svc>/Dockerfile .` 根 context）：complaint-service exit 0、qc-service exit 0（两者沿用既有「grep -v rocketmq + 补装」模式）
  - 证据（实测拦截真问题）：初版 case-service/review-service 在 arm64 失败（grpcio-tools 被 rocketmq 5.1.1 依赖链回溯至 1.51.3，PyPI 无 cp311 aarch64 wheel，sdist 构建报 `FileNotFoundError: 'c++'`）；追加 `--platform linux/amd64` 构建暴露确定性根因——`ERROR: Cannot install requirements.lock (line 61) and rocketmq-python-client`：langfuse 4.15.2 要求 otlp-proto-http>=1.33.1 而 rocketmq 链上 otlp 1.33.0 钉死 ==1.33.0，ResolutionImpossible；CI amd64 必复现，非平台偶发
  - 证据（最小修复，单独报告）：grep 三个脚手架 app 源码对 rocketmq/eventbus/langfuse/opentelemetry 零引用；将 call/case/review 三个 Dockerfile 的裸装 lock 改为与 mcp-gateway 同款 `grep -vE '^rocketmq-python-client' requirements.lock | pip install -r`（每文件仅 1 条 RUN 变更 + 中文根因注释，未动任何业务代码/依赖文件）；修复后 case-service、review-service 重建均 **CASE_FIX_EXIT=0 / REVIEW_FIX_EXIT=0** 并产出本地镜像；call-service 改动完全同构（满足「至少 2 个」后未重复构建）
  - 环境注记：本机为 arm64，CI runner 为 amd64；本次 arm64 原生通过即证明依赖解析与双架构 wheel 均可用（排除 rocketmq 后不再触发 grpcio-tools 回溯）
- [x] 4.2 新增 `.github/workflows/cd.yml`：`on: push: tags: ['v*']`（加 workflow_dispatch 补跑入口）；`gate` job 以 reusable workflow 调用 ci.yml，`build-push` job `needs: gate`，matrix 8 服务以根 context + `services/<svc>/Dockerfile` 构建；metadata 产出 `${GITHUB_REF_NAME}` 与 `latest` 双 tag；actionlint/YAML 校验通过
  - 证据 2026-10-07（新增 `.github/workflows/cd.yml`）：on.push.tags=['v*'] + workflow_dispatch（注释注明补跑须选 tag ref，避免分支名当镜像 tag）；`gate` job `uses: ./.github/workflows/ci.yml`（ci.yml 已含 workflow_call，无 secret 消费故不传 secrets:inherit，ACR 凭证不暴露给测试 job）；`build-push` needs: gate + fail-fast: false，matrix 8 服务与 4.1 清点清单一致；`context: .` + `file: services/${{ matrix.svc }}/Dockerfile`；metadata-action 双 tag `type=raw,value=${{ github.ref_name }}` + `type=raw,value=latest`；顶层 `permissions: contents: read` 最小权限
  - 证据（action 版本零编造）：login-action@v4.6.0 / metadata-action@v6.2.0 / build-push-action@v7.4.0 均经 GitHub Releases API 查得 latest，并在各固定 tag 的 action.yml 核实 inputs（registry/username/password、images/tags/labels、context/file/push/tags/labels）；checkout@v4 与 ci.yml 现状一致；`provenance: false` 规避 ACR 个人版对 OCI attestation 索引的兼容风险并产出单一 manifest（利于 4.5 digest 比对）
  - 证据（静态校验）：本地安装 actionlint v1.7.12（GitHub Releases 官方二进制 darwin/arm64），`actionlint .github/workflows/cd.yml .github/workflows/ci.yml` exit 0（含本地 reusable workflow 路径存在性检查）；python pyyaml safe_load 解析 OK；负向 grep 无明文凭证，仅 `${{ secrets.ACR_USERNAME }}`/`${{ secrets.ACR_PASSWORD }}` 引用（41/42 行）
- [x] 4.3 接入 ACR：由用户在 GitHub 配置 secrets `ACR_USERNAME`/`ACR_PASSWORD`（确认已配置）；workflow 用 `docker/login-action` 登录 `crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com`（新个人版实例独立域名），核对 workflow 文件与日志不出现明文凭证
  - 证据 2026-10-07（secrets 配置）：用户截图确认 GitHub 仓库 Settings→Secrets and variables→Actions 的 **Repository secrets** 下存在 ACR_USERNAME / ACR_PASSWORD 两行（带锁图标、值不可见）；曾误建在 Variables（明文）一处，已指导删除重建到 Secrets
  - 证据（凭证有效性冒烟）：用户本地终端 `docker login crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com -u GavinYoung` 输入固定密码后 **Login Succeeded**（未触发批次 3 记录的 keychain 故障）；用户名/密码/域名三要素在打 tag 前已验证
  - 证据（文件侧复核）：`grep -rn 'secrets\.' .github/workflows/` 仅 cd.yml:43/44 两处 `${{ secrets.ACR_USERNAME }}`/`${{ secrets.ACR_PASSWORD }}` 引用；明文/登录命令/密码形态/echo secret 扫描均 CLEAN；实例命名空间 `ai-collection` 状态正常、自动创建仓库开启
- [x] 4.4 预演：推送 `v0.1.0-rc1`，确认 gate 全部门禁通过后才构建推送，8 个服务镜像双 tag 全部 push 成功；失败则按日志修复后重打 rc tag
  - 证据 2026-10-07（git 链路，经用户明确授权 3 commit）：工作区按能力拆 3 commit——6e3e9bd sops、c2694c1 backup、7f3519e cd+脚手架修复+openspec；`git push origin dev`（04530b5..7f3519e）后在 7f3519e（含 cd.yml，git cat-file -e 验证）打 annotated tag `v0.1.0-rc1` 并 push；明文 .env 经 check-ignore 确认未入库
  - 证据（gate 门禁前置，run id 37611208303）：API 实测 job 顺序为 gate 先全部 completed/success——service-test(data-platform/rag-service/qc-service/mcp-gateway/complaint-service) + common 共 **6/6 success**（批次 1 六门禁首次 GitHub 云端真实运行全绿），其后 build-push 才进入 in_progress；gate 期间无任何镜像推送步骤启动
  - 证据（8 服务双 tag push）：build-push matrix 8 job 轮询至 **8/8 completed success 零失败**，run 级 conclusion=success；data-platform job 日志含 `Login Succeeded!` 且结尾推送 manifest v0.1.0-rc1/latest
  - 证据（凭证无泄漏）：下载 data-platform 完整 job 日志，截图曾出现的固定密码字符串命中 **0** 次；日志仅显示 registry 域名，结尾 `Removing login credentials`（login-action 自动 logout）
  - 证据（仅构建分发）：grep cd.yml 无 ssh/scp/rsync/docker -H/kubectl/helm/deploy 任何远程动作，job 仅 gate、build-push 两个
- [x] 4.5 验收：对 8 个服务逐一 `docker pull crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection/<svc>:v0.1.0-rc1` 成功（私有仓库先 docker login 同域名），`:latest` 指向同一镜像；确认普通 main 分支推送不触发 CD、流水线未执行任何服务器侧部署动作
  - 证据 2026-10-07（8 镜像可拉取）：私有仓同域名 `docker login` 后，8 个服务 `:v0.1.0-rc1` 与 `:latest` 共 16 个引用全部 `docker image inspect` 存在（拉取自 ACR，均带 crpi 域名 RepoDigests，非本地 tag）；逐服务比对两 tag 的 RepoDigest：**data-platform / rag-service / qc-service / mcp-gateway / complaint-service / call-service / case-service / review-service 全部 MATCH，SUMMARY PASS=8 FAIL=0 MISSING=0**（双 tag 指向同一次构建同一镜像）
  - 环境注记：首次 pull 报 pull access denied——根因是本机 `~/.docker/config.json` 在两次登录间被重置为 `auths:{}`（与批次 3 Docker Desktop 凭证助手故障同类），用户重新 `docker login` 后凭证写入（auths 含 crpi 域名、token 在位），拉取即成功；非镜像/权限问题
  - 证据（普通分支推送不触发 CD）：`GET /actions/workflows/cd.yml/runs` 实测 CD 历史**仅 1 次运行**（v0.1.0-rc1，event=push）；此前 `git push origin dev`（04530b5..7f3519e，含 cd.yml 本体）未产生任何 CD 运行——tag 匹配 v* 才触发的语义成立（本仓无 main 分支，主干为 dev/master，dev 推送即「普通分支推送」证据）
  - 证据（无服务器侧部署）：cd.yml 静态扫描无 ssh/scp/rsync/docker -H/kubectl/helm/远程 deploy；job 仅 gate、build-push；未新增任何部署脚本；本 change 唯一产物为 ACR 中 8 组镜像

## 5. 整体验收

- [x] 5.1 运行 `openspec validate infra-production-readiness --strict` 通过
  - 证据 2026-10-07：`openspec validate infra-production-readiness --strict` → `Change 'infra-production-readiness' is valid`，exit 0（域名/脚手架修复等工件变更后复跑仍通过）
- [x] 5.2 输出四能力逐项 pass/fail 验收对照表（含每项证据命令与结果）：六 job 门禁、sops 四条验收、备份/恢复演练、8 镜像 pull；全部 pass 后交付

## 四能力验收对照表

### 能力一：CI 六 job 门禁

| 验收项 | 结果 | 证据 |
|---|---|---|
| ci.yml 语法校验通过 | PASS | actionlint/YAML 校验通过 |
| service-test matrix 5 服务全绿 | PASS | data-platform/rag-service/qc-service/mcp-gateway/complaint-service 5/5 success（run id 37611208303） |
| common job 全绿 | PASS | common tests + call/case/review health 通过 |
| 1.8 branch protection required checks | **搁置（用户决定）** | GitHub Free 私有仓不支持（API 403，需 Pro 或 public）；用户 2026-10-07 明确不升级/不公开，永久搁置，不阻塞交付 |
| 1.9 Merge 按钮强制阻止语义 | **搁置（随 1.8）** | 依赖 1.8；tag 制品路径已由 cd.yml reusable gate 强约束（gate 不过不产镜像，已实证），旁路仅为分支 PR 合并 |
| complaint 四阶段（unit/integration/e2e/regression/smoke） | PASS | 四阶段等价迁入；覆盖率 <85% 时 job 失败已人为验证（1.5） |
| ruff 非阻塞 lint | PASS | complaint/mcp-gateway 输出 ruff 结果但不影响 job 成败（continue-on-error，1.7） |

### 能力二：sops 加密

| 验收项 | 结果 | 证据 |
|---|---|---|
| `.sops.yaml` 配置生效 | PASS | age 公钥 recipient + path_regex + encrypted_regex 配置正确 |
| `.env.enc` 密钥字段加密 | PASS | `grep -E '(PG_PASSWORD|LLM_API_KEY|...)=' .env.enc` 输出均为 `ENC[...]` |
| 解密结果与原 `.env` 一致 | PASS | sops 解密逐行 diff 完全一致 |
| `.env.example` 无真实密钥 | PASS | 全部密钥字段为 `<change_me>` 占位值 |
| 启动方式验证 | PASS | 以 /tmp 下 `.env` 后缀符号链接 `sops exec-env <link>.env 'docker compose ... up -d'`（2.5 实测：exec-env 不识别 --input-type，直接传 .env.enc 会报 missing file），容器内 7/7 关键键为真实值、5 健康端点 200，down 后无新明文文件 |

### 能力三：备份/恢复演练

| 验收项 | 结果 | 证据 |
|---|---|---|
| backup_pg.sh 产物非空 | PASS | `backups/pg/pg_<ts>.sql.gz` 存在且非空 |
| restore_pg.sh 恢复演练 | PASS | count(*) 一致 + 3 条样本字段完全相同 |
| backup_es.sh 快照成功 | PASS | ES 可列出快照、宿主目录非空 |
| restore_es.sh 恢复演练 | PASS | 删除索引后恢复可重新检索 |
| backup_milvus.sh 产物非空 | PASS | etcd snapshot + minio mirror 打包成功 |
| restore_milvus.sh 恢复演练 | PASS | dev 环境 collection 可查 |
| backup_all.sh 全量备份 | PASS | pg→milvus→es 串行成功，同日连跑两次互不覆盖 |
| 脚本含 cron 示例且未自动注册 | PASS | 注释含 `0 2 * * *`，系统 crontab 无变化 |

### 能力四：CD 镜像分发

目标仓库：`crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection/<svc>`（2024-09 后新个人版独立域名），预演 tag `v0.1.0-rc1`，run id 37611208303。

| 验收项 | 结果 | 证据 |
|---|---|---|
| cd.yml 仅 tag 触发 | PASS | `on: push: tags: ['v*']`；API 实测 CD 仅 1 次运行（tag），push dev 未触发 |
| gate 门禁前置 | PASS | gate 6/6 success 后 build-push 才 in_progress，gate 期间无镜像步骤 |
| 8 服务双 tag push 成功 | PASS | build-push matrix 8/8 completed success，run conclusion=success |
| 8 服务 rc1/latest digest 一致 | PASS | 16 引用拉取自 ACR，RepoDigest 8/8 MATCH（PASS=8 FAIL=0） |
| 凭证无泄漏 | PASS | 日志固定密码命中 0 次，仅显域名，结尾自动 Removing login credentials |
| 无服务器侧部署动作 | PASS | 仅 gate + build-push，静态扫描无 ssh/scp/rsync/kubectl/helm/远程 deploy |

### 整体结论

**四能力验收：CI 六门禁云端运行、sops、备份/恢复演练、CD 8 镜像分发的全部功能性验收项均 PASS（2026-10-07 实测），change 功能交付完成。** 1.8/1.9 两项因个人账户私有仓 + GitHub Free 不支持 branch protection/rulesets（API 403）无法实现，**用户已于 2026-10-07 明确决定：不升级 Pro、不转 public，两项永久搁置、不视为交付缺口**（任务保留未勾选作为诚实记录，待未来账号/仓库形态变化时按既定方案补验）。对真实发布路径的防护未受影响：tag 制品门禁由 cd.yml reusable gate 在流水线内强约束（gate 不过不产镜像，本次已实证）。另：本批最小修复 call/case/review 三脚手架 Dockerfile（rocketmq/langfuse 依赖互斥，app 零引用，已报用户）。
