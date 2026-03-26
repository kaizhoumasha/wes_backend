"""
SMT 粗分机 E2E 测试包

包含 SMT 粗分机插件的端到端测试，测试插件与 Mock 设备的完整交互流程。

运行方式:
    # 运行所有 E2E 测试
    uv run pytest tests/e2e/smt_classifier/ -v

    # 仅运行 E2E 测试（跳过其他测试）
    uv run pytest tests/e2e/smt_classifier/ -v -m e2e

    # 排除慢速测试
    uv run pytest tests/e2e/smt_classifier/ -v -m "e2e and not slow"
"""

from __future__ import annotations

__all__ = []
