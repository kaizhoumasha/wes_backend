"""生成仍保留的 SystemCapability 确定性静态索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.runtime.system_capabilities.index_builder import SystemCapabilityIndexBuilder  # noqa: E402
from src.app.wms_integration.ports.effect_preparation import WmsEffectPreparationPort  # noqa: E402
from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort  # noqa: E402

SYSTEM_ROOT = REPO_ROOT / "src/app/runtime/system_capabilities"
DEFAULT_SYSTEM_OUTPUT = SYSTEM_ROOT / "generated_index.py"
SYSTEM_CAPABILITY_PORT_CATALOG: tuple[type[object], ...] = (WmsEffectPreparationPort, WmsQueryExecutionPort)
SYSTEM_CAPABILITY_ADMISSION_CATALOG = ("runtime", "wms.2026-07-28.full-factory")


def generate(*, system_output: Path, check: bool) -> int:
    """生成或验证核心 SystemCapability 索引。"""

    builder = SystemCapabilityIndexBuilder(
        known_ports=SYSTEM_CAPABILITY_PORT_CATALOG,
        known_admissions=SYSTEM_CAPABILITY_ADMISSION_CATALOG,
    )
    index = builder.build(builder.discover(root=SYSTEM_ROOT, package="src.app.runtime.system_capabilities"))
    print(f"system_capabilities: count={len(index.identities)} digest={index.digest}")
    current = system_output.read_text(encoding="utf-8") if system_output.exists() else None
    if current == index.source:
        return 0
    if check:
        print(f"generated index drift: {system_output}", file=sys.stderr)
        return 1
    system_output.write_text(index.source, encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查生成文件是否漂移")
    parser.add_argument("--system-output", type=Path, default=DEFAULT_SYSTEM_OUTPUT)
    args = parser.parse_args()
    return generate(system_output=args.system_output, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
