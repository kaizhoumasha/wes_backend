# ============================================
# 多阶段构建 Dockerfile - P9 WES Backend
# ============================================
# 支持环境: development, testing, production
# 用法: docker build --target <stage> -t wes-backend:<env> .
# ============================================

# ============================================
# Stage 1: Base - 基础镜像
# ============================================
FROM python:3.13-slim AS base

# 设置工作目录
WORKDIR /app

# 构建参数：支持多时区部署（默认：Asia/Shanghai）
# ⚠️ 注意：应与 .env 中的 DATETIME_TIMEZONE 保持一致
# 构建时指定：docker build --build-arg CONTAINER_TIMEZONE=Asia/Shanghai ...
ARG CONTAINER_TIMEZONE=Asia/Shanghai
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security
ARG PYPI_MIRROR=https://mirrors.aliyun.com/pypi/simple

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=${PYPI_MIRROR} \
    PYTHONPATH=/app \
    UV_DEFAULT_INDEX=${PYPI_MIRROR} \
    UV_CACHE_DIR=/root/.cache/uv \
    UV_HTTP_TIMEOUT=120 \
    DEBIAN_FRONTEND=noninteractive \
    # 优化 Python 编译
    PYTHON_O=1 \
    # 设置时区（从构建参数传入）
    TZ=${CONTAINER_TIMEZONE}

# 安装系统依赖
RUN sed -i \
    -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
    -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
    /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    # 时区数据（支持 TZ 环境变量）
    tzdata \
    # 编译依赖
    gcc \
    g++ \
    libc-dev \
    libpq-dev \
    # 工具
    git \
    curl \
    # 清理缓存
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml uv.lock ./
COPY packages/wes_plugin_sdk/pyproject.toml packages/wes_plugin_sdk/pyproject.toml
COPY packages/wes_plugin_sdk/src packages/wes_plugin_sdk/src
COPY workline_plugins/rough_sorter/pyproject.toml workline_plugins/rough_sorter/pyproject.toml
COPY workline_plugins/rough_sorter/src workline_plugins/rough_sorter/src

# ============================================
# Stage 2: Builder - 依赖安装
# ============================================
FROM base AS builder

# 安装 uv；外部镜像偶发返回截断索引或挂起时，限制单次等待并有限重试。
RUN set -eu; \
    for attempt in 1 2 3; do \
        if timeout --kill-after=10s 180s pip install --no-cache-dir uv; then \
            break; \
        fi; \
        if [ "$attempt" -eq 3 ]; then \
            exit 1; \
        fi; \
        sleep $((attempt * 5)); \
    done

# 创建虚拟环境并基于锁文件安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    # CI 镜像仅安装测试与质量检查必需依赖，避免把 basedpyright/nodejs-wheel-binaries 拉进来
    uv sync --frozen --no-dev --extra dev --group ci --no-install-project --active

# ============================================
# Stage 3: Development - 开发环境
# ============================================
FROM base AS development

# 复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 激活虚拟环境
ENV PATH="/opt/venv/bin:$PATH"

# 安装开发工具
RUN pip install --no-cache-dir \
    # 代码质量
    ruff \
    # 测试工具
    pytest \
    pytest-asyncio \
    pytest-cov \
    pytest-html \
    # 性能测试
    locust

# 复制项目文件
COPY . .

# 提交元数据放在共享依赖层之后，避免每个 commit 都使 apt/uv 缓存失效。
ARG WES_VCS_REVISION
ARG WES_SOURCE_TREE
LABEL org.opencontainers.image.revision="${WES_VCS_REVISION}" \
      com.zontec.wes.source-manifest="${WES_SOURCE_TREE}"

# 暴露端口
EXPOSE 8001

# 开发环境启动命令 (支持热重载)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# ============================================
# Stage 4: Testing - 测试环境
# ============================================
FROM base AS testing

# 复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
# 保留不可变镜像依赖，并为只读 workspace 中的 uv 提供稳定项目环境路径。
RUN ln -s /opt/venv /app/.venv
# CI 验收入口统一使用 uv run --no-sync，复用镜像内已锁定的虚拟环境。
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# 激活虚拟环境
ENV PATH="/app/.venv/bin:$PATH"

# 复制项目文件
COPY . .

# CI 与部署入口脚本
RUN if [ -d /app/docker/test ]; then chmod +x /app/docker/test/*.sh; fi

# 创建测试目录
RUN mkdir -p /app/reports/coverage /app/reports/test

# 本地构建允许空标签；CI 与验收入口负责传入并严格校验真实 revision/source manifest。
ARG WES_VCS_REVISION
ARG WES_SOURCE_TREE
LABEL org.opencontainers.image.revision="${WES_VCS_REVISION}" \
      com.zontec.wes.source-manifest="${WES_SOURCE_TREE}"

# 暴露端口 (Locust Web UI)
EXPOSE 8089

# 测试环境默认命令 (运行测试)
CMD ["pytest", "-v", "--cov=src", "--cov-report=html:reports/coverage", "--cov-report=term-missing"]

# 完整 exporter fingerprints 只在隔离 stage 中校验；final 仅消费校验后的两个 raw artifacts。
FROM testing AS provider-artifact-validation

COPY reports/release-provider /tmp/wes-release-provider

ARG WES_PROVIDER_OPENAPI_SHA256
ARG WES_PROVIDED_PERMISSIONS_SHA256
ARG WES_MIGRATION_TREE_SHA256
ARG WES_BACKEND_DEPENDENCIES_SHA256
ARG WES_BACKEND_RECIPE_SHA256
ARG WES_EXPECTED_SCHEMA_HEAD
ARG WES_VCS_REVISION
ARG WES_SOURCE_TREE
RUN python -c 'import os; from pathlib import Path; from scripts.export_release_provider import validate_release_provider_artifacts; validate_release_provider_artifacts(Path("/tmp/wes-release-provider"), expected={"kind": "wes.release.backend-fingerprints.v1", "provider_openapi_sha256": os.environ["WES_PROVIDER_OPENAPI_SHA256"], "provided_permissions_sha256": os.environ["WES_PROVIDED_PERMISSIONS_SHA256"], "migration_tree_sha256": os.environ["WES_MIGRATION_TREE_SHA256"], "dependencies_sha256": os.environ["WES_BACKEND_DEPENDENCIES_SHA256"], "recipe_sha256": os.environ["WES_BACKEND_RECIPE_SHA256"], "expected_schema_head": os.environ["WES_EXPECTED_SCHEMA_HEAD"]}, revision=os.environ["WES_VCS_REVISION"], source_tree=os.environ["WES_SOURCE_TREE"])' && \
    install -d -m 0755 /validated && \
    install -m 0644 /tmp/wes-release-provider/provider-openapi.json /validated/provider-openapi.json && \
    install -m 0644 /tmp/wes-release-provider/provided-permissions.json /validated/provided-permissions.json

# Production 只从清理后的源码快照复制，避免 CI-only checker/fingerprints 出现在任何 runtime layer。
FROM base AS production-source

COPY . /app
RUN rm -rf /app/tools/release_checker && \
    rm -rf /app/reports/release-provider && \
    rm -rf /app/.agents

# ============================================
# Stage 5: Production - 生产环境
# ============================================
FROM base AS production

# 创建非 root 用户
RUN groupadd -r wesuser && useradd -r -g wesuser wesuser

# 复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 激活虚拟环境
ENV PATH="/opt/venv/bin:$PATH"

# 复制已清理的项目文件
COPY --from=production-source /app /app

# 后端 producer 唯一导出的 provider 合同；fingerprints 所在 validation stage 不进入运行镜像。
COPY --from=provider-artifact-validation /validated /opt/wes/release

ARG WES_PROVIDER_OPENAPI_SHA256
ARG WES_PROVIDED_PERMISSIONS_SHA256
ARG WES_MIGRATION_TREE_SHA256
ARG WES_BACKEND_DEPENDENCIES_SHA256
ARG WES_BACKEND_RECIPE_SHA256
ARG WES_EXPECTED_SCHEMA_HEAD
ARG WES_VCS_REVISION
ARG WES_SOURCE_TREE

# 镜像内入口脚本
RUN if [ -d /app/docker/test ]; then chmod +x /app/docker/test/*.sh; fi

# 创建日志目录
RUN mkdir -p /app/logs && \
    chown -R wesuser:wesuser /app

# 提交元数据放在最终运行层，保持共享依赖层可跨 commit 复用。
LABEL org.opencontainers.image.revision="${WES_VCS_REVISION}" \
      com.zontec.wes.source-manifest="${WES_SOURCE_TREE}" \
      org.wes.release.provider-openapi.sha256="${WES_PROVIDER_OPENAPI_SHA256}" \
      org.wes.release.provided-permissions.sha256="${WES_PROVIDED_PERMISSIONS_SHA256}" \
      org.wes.release.migration-tree.sha256="${WES_MIGRATION_TREE_SHA256}" \
      org.wes.release.backend-dependencies.sha256="${WES_BACKEND_DEPENDENCIES_SHA256}" \
      org.wes.release.backend-recipe.sha256="${WES_BACKEND_RECIPE_SHA256}" \
      org.wes.release.expected-schema-head="${WES_EXPECTED_SCHEMA_HEAD}"

# 切换到非 root 用户
USER wesuser

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# 生产环境启动命令 (多 worker)
# 数据库容量公式输入：1 x 4 x 5（API 容器数 x Uvicorn 进程数 x 单进程 pool_size）。
# 修改 --workers 时必须同步更新容量门禁与部署计划。
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "4"]
