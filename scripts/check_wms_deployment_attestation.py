"""生成或校验离线 WMS deployment attestation。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import cast

# 直接以 `python scripts/...` 运行时，确保仓库根目录可导入。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from src.app.wms_integration.deployment_attestation import (  # noqa: E402
    WmsDeploymentRole,
    build_wms_deployment_attestation,
    parse_wms_deployment_attestation_lines,
    summarize_wms_deployment_attestations,
    verify_wms_deployment_attestations,
)
from src.core.conf import settings  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("emit", help="从受信容器环境输出单角色 compact JSON artifact")
    subparsers.add_parser("verify-stdin", help="从 stdin 校验四行 compact JSON artifact")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "emit":
            role = cast("WmsDeploymentRole", os.environ.get("WMS_DEPLOYMENT_ROLE", ""))
            image_identity = os.environ.get("WMS_DEPLOYMENT_IMAGE_ID", "")
            default_queues = "default,celery,device" if role == "wes-worker" else ""
            artifact = build_wms_deployment_attestation(
                role=role,
                image_identity=image_identity,
                settings_source=settings,
                worker_queues=os.getenv("CELERY_WORKER_QUEUES", default_queues),
                worker_concurrency=os.getenv("CELERY_WORKER_CONCURRENCY"),
            )
            print(artifact.model_dump_json())
        else:
            image_identity = os.environ.get("WMS_DEPLOYMENT_IMAGE_ID", "")
            artifacts = parse_wms_deployment_attestation_lines(sys.stdin.read().splitlines())
            verified = verify_wms_deployment_attestations(
                artifacts,
                settings_source=settings,
                expected_image_identity=image_identity,
            )
            summary = summarize_wms_deployment_attestations(verified)
            print(summary.model_dump_json(indent=2))
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        print(f"WMS deployment attestation rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
