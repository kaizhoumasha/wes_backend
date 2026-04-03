# syntax=docker/dockerfile:1.7

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
ARG DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

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
    curl \
    # 清理缓存
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# ============================================
# Stage 2: Builder - 依赖安装
# ============================================
FROM base AS builder

# 安装 uv (更快的 Python 包管理器)
RUN pip install --no-cache-dir uv

# 创建虚拟环境并基于锁文件安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv sync --frozen --all-extras --all-groups --no-install-project --active

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

# 激活虚拟环境
ENV PATH="/opt/venv/bin:$PATH"

# 复制项目文件
COPY . .

# CI 与部署入口脚本
RUN if [ -d /app/docker/test ]; then chmod +x /app/docker/test/*.sh; fi

# 创建测试目录
RUN mkdir -p /app/reports/coverage /app/reports/test

# 暴露端口 (Locust Web UI)
EXPOSE 8089

# 测试环境默认命令 (运行测试)
CMD ["pytest", "-v", "--cov=src", "--cov-report=html:reports/coverage", "--cov-report=term-missing"]

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

# 复制项目文件
COPY . .

# 镜像内入口脚本
RUN if [ -d /app/docker/test ]; then chmod +x /app/docker/test/*.sh; fi

# 创建日志目录
RUN mkdir -p /app/logs && \
    chown -R wesuser:wesuser /app

# 切换到非 root 用户
USER wesuser

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/api/v1/performance/health || exit 1

# 生产环境启动命令 (多 worker)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--log-config", "null"]
