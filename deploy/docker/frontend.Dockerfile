# T9.5（PHASE-009）前端镜像：node 构建 → nginx 托管静态资源 + 反代 gateway/notify
# 构建：docker build -f deploy/docker/frontend.Dockerfile .（上下文 = 仓库根）
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx/nginx.container.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
