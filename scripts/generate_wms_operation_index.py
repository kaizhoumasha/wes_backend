"""生成 WMS typed operation 的确定性静态索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.runtime.system_capabilities.wms.operation_index_builder import WmsOperationIndexBuilder  # noqa: E402
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "src/app/runtime/system_capabilities/wms/generated_operation_index.py"


def generate(*, output: Path, check: bool) -> int:
    generated = WmsOperationIndexBuilder.build(WMS_PROVIDER_PROFILE)
    print(f"wms_operations: count={len(generated.identities)} digest={generated.digest}")
    current = output.read_text(encoding="utf-8") if output.exists() else None
    if current == generated.source:
        return 0
    if check:
        print(f"generated index drift: {output}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated.source, encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return generate(output=args.output, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
