# syntax=docker/dockerfile:1

# ---- 构建阶段：安装依赖并产出静态产物（vite base 固定 /admin/）----
FROM node:20-alpine AS build
WORKDIR /app

# 先拷依赖清单，源码变动时最大化命中 npm ci 层缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- 运行阶段：nginx 托管静态产物 ----
# 仅接 ai-shared-net 内网，不 publish 端口；经网关 /admin/ 统一入口对外
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/admin
EXPOSE 80
