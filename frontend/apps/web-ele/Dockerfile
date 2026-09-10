# syntax=docker/dockerfile:1.6
# QuanT 前端（@vben/web-ele）构建 + nginx 托管。
#
# ⚠️ build context 必须是 `frontend/`（monorepo 依赖根目录的 pnpm 工作区/lockfile）：
#     docker build -f apps/web-ele/Dockerfile -t quant-frontend ./frontend
#
# 仓库自带的 `frontend/scripts/deploy/Dockerfile` 是 vben 官方 playground 示例
# （它 COPY /app/playground/dist），**不能**用于本项目，故单列本文件。
FROM node:22-slim AS builder

ENV PNPM_HOME="/pnpm" \
    PATH="$PNPM_HOME:$PATH" \
    NODE_OPTIONS=--max-old-space-size=8192 \
    TZ=Asia/Shanghai \
    CI=true

# 国内网络构建建议用镜像源（默认已给 npmmirror，可用 --build-arg 覆盖）：
#   docker build --build-arg NPM_REGISTRY=https://registry.npmjs.org ...
ARG NPM_REGISTRY=https://registry.npmmirror.com

RUN npm i -g corepack && corepack enable && pnpm config set registry "$NPM_REGISTRY"

WORKDIR /app
COPY . /app

RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
RUN pnpm run build --filter=@vben/web-ele

FROM nginx:stable-alpine AS production

RUN echo "types { application/javascript js mjs; }" > /etc/nginx/conf.d/mjs.conf \
    && rm -rf /etc/nginx/conf.d/default.conf

COPY --from=builder /app/apps/web-ele/dist /usr/share/nginx/html
COPY apps/web-ele/nginx.conf /etc/nginx/conf.d/web-ele.conf

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
