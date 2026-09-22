#!/bin/sh
# Docker Desktop extra_hosts 会同时写入 host.docker.internal 的 A/AAAA。
# nginx 解析 upstream 时优先试 IPv6，本机到 host-gateway 的 IPv6 不通，
# 会先打 error_log 再回退 IPv4。启动前去掉 AAAA，只留 IPv4。
set -eu
HOSTS=/etc/hosts
if [ -w "$HOSTS" ]; then
  ipv4=$(awk '$NF == "host.docker.internal" && $1 !~ /:/ { print $1; exit }' "$HOSTS" || true)
  if [ -n "${ipv4:-}" ]; then
    grep -v '[[:space:]]host.docker.internal$' "$HOSTS" > /tmp/hosts
    printf '%s\thost.docker.internal\n' "$ipv4" >> /tmp/hosts
    cat /tmp/hosts > "$HOSTS"
  fi
fi
exec nginx -g 'daemon off;'
