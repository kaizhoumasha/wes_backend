"""QA ISSUE-007：Mock 镜像必须能从固定工作目录导入打包的 ``src`` 命名空间。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mock_image_exposes_app_root_on_pythonpath() -> None:
    dockerfile = (REPO_ROOT / "tests" / "mock" / "Dockerfile").read_text(encoding="utf-8")

    assert "ENV PYTHONPATH=/app" in dockerfile
