"""最终 Mock 镜像必须同时支持 Compose 声明的 ECS/WMS 入口。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FEASIBILITY_REPORT = (
    Path(__file__).resolve().parents[2] / "docs" / "operations" / "wms-northbound-feasibility-report.md"
)


def _reported_mock_image_digest(report: str, image_name: str) -> str:
    match = re.search(rf"{image_name} Docker image\s+`(sha256:[0-9a-f]{{64}})`", report)
    assert match is not None, f"{image_name} image digest is missing from feasibility report"
    return match.group(1)


@pytest.mark.parametrize(
    ("image_env", "default_image", "module_name"),
    (
        ("MOCK_ECS_IMAGE", "wes-mock:ecs", "ecs_mock_server"),
        ("MOCK_WMS_IMAGE", "wes-mock:wms", "wms_mock_server"),
    ),
)
def test_mock_image_imports_compose_entrypoint(
    image_env: str,
    default_image: str,
    module_name: str,
) -> None:
    image = os.getenv(image_env, default_image)
    docker = shutil.which("docker")
    assert docker is not None

    completed = subprocess.run(
        [docker, "run", "--rm", image, "python", "-c", f"import {module_name}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr


def test_wms_mock_image_accepts_fractional_time_contract_overrides() -> None:
    docker = shutil.which("docker")
    assert docker is not None

    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2.5",
            "-e",
            "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS=30.5",
            "-e",
            "WMS_EFFECT_STATUS_TIMEOUT_SECONDS=2.5",
            "wes-mock:wms",
            "python",
            "-c",
            (
                "from fastapi.testclient import TestClient; import wms_mock_server as module; "
                "contract=TestClient(module.app).get('/northbound/contract').json(); "
                "assert contract['status_visibility_sla_seconds'] == 2.5; "
                "assert contract['submit_deadline_seconds'] == 30.5; "
                "assert contract['status_deadline_seconds'] == 2.5"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("image_name", ("ECS", "WMS"))
def test_feasibility_report_records_point_in_time_mock_image_digest(image_name: str) -> None:
    report = FEASIBILITY_REPORT.read_text(encoding="utf-8")
    assert _reported_mock_image_digest(report, image_name).startswith("sha256:")
