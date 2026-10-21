# platform/secrets-sops Specification

## Purpose
用无服务端依赖的 sops + age 对仓库中的密钥进行加密管理：密文可安全入库、明文永不入库、启动时解密注入，使密钥的存储、流转与人员持有边界可审计。

## Requirements

### Requirement: sops 加密规则配置

仓库根 SHALL 提供 `.sops.yaml`，声明 age 公钥（recipient）与加密规则：所有键名**包含** `KEY`、`SECRET`、`PASSWORD`、`TOKEN` 子串的字段 MUST 被加密（按子串而非严格后缀匹配），其余非密钥配置保持明文可读，空值字段除外（无秘密可保护）。规则 MUST 覆盖根 `.env` 内现有全部敏感字段（含但不限于 PG 密码、LLM/DeepSeek/Anthropic/DashScope 密钥、AES/JWT 密钥、ASR/TTS 凭证、`RAG_API_TOKEN`，以及以 `_ID`/`_SECRET` 结尾的 CCC 凭证）；现有非密钥字段（如 `ENABLE_AUTH`、`MCP_GATEWAY_ENABLE_AUTH` 等布尔/开关项）MUST NOT 被误伤加密。

#### Scenario: 含敏感子串的字段被加密

- **WHEN** 用 `.sops.yaml` 对环境文件执行加密
- **THEN** 键名包含 `KEY`/`SECRET`/`PASSWORD`/`TOKEN` 子串的值全部变为 sops 密文（`ENC[...]`），非敏感字段仍为明文

#### Scenario: CCC_ACCESS_KEY_ID 因子串规则被加密

- **WHEN** 对含 `CCC_ACCESS_KEY_ID`（以 `_ID` 结尾、不满足任何严格后缀模式）的环境文件执行加密
- **THEN** `CCC_ACCESS_KEY_ID` 与 `CCC_ACCESS_KEY_SECRET` 的值均为 `ENC[...]` 密文，不因命名以 `_ID` 结尾而漏加密

#### Scenario: 配置可被 sops 正常识别

- **WHEN** 在仓库根执行 sops 加解密操作
- **THEN** sops 自动读取 `.sops.yaml` 的规则与公钥，无需手工额外指定加密参数

### Requirement: age 密钥对持有边界

系统 SHALL 生成一对 age 密钥：公钥写入仓库内 `.sops.yaml`，可公开；对应私钥 MUST NOT 出现在仓库、CI 日志或任何共享位置，SHALL 由至少 2 名核心成员各自离线持有（如本地密码管理器），任一名成员可用其私钥独立完成解密。

#### Scenario: 私钥不在仓库中

- **WHEN** 在仓库历史与工作区中检索 age 私钥
- **THEN** 找不到任何 age 私钥内容，仅存在公钥与加密规则

#### Scenario: 至少两人可独立解密

- **WHEN** 任一持私钥的核心成员在本地使用自己的私钥
- **THEN** 可独立解密 `.env.enc`，不依赖单一持有人在线

### Requirement: 密文入库与明文边界

根环境文件 SHALL 加密为 `.env.enc` 并作为唯一入库的密钥载体，且 MUST 能被 sops 正常解密回原始内容。明文 `.env` 及历史明文备份文件（如 `.env.bak`）MUST NOT 被 git 跟踪（`git check-ignore` 对明文 `.env` 返回忽略）。`.env.example` SHALL 保留全部字段名与注释，但所有密钥字段值 MUST 为 `<change_me>` 占位，MUST NOT 含任何真实密钥。`services/complaint-service/config/{dev,test,pre,prod}.env` 维持现状，不在本能力改动范围。

#### Scenario: 明文环境文件被忽略

- **WHEN** 执行 `git check-ignore .env`
- **THEN** 命令返回该文件被忽略，明文密钥文件不进入提交

#### Scenario: 密文文件中的密钥不可直接读取

- **WHEN** 在 `.env.enc` 中检索 PG 密码、LLM 密钥、DeepSeek 密钥等字段的赋值
- **THEN** 检索到的值均为 `ENC[...]` 形式密文，不存在明文真实值

#### Scenario: 示例文件不含真实密钥

- **WHEN** 检查 `.env.example`
- **THEN** 仅含字段名、注释与 `<change_me>` 占位值，不含任何真实密钥

#### Scenario: 密文可完整解密

- **WHEN** 持私钥成员对 `.env.enc` 执行 sops 解密
- **THEN** 得到加密前的完整环境变量内容，字段与值无丢失

### Requirement: 解密注入的启动方式

依赖根环境变量的 compose 启动 SHALL 经 `sops exec-env` 解密注入后再拉起（`SOPS_AGE_KEY_FILE` 指向成员本地私钥）。sops 3.13.3 的 exec-env 不接受 `--input-type`（全局 flag 在子命令上下文不生效），dotenv 类型仅按**文件扩展名**推断，命令须为单个字符串——实际形态是以 `.env` 后缀符号链接指向 `.env.enc` 后包装启动命令（链接建在仓库外）。解密后的真实密钥值 MUST NOT 因启动流程落盘为新的明文文件。

当前编排现状（2026-10-06 验证）：`docker-compose(.dev).yml` 各应用服务硬编码 `env_file: .env`，容器内密钥由 compose 启动时从磁盘明文 `.env` 读取，exec-env 注入仅覆盖 compose 级 `${VAR}` 插值；持有者 SHALL 暂保留本地 `.env`（gitignore 防回流），完整注入链改造列为后续独立 change。

#### Scenario: 容器内获得真实密钥

- **WHEN** 经 `sops exec-env <link>.env 'docker compose … up -d'`（`.env` 后缀符号链接指向 `.env.enc`）包装启动后进入容器查看环境变量
- **THEN** 容器进程环境中存在真实密钥值，应用可正常连接 PG/Redis/LLM 等依赖

#### Scenario: 启动过程不留新明文

- **WHEN** 完成上述启动后检查工作区与备份目录
- **THEN** 不存在由启动命令新生成的明文密钥文件，仓库中密钥唯一形态仍是 `.env.enc`（既有本地 `.env` 为启动输入、被 gitignore 排除，非启动过程新产出）
