"""DEVICE_COMMAND_BOUNDARY guardrail: DeviceCommand 不含 PLC/坐标/关节/安全回路字段。

验证禁止字段被识别; 字段白名单见 device-command-contract.md §5。
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"

FORBIDDEN_FIELDS = {"plc", "coordinate", "joint_angle", "x_coord", "y_coord", "safety_loop"}


def test_device_command_boundary_forbidden_fields_identified():
    """DEVICE_COMMAND_BOUNDARY 禁止字段集合覆盖 PLC/坐标/关节/安全回路。"""
    violation_field = "joint_angle: float"
    matched = {f for f in FORBIDDEN_FIELDS if f in violation_field}
    assert matched


def test_device_command_boundary_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_device_command_boundary" in content
    for field in ("plc", "coordinate", "joint", "safety_loop"):
        assert field in content


def _run_guardrails(extra_files: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """在隔离 tmp 仓库结构里跑 guardrail，避免污染主仓库。

    复制 scanner 脚本 + allowlist 到 tmp，只放入指定测试文件，跑 enforced mode。
    """
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "scripts").mkdir()
        shutil.copy(GUARDRAIL, tmp_path / "scripts" / "architecture-guardrails.sh")
        shutil.copy(
            REPO_ROOT / "scripts" / "workline_inbox_retirement_guardrail.py",
            tmp_path / "scripts" / "workline_inbox_retirement_guardrail.py",
        )
        (tmp_path / "scripts" / "architecture-guardrails.allowlist").write_text("# empty allowlist for isolated test\n")
        (tmp_path / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "architecture" / "legacy-cleanup-matrix.csv").write_text("entry_id,drop_phase\n")
        for sub in ("src/app/device", "src/app/workline", "src/app/runtime"):
            (tmp_path / sub).mkdir(parents=True)
        for rel_path, content in (extra_files or {}).items():
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return subprocess.run(
            ["bash", "scripts/architecture-guardrails.sh", "--mode", "enforced"],  # noqa: S607
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )


def test_device_command_boundary_catches_real_forbidden_field_declaration():
    """DEVICE_COMMAND_BOUNDARY 必须捕获真实 Pydantic 字段声明（plc_address: str = Field(...)）。"""
    result = _run_guardrails(
        extra_files={
            "src/app/device/models/evil.py": textwrap.dedent(
                """
                from pydantic import BaseModel, Field


                class EvilCommand(BaseModel):
                    plc_address: str = Field(description="should be caught")
                    coordinate: float = Field(default=0.0)
                """
            )
        }
    )
    assert result.returncode != 0, f"scanner 必须捕获真实 PLC/coordinate 字段声明\nstderr={result.stderr}"
    assert "[DEVICE_COMMAND_BOUNDARY]" in result.stderr
    assert "evil.py" in result.stderr


def test_device_command_boundary_ignores_forbidden_keys_inside_blacklist_set():
    """DEVICE_COMMAND_BOUNDARY 不应误报 H4 黑名单常量集合。

    回归测试: 工作区 review 发现 9 个 DEVICE_COMMAND_BOUNDARY false positive,
    都来自 _FORBIDDEN_PARAM_KEYS = {"plc", "plc_address", ...} 这种黑名单字面量。
    """
    result = _run_guardrails(
        extra_files={
            "src/app/device/models/safe.py": textwrap.dedent(
                """
                _FORBIDDEN_PARAM_KEYS = {
                    "plc",
                    "plc_address",
                    "coordinate",
                    "joint_angle",
                    "x_coord",
                    "y_coord",
                    "safety_loop",
                }


                def is_forbidden(key: str) -> bool:
                    return key.lower() in _FORBIDDEN_PARAM_KEYS
                """
            )
        }
    )
    assert result.returncode == 0, f"scanner 不应误报黑名单常量\nstderr={result.stderr}"
    assert "[DEVICE_COMMAND_BOUNDARY]" not in result.stderr


def test_device_command_boundary_ignores_forbidden_keywords_in_docstrings():
    """DEVICE_COMMAND_BOUNDARY 不应误报 docstring/注释里描述禁止字段的文字。"""
    result = _run_guardrails(
        extra_files={
            "src/app/device/models/docs.py": textwrap.dedent(
                '''
                """模块文档.

                禁止字段示例: plc_address / coordinate / joint_angle / safety_loop
                这些字段名禁止出现在 DeviceCommand 中。
                """


                def noop() -> None:
                    # plc_address / coordinate 等是禁止的字段名
                    pass
                '''
            )
        }
    )
    assert result.returncode == 0, f"scanner 不应误报 docstring/注释\nstderr={result.stderr}"
    assert "[DEVICE_COMMAND_BOUNDARY]" not in result.stderr
