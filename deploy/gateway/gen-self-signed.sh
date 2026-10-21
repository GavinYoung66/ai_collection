#!/bin/sh
# 生成网关本地自签 TLS 证书（api-gateway-hardening D7 / tasks 6.2）。
# 幂等：证书已存在则跳过。生产环境替换正式证书覆盖同路径即可。
# 用法：sh deploy/gateway/gen-self-signed.sh
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)/tls"
CRT="$DIR/tls.crt"
KEY="$DIR/tls.key"

mkdir -p "$DIR"

if [ -s "$CRT" ] && [ -s "$KEY" ]; then
  echo "exists: $CRT $KEY (skip)"
  exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$KEY" -out "$CRT" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:ai-api-gateway,IP:127.0.0.1"
chmod 600 "$KEY"

echo "generated: $CRT $KEY"
