"""部署阶段真实 TCP WMS conformance CLI；导入时不建立外部连接。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.app.runtime.system_capabilities.wms.provider_conformance import (
    WMS_PROVIDER_CONFORMANCE_CASES,
    ConformanceTarget,
    build_wms_release_conformance_report,
)
from src.app.sys.external_http_credentials import build_environment_external_http_credential_provider
from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
from src.app.wms_integration.provider_profile import WmsProviderProfileSettings
from tests.support.wms_conformance_runner import (
    RealTcpConformanceRunner,
    RealTcpScenarioAsset,
    build_real_tcp_scenario_matrix,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full WMS REAL_TCP conformance matrix")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--scenario-asset", type=Path, required=True)
    parser.add_argument("--wms-build-version", required=True)
    parser.add_argument("--responsible-person", required=True)
    parser.add_argument("--confirm-execution-safety", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> str:
    if not args.confirm_execution_safety:
        raise ValueError("REAL_TCP conformance requires explicit execution safety confirmation")
    profile_payload = json.loads(args.profile.read_text(encoding="utf-8"))
    compiled_profile = compile_wms_provider_profile(WmsProviderProfileSettings.model_validate(profile_payload))
    asset = RealTcpScenarioAsset.model_validate_json(args.scenario_asset.read_text(encoding="utf-8"))
    scenarios = build_real_tcp_scenario_matrix(
        cases=WMS_PROVIDER_CONFORMANCE_CASES,
        scenarios=asset.scenarios,
    )
    async with httpx.AsyncClient(trust_env=False) as client:
        runner = RealTcpConformanceRunner(
            scenarios,
            compiled_profile=compiled_profile,
            client=client,
            credential_provider=build_environment_external_http_credential_provider(),
        )
        observations = tuple([await runner.execute(case) for case in WMS_PROVIDER_CONFORMANCE_CASES])
    report = build_wms_release_conformance_report(
        compiled_profile=compiled_profile,
        cases=WMS_PROVIDER_CONFORMANCE_CASES,
        observations=observations,
        target=ConformanceTarget.REAL_TCP,
        fixture_digest=asset.digest,
        generated_at=datetime.now(UTC),
        wms_build_version=args.wms_build_version,
        responsible_person=args.responsible_person,
        execution_safety_confirmed=True,
    )
    return report.model_dump_json(indent=2)


def main() -> None:
    print(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
