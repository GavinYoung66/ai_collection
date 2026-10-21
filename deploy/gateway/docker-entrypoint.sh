#!/bin/sh
# api-gateway 容器入口：从 /etc/nginx/nginx.conf.template 渲染最终配置。
#
# 约束（openspec change api-gateway-hardening：tasks 2.2 / D2 / spec
# 「共享网络与配置模板化部署」）：
# - envsubst MUST 使用显式变量白名单：默认行为会替换全部 $VAR，
#   nginx 内置变量（$host/$remote_addr/$status/$request_id/$http_x_trace_id 等）
#   不是容器环境变量，会被替换为空串，导致 trace 透传、限流键、日志格式失效。
# - 条件块（auth_request / CORS map / TLS 8443 / 日志格式 / split_clients）
#   由本脚本按开关拼接 include 文件，MUST NOT 靠 envsubst 生成
#  （envsubst 无分支能力，也无法把逗号字符串展开成 map 块）；
#   开关关闭时对应 include 为空文件（include 空文件 = no-op）。
set -eu

# 1) 变量兜底策略（交叉审核 P2-1 修复）：UPSTREAM_* 是 upstream 地址的唯一
#    事实源（.env）载体，未注入/为空时立即失败——不回退第二份硬编码地址，
#    也不渲染空串（spec：全仓 MUST NOT 存在第二份硬编码 upstream 地址）。
#    开关类变量仅语义默认值（false/0 等，非地址）。
: "${UPSTREAM_COMPLAINT:?missing UPSTREAM_COMPLAINT (set it in .env)}"
: "${UPSTREAM_QC:?missing UPSTREAM_QC (set it in .env)}"
: "${UPSTREAM_RAG:?missing UPSTREAM_RAG (set it in .env)}"
: "${UPSTREAM_WEB:?missing UPSTREAM_WEB (set it in .env)}"
: "${CORS_ALLOWED_ORIGINS:=http://localhost:8080,http://127.0.0.1:8080}"
: "${ENABLE_AUTH:=false}"
: "${TLS_ENABLED:=false}"
: "${CANARY_WEIGHT:=0}"
: "${LOG_JSON:=false}"
export UPSTREAM_COMPLAINT UPSTREAM_QC UPSTREAM_RAG UPSTREAM_WEB
export CORS_ALLOWED_ORIGINS ENABLE_AUTH TLS_ENABLED CANARY_WEIGHT LOG_JSON

# 1.5) CANARY_WEIGHT 合法性（D11 / tasks 10.1）：0-100 整数，非法即 fail fast，
#      避免生成语法错误的分流配置。
case "$CANARY_WEIGHT" in
    ''|*[!0-9]*) echo "docker-entrypoint: CANARY_WEIGHT must be an integer in 0..100, got '$CANARY_WEIGHT'" >&2; exit 1 ;;
esac
if [ "$CANARY_WEIGHT" -gt 100 ]; then
    echo "docker-entrypoint: CANARY_WEIGHT must be <= 100, got $CANARY_WEIGHT" >&2
    exit 1
fi

TEMPLATE=/etc/nginx/nginx.conf.template
OUTPUT=/etc/nginx/nginx.conf
COMMON_TEMPLATE=/etc/nginx/server_common.conf.template
COMMON_OUTPUT=/etc/nginx/server_common.conf

# 2) 白名单渲染：仅下列 ${VAR} 被替换，nginx 内置 $变量一律原样保留。
#    server_common.conf 同白名单渲染（8080 与 8443 共用，唯一事实源）。
VARS='${UPSTREAM_COMPLAINT} ${UPSTREAM_QC} ${UPSTREAM_RAG} ${UPSTREAM_WEB} ${CORS_ALLOWED_ORIGINS} ${ENABLE_AUTH} ${TLS_ENABLED} ${CANARY_WEIGHT} ${LOG_JSON}'
envsubst "$VARS" < "$TEMPLATE" > "$OUTPUT"
envsubst "$VARS" < "$COMMON_TEMPLATE" > "$COMMON_OUTPUT"

# 2.5) 条件鉴权 include（api-gateway-hardening D2/D4 / tasks 4.3）：
#      envsubst 无分支能力，由本脚本按 ENABLE_AUTH 拼接 include 文件。
#      - true：写入 auth_request + 身份透传 + 401 错误信封；
#      - false：写空文件，各 location 的 include 为空操作，
#        零回归（无 auth 子请求、无 401 改写、后端行为与 C 批一致）。
#      error_page 401 MUST 仅在此 include 内（ENABLE_AUTH=true 才生效），
#      不得放 server 级全局——否则 false 时后端自有 401 会被误改写为 B0002。
AUTH_CONF=/etc/nginx/auth_request.conf
if [ "$ENABLE_AUTH" = "true" ]; then
  cat > "$AUTH_CONF" <<'EOF'
auth_request /internal/authz;
auth_request_set $user_id $upstream_http_x_user_id;
auth_request_set $user_role $upstream_http_x_user_role;
proxy_set_header X-User-Id $user_id;
proxy_set_header X-User-Role $user_role;
error_page 401 = @auth_required;
EOF
else
  : > "$AUTH_CONF"
fi

# 2.6) CORS 白名单 map（D6 / tasks 5.1）：把逗号分隔的 CORS_ALLOWED_ORIGINS
#      展开为 map 块（envsubst 无法把字符串展开成 map）。每条 origin 的
#      正则元字符（. - 等）转义后用 ~^...$ 精确匹配，命中回显该 Origin。
#      白名单为空/缺失时仅 `default "";`——任何 Origin 都不回显。
CORS_CONF=/etc/nginx/cors.conf
{
  echo 'map $http_origin $cors_origin {'
  echo '    default "";'
  origins=$(printf '%s' "$CORS_ALLOWED_ORIGINS" | tr -d '[:space:]')
  OLD_IFS=$IFS; IFS=','
  for o in $origins; do
    [ -z "$o" ] && continue
    esc=$(printf '%s' "$o" | sed 's/[.-]/\\&/g')
    printf '    "~^%s$" $http_origin;\n' "$esc"
  done
  IFS=$OLD_IFS
  echo '}'
} > "$CORS_CONF"

# 2.7) TLS 开关（D7 / tasks 6.2）：
#      - tls.conf：8443 ssl server 块（true 才有内容），HSTS 仅 HTTPS 侧；
#        路由复用 /etc/nginx/server_common.conf（与 8080 同一份，不漂移）。
#      - tls_redirect.conf：8080 server 块首行 include，true 时 301 跳 8443；
#        MUST 豁免 /gateway/health——compose healthcheck 走明文 wget，
#        被 301 到自签证书端口会失败。
#      证书由宿主机 deploy/gateway/gen-self-signed.sh 预生成（alpine 镜像无
#      openssl），TLS_ENABLED=true 而证书缺失时 fail fast。
TLS_CONF=/etc/nginx/tls.conf
TLS_REDIRECT_CONF=/etc/nginx/tls_redirect.conf
if [ "$TLS_ENABLED" = "true" ]; then
  CRT=/etc/nginx/tls/tls.crt
  KEY=/etc/nginx/tls/tls.key
  if [ ! -s "$CRT" ] || [ ! -s "$KEY" ]; then
    echo "docker-entrypoint: TLS_ENABLED=true but $CRT/$KEY missing; run 'sh deploy/gateway/gen-self-signed.sh' on host first" >&2
    exit 1
  fi
  cat > "$TLS_CONF" <<'EOF'
server {
    listen 8443 ssl;
    listen [::]:8443 ssl;
    server_name _;
    ssl_certificate     /etc/nginx/tls/tls.crt;
    ssl_certificate_key /etc/nginx/tls/tls.key;
    add_header Strict-Transport-Security "max-age=31536000" always;
    include /etc/nginx/server_common.conf;
}
EOF
  cat > "$TLS_REDIRECT_CONF" <<'EOF'
if ($uri != /gateway/health) {
    return 301 https://$host:8443$request_uri;
}
EOF
else
  : > "$TLS_CONF"
  : > "$TLS_REDIRECT_CONF"
fi

# 2.8) 访问日志格式开关（tasks 9.1）：LOG_JSON=false 用现有 gateway 文本格式，
#      true 切 gateway_json（单行 JSON，含 trace_id/request_time/
#      upstream_response_time/status）。
LOG_CONF=/etc/nginx/log.conf
if [ "$LOG_JSON" = "true" ]; then
  echo 'access_log /var/log/nginx/access.log gateway_json;' > "$LOG_CONF"
else
  echo 'access_log /var/log/nginx/access.log gateway;' > "$LOG_CONF"
fi

# 2.9) 灰度分流（D11 / tasks 10.1）：split_clients 的百分比 MUST 是脚本 bake
#      的数字（nginx 变量不能用于 split_clients 百分比）。分流键用稳定会话
#      标识 $cookie_canary；CANARY_WEIGHT=0 时仅 `* v1` 一行（v2 无流量），
#      >0 时按权重分 v1/v2。
CANARY_CONF=/etc/nginx/canary.conf
gen_split() {
  # $1=目标变量名  $2=v1 upstream 组  $3=v2 upstream 组
  if [ "$CANARY_WEIGHT" -gt 0 ]; then
    printf 'split_clients "${cookie_canary}" $%s {\n    %s%% %s;\n    * %s;\n}\n' \
      "$1" "$CANARY_WEIGHT" "$3" "$2"
  else
    printf 'split_clients "${cookie_canary}" $%s {\n    * %s;\n}\n' "$1" "$2"
  fi
}
{
  gen_split complaint_upstream complaint complaint_v2
  gen_split qc_upstream qc qc_v2
  gen_split kb_upstream kb kb_v2
  gen_split web_upstream web web_v2
} > "$CANARY_CONF"

# 2.10) 灰度 proxy_pass 形态（D11 / tasks 10.1 优化）：CANARY_WEIGHT=0 时用定名
#       upstream（http://complaint），保留 upstream keepalive 连接缓存；>0 时用
#       变量形态（http://$complaint_upstream）以支持 split_clients 分流，代价是
#       keepalive 失效（每请求新建连接，属灰度启用态可接受代价）。
#       模板各 location 以 include 引用，避免在模板里写死变量形态。
PROXY_PASS_DIR=/etc/nginx/proxy_pass
mkdir -p "$PROXY_PASS_DIR"
if [ "$CANARY_WEIGHT" -gt 0 ]; then
  printf 'proxy_pass http://$complaint_upstream;\n' > "$PROXY_PASS_DIR/complaint.conf"
  printf 'proxy_pass http://$qc_upstream;\n'       > "$PROXY_PASS_DIR/qc.conf"
  printf 'proxy_pass http://$kb_upstream;\n'       > "$PROXY_PASS_DIR/kb.conf"
else
  printf 'proxy_pass http://complaint;\n' > "$PROXY_PASS_DIR/complaint.conf"
  printf 'proxy_pass http://qc;\n'       > "$PROXY_PASS_DIR/qc.conf"
  printf 'proxy_pass http://kb;\n'       > "$PROXY_PASS_DIR/kb.conf"
fi

# 3) fail fast：nginx -t 失败则打印渲染结果并 exit 1，禁止静默启动坏配置。
if ! nginx -t; then
    echo "docker-entrypoint: nginx -t failed; rendered $OUTPUT was:" >&2
    cat "$OUTPUT" >&2
    exit 1
fi

# 4) exec 接管 PID 1：否则 sh 虽存活，容器信号与存活判定不会落在 nginx 上。
exec nginx -g 'daemon off;'
