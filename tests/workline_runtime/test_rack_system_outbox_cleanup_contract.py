from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src", "tests", "migrations")
SELF = Path(__file__).resolve()


def test_rack_system_outbox_cleanup_has_no_legacy_workline_rack_or_outbox_refs() -> None:
    forbidden_fragments = (
        "Workline" + "Outbox",
        "Workline" + "OutboxRepository",
        "Workline" + "RackTask",
        "Workline" + "RackTaskRepository",
        "Workline" + "RackOperationService",
        "Workline" + "RackTaskLifecycleService",
        "workline" + "_outbox",
        "workline" + "_rack_tasks",
        "src.app.workline.services.rack" + "_gateway",
        "src.app.workline.services.rack" + "_operation_service",
        "src.app.workline.services.rack" + "_task_service",
    )
    ignored_parts = {"__pycache__", ".pytest_cache"}
    offenders: list[str] = []

    for root in SCAN_ROOTS:
        for path in (PROJECT_ROOT / root).rglob("*"):
            if path == SELF or not path.is_file() or ignored_parts.intersection(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # noqa: S112 - binary/generated files are irrelevant to this text contract.
                continue
            for fragment in forbidden_fragments:
                if fragment in text:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)} contains {fragment}")

    assert offenders == []
