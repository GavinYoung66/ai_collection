## Context

当前网关为 Nginx 反向代理（deploy/gateway/nginx.conf + docker-compose.gateway.yml），监听 8080，已具备路由转发、限流(20r/s burst40)、CORS(*)、trace 透传、统一错误信封(B0006/B0008/B0009)、Gradio WebSocket、静态托管。网关 compose 独立于业务 compose，未接入业务 docker network，因此 upstream 被迫使用 `host.docker.internal:800x` 绕宿主机端口映射。各服务容器内部端口统一为 8000，通过 `8006:8000` 等映射到宿主机。common/security 已有 rbac.py，可复用角色模型。

## Goals / Non-Goals

**Goals:**
- 使网关达到可生产部署门槛：共享网络、配置模板化、鉴权（默认关闭）、CORS 收紧、TLS/安全头、被动熔断、超时分级、指标导出、灰度开关。
- 鉴权验证逻辑全平台单一实现（common/security/authn.py），网关 sidecar 与 Python 服务均可复用。
- 本地开发体验零回归：ENABLE_AUTH=false（默认）时行为与现状完全一致。

**Non-Goals:**
- 不做配置中心（Apollo/Nacos）——独立后续 change。
- 不做服务发现——docker DNS 已满足当前规模。
- 不做各 upstream 分状态码统计——需 Lua/商业版，列为后续。
- 不改服务间东西向调用方式——内部调用韧性仍由各调用方负责。

## Decisions

### D1: 共享网络 + 容器服务名 upstream，废弃 host.docker.internal

**选择**：定义 external docker network（如 `ai-shared-net`），网关与 complaint-service/qc-service/rag-service 均接入；upstream 改为 `complaint-service:8000` 等容器服务名寻址。

**理由**：host.docker.internal 依赖宿主机端口映射，跨环境（纯容器/k8s）不可用，且多绕一跳。共享网络后网关直连容器，端口映射仅服务于本地脚本/外部直连调试，与网关路由解耦。

**备选**：①保持 host.docker.internal——被否，跨环境不可用；②用 Docker links（`links:`）——已废弃，不推荐。

### D2: nginx 模板化(envsubst) + .env 单一事实源

**选择**：nginx.conf 改为 nginx.conf.template，容器启动时 envsubst 从 .env 渲染。上游地址、CORS 白名单、ENABLE_AUTH、TLS_ENABLED、CANARY_WEIGHT、LOG_JSON 等全部从 .env 注入。渲染 MUST 使用显式变量白名单 `envsubst '${VAR1} ${VAR2} …'`：envsubst 默认替换所有 `$VAR`，会把 nginx 内置变量（`$host`/`$remote_addr`/`$status`/`$request_id` 等）替换为空串导致配置失效。条件块（TLS 8443 server 块、auth_request 指令）MUST NOT 靠 envsubst 生成——envsubst 无条件能力，由入口脚本按开关拼接 include 文件实现。

**理由**：消除 nginx.conf 与各服务 config.py 的双份硬编码，.env 成为唯一事实源，改一处即生效。

**备选**：①配置中心动态渲染——超范围，属后续 change；②手写多份 nginx.conf 环境变体——易漂移。

### D3: auth_request + 极薄 Python sidecar + common 下沉

**选择**：Nginx `auth_request` 指令向内部 sidecar（deploy/auth，FastAPI <150 行）发起子请求；sidecar import common/security/authn.py 执行验证，成功返 204 + X-User-Id/X-User-Role，失败返 401。

**理由**：验证逻辑全平台单一实现（common），网关 sidecar 与 Python 服务均可复用，避免多份鉴权代码漂移。Nginx 原生 auth_request 模块（官方镜像自带）无需换镜像。

**备选**：①纯 Nginx map 校验 API Key + OpenResty Lua 验 JWT——会导致鉴权逻辑出现 nginx 一份、Python 一份，违背单一事实源；②各服务自实现鉴权——重复代码，漂移风险高。

### D4: ENABLE_AUTH 默认关闭 + 豁免清单

**选择**：ENABLE_AUTH 环境变量控制是否插入 auth_request 指令；false（默认）时不插入，本地行为零回归；true 时启用。/gateway/health 与 /health、OPTIONS 预检永远豁免，由 Nginx location/方法判定执行，不依赖 sidecar。

**理由**：本地开发不需要鉴权，强制开启会破坏现有演示流程；豁免清单确保即使 sidecar 不可用，健康检查与预检仍正常。

**备选**：①默认开启——破坏本地体验；②豁免交 sidecar 判断——sidecar 挂了健康检查也挂，不可接受。

### D5: JWT/API Key 双方案并存 + 恒定时间比对

**选择**：authn.py 同时支持 JWT（验签/过期/iss/aud）与 API Key（恒定时间比对）两种凭证；请求头 Authorization: Bearer <token> 统一入口，sidecar 先尝试 JWT 解析，失败再尝试 API Key 比对。JWT 验签复用并扩展 rbac.py 现有 `verify_token`（补 iss/aud 与拒绝原因），role 直接取自 payload 的 `role` 字段（与 require_role.py 语义一致），不另起一套 JWT 解码、不引入第二套角色模型。

**理由**：JWT 适合前端用户会话，API Key 适合服务间/脚本调用；恒定时间比对防时序攻击。双方案覆盖不同场景。

**备选**：①仅 JWT——服务间脚本不便携带 JWT；②仅 API Key——前端会话管理弱。

### D6: CORS 从 * 收紧为白名单

**选择**：Access-Control-Allow-Origin 从 * 改为 .env 的 CORS_ALLOWED_ORIGINS 白名单，按请求 Origin 匹配后回显；默认含 localhost:8080/127.0.0.1:8080。白名单逗号字符串由入口脚本展开为 nginx map 块（envsubst 无法把字符串生成 map），模板 include 该块，add_header 引用 map 结果。

**理由**：* 无法与凭证/cookie 模式安全共存，生产环境必须收紧。白名单方式兼顾安全与多开发源。

**备选**：①保持 *——生产硬伤；②反射任意 Origin——等同 *，无意义。

### D7: TLS 开关化 + 安全响应头

**选择**：TLS_ENABLED=true 时监听 8443 + 证书挂载 + 8080 跳 8443 + HSTS；false（默认）维持 8080 明文。安全响应头（X-Frame-Options/X-Content-Type-Options/Referrer-Policy）始终添加。

**理由**：本地开发无需 TLS（自签证书浏览器告警影响体验）；生产开启即可。安全头零成本始终添加。

**备选**：①强制 TLS——破坏本地体验；②无安全头——安全基线缺失。

### D8: 被动熔断 max_fails/fail_timeout + 非幂等不重试

**选择**：upstream 加 max_fails=3 fail_timeout=10s；proxy_next_upstream 仅对幂等方法生效，POST/PUT/DELETE/PATCH 不自动重试。

**理由**：连续失败时快速失败避免逐次等待超时；非幂等重试会导致重复创建工单等副作用。

**备选**：①主动熔断（需 Lua/外部组件）——超范围；②无熔断——慢故障拖垮网关连接池。

### D9: 超时三级分级（3s/15s/120s）

**选择**：health 3s、普通读写 15s、judge/流式 120s，按 location 区分。

**理由**：现状统一 120s 导致短请求长时间占用连接；分级后普通接口快速失败释放连接，长任务不受影响。

**备选**：①统一 15s——judge 类长任务被误杀；②统一 120s——现状，资源浪费。

### D10: metrics 边界（stub_status 能力上限）

**选择**：开 stub_status + nginx prometheus exporter，/metrics 仅内网/鉴权可达。指标限定为活跃连接数、总请求数等 stub_status 原生项。

**理由**：stub_status 是官方 nginx 免费模块能提供的全部；各 upstream 分状态码需 Lua/商业版，不在本次承诺。明确边界避免交付时预期落差。

**备选**：①引入 Lua 模块——增加镜像复杂度，超范围；②无指标——可观测性缺口。

### D11: 灰度只留开关，当前不启用

**选择**：split_clients + CANARY_WEIGHT + v1/v2 双 upstream，weight=0（默认）全走 v1；v2 指向与 v1 相同后端（占位）。

**理由**：当前单版本无灰度需求，但备好开关使将来升级时可零配置启用。不部署 v2 避免资源浪费。

**备选**：①不做灰度——将来升级需改 nginx.conf；②部署 v2——无意义，资源浪费。

### D12: data-platform 不暴露

**选择**：data-platform 默认不对外暴露路由，保持纯内部服务。

**理由**：dp 是数据中台内部服务，外部前端不应直接访问其接口。若未来运维需要只读看快照，再以"只读 + 强制鉴权"暴露子集。

**备选**：①暴露 dp 全部路由——安全面过大；②暴露只读 snapshot——当前无需求，留待未来。

### D13: 配置中心为独立后续 change

**选择**：本次仅以 .env + nginx 模板消除地址漂移；配置中心（Apollo/Nacos）明确列为独立后续 change。

**理由**：配置中心需独立部署 server+DB、客户端长轮询与本地缓存兜底、配置分层/热更新模型、提示词 8 步发布对接、权限审计，是独立基础设施项。塞入本次会模糊 change 范围，违背"一个 change 解决一类问题"原则。

**备选**：①本次一并做配置中心——范围过大，验收不可信；②永不做——动态配置/提示词治理需求无法满足。

## Risks / Trade-offs

- **[sidecar 增加运维复杂度]** → sidecar 极薄（<150 行），常驻开销极小；ENABLE_AUTH=false 时完全不调用，不影响本地。
- ** [envsubst 不支持运行时热更新]** → 改配置需重启网关容器（秒级），可接受；热更新属配置中心范畴。
- **[stub_status 指标维度有限]** → 明确边界，分状态码统计列为后续，不承诺。
- **[灰度 split_clients 需 v2 upstream 占位]** → v2 指向与 v1 相同后端，零额外资源；启用时只需改 upstream 指向。
- **[TLS 自签证书浏览器告警]** → 仅 TLS_ENABLED=true 时；本地默认关闭不受影响，生产替换正式证书。

## Migration Plan

1. 先建共享网络 + 改 upstream 为容器服务名 → 验证网关调通三服务（不改任何业务代码）。
2. nginx 模板化 → 验证 .env 渲染生效。
3. 加鉴权 sidecar + auth_request + ENABLE_AUTH=false → 验证零回归 + 鉴权单测全绿。
4. 逐项加 CORS 白名单 / TLS / 熔断 / 超时分级 / metrics / 灰度开关 → 每项单独验证。
5. 全部完成后 ENABLE_AUTH=false 落地，等待人工审查后再决定是否生产开启。

**回滚策略**：每步独立，任一步骤出问题可单独回退该步 nginx 配置；最差情况回退到 change 前的 nginx.conf（host.docker.internal 版本），业务不受影响。

## Open Questions

无。所有决策已在 D1-D13 中明确，无待决项影响 specs 或 task 分解。
