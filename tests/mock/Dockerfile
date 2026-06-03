# ============================================
# Mock 服务 Dockerfile
# ============================================
# 用途: 为 E2E 测试提供设备 Mock 服务
# 构建: docker build -t wes-mock:latest -f tests/mock/Dockerfile .
# ============================================

FROM python:3.13-slim

LABEL maintainer="WES Backend Team"
LABEL description="Mock 外部系统服务（ECS、WMS）用于 E2E 测试"

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

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
COPY src/workline_runtime/sandbox_catalog.py /app/src/workline_runtime/sandbox_catalog.py
COPY src/workline_runtime/runtime_events.py /app/src/workline_runtime/runtime_events.py

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
