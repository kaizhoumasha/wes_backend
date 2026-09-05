# ============================================
# Mock 服务 Dockerfile
# ============================================
# 用途: 为 E2E 测试提供设备 Mock 服务
# 构建: docker build -t wes-mock:latest -f tests/mock/Dockerfile .
# ============================================

FROM python:3.13-slim

LABEL maintainer="WES Backend Team"
LABEL description="Mock 外部系统服务（ECS、WMS）用于 E2E 测试"

ARG DEBIAN_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PIP_INDEX_URL=${PYPI_MIRROR}
# 固定运行目录为 /app/tests/mock；显式暴露镜像内打包的 src/tests 命名空间。
ENV PYTHONPATH=/app

# 安装系统依赖
RUN sed -i \
    -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
    -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
    /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 保留既有系统依赖层缓存，仅对发生故障的 Python 包源应用覆盖。
ARG PYPI_INSTALL_MIRROR=https://mirrors.aliyun.com/pypi/simple
ENV PIP_INDEX_URL=${PYPI_INSTALL_MIRROR}

# 安装 Python 依赖（仅 mock 服务需要的）
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    httpx \
    pydantic \
    watchfiles  # 用于更快的文件变更检测（热更新）

# 复制 mock 服务代码
WORKDIR /app
COPY tests/mock/ /app/tests/mock/
COPY src/wes_plugin_sdk/src/wes_plugin_sdk/ /app/wes_plugin_sdk/
COPY src/static/swagger-ui/ /app/src/static/swagger-ui/
# WMS Transport Mock 复用基础线协议能力，不依赖旧业务 operation registry。
COPY src/app/wms_adapter/strict_json.py /app/src/app/wms_adapter/strict_json.py
COPY src/app/wms_adapter/transport_openapi.py /app/src/app/wms_adapter/transport_openapi.py
COPY src/app/wms_adapter/transport_wire.py /app/src/app/wms_adapter/transport_wire.py
COPY src/app/transport/callback_json.py /app/src/app/transport/callback_json.py
COPY src/app/transport/contracts.py /app/src/app/transport/contracts.py
COPY src/core/uuid7.py /app/src/core/uuid7.py
# ECS Mock 仅复用 callback 域无外部依赖的事件分类常量与判定函数。
COPY src/app/callback/contracts/runtime_events.py /app/src/app/callback/contracts/runtime_events.py

# 创建非 root 用户
RUN useradd -m -u 1000 mockuser && \
    chown -R mockuser:mockuser /app

USER mockuser

# 健康检查（需要在容器运行时动态配置端口）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# 默认工作目录
WORKDIR /app/tests/mock

# 默认命令（可被 docker-compose 覆盖）
CMD ["python", "-m", "uvicorn", "ecs_mock_server:app", "--host", "0.0.0.0", "--port", "8010"]
