#!/bin/sh
# 幂等创建网关共享网络。
#
# 网关与业务服务经该网络以「容器服务名:8000」互访；docker-compose.gateway.yml、
# docker-compose.dev.yml、docker-compose.complaint.yml 中该网络声明为 external: true，
# 因此必须在任何 `docker compose up` 之前先执行本脚本，否则 compose 会因网络不存在而拒绝启动。
#
# 用法：sh deploy/gateway/net-up.sh
set -eu

NET=ai-shared-net

if docker network inspect "$NET" >/dev/null 2>&1; then
  echo "network $NET already exists"
else
  docker network create "$NET" >/dev/null
  echo "network $NET created"
fi
