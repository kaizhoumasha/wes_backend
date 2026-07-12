"""扫描 active surface 中已退役的 WorklineInbox 引用。"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class LegacySignature:
    key: str
    value: str
    reason: str


@dataclass(frozen=True, order=True, slots=True)
class LegacyReference:
    path: str
    line: int
    signature: str
    reason: str


LEGACY_SIGNATURES = (
    LegacySignature("legacy_symbol", "WorklineInbox", "引用已删除的 WorklineInbox symbol"),
    LegacySignature(
        "legacy_repository_symbol",
        "WorklineInboxRepository",
        "引用已删除的 WorklineInboxRepository symbol",
    ),
    LegacySignature("legacy_service_symbol", "WorklineInboxService", "引用已删除的 WorklineInboxService symbol"),
    LegacySignature("legacy_batch_processor_symbol", "InboxBatchProcessor", "引用已删除的 InboxBatchProcessor"),
    LegacySignature("legacy_consumer_symbol", "RuntimeInboxConsumer", "引用已删除的 RuntimeInboxConsumer facade"),
    LegacySignature("legacy_table", "wes_biz.workline_inbox", "引用已删除的旧 Inbox 表"),
    LegacySignature(
        "legacy_callback_owner",
        "callback_ingress_service.inbox_service",
        "引用已删除的 callback inbox service owner",
    ),
    LegacySignature("legacy_create_device", "create_device_event_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_result", "create_command_result_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_external", "create_external_http_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_internal", "create_internal_event_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature("legacy_create_timeout", "create_timeout_inbox", "调用已删除的旧 Inbox producer"),
    LegacySignature(
        "legacy_task",
        "src.celery_app.tasks.workline.process_inbox_batch",
        "引用已删除的旧 Inbox Celery task",
    ),
    LegacySignature("legacy_task_short", "process_inbox_batch", "引用已删除的旧 Inbox batch task"),
    LegacySignature("legacy_enqueue", "enqueue_workline_inbox", "调用已删除的旧 Inbox enqueue shim"),
    LegacySignature(
        "legacy_model_import",
        "src.app.runtime.orchestration.models.inbox",
        "import 已删除的旧 Inbox model module",
    ),
    LegacySignature(
        "legacy_model_member_import",
        "src.app.runtime.orchestration.models import inbox",
        "import 已删除的旧 Inbox model member",
    ),
    LegacySignature(
        "legacy_repository_import",
        "src.app.runtime.orchestration.repositories.inbox_repository",
        "import 已删除的旧 Inbox repository module",
    ),
    LegacySignature(
        "legacy_repository_member_import",
        "src.app.runtime.orchestration.repositories import inbox_repository",
        "import 已删除的旧 Inbox repository member",
    ),
    LegacySignature(
        "legacy_service_import",
        "src.app.runtime.orchestration.services.inbox.inbox_service",
        "import 已删除的旧 Inbox service module",
    ),
    LegacySignature(
        "legacy_service_member_import",
        "src.app.runtime.orchestration.services.inbox import inbox_service",
        "import 已删除的旧 Inbox service member",
    ),
    LegacySignature(
        "legacy_workline_model_import",
        "src.app.workline.models.inbox",
        "import 已删除的 Workline Inbox model module",
    ),
    LegacySignature(
        "legacy_workline_model_member_import",
        "src.app.workline.models import inbox",
        "import 已删除的 Workline Inbox model member",
    ),
    LegacySignature(
        "legacy_workline_repository_import",
        "src.app.workline.repositories.inbox_repository",
        "import 已删除的 Workline Inbox repository module",
    ),
    LegacySignature(
        "legacy_workline_repository_member_import",
        "src.app.workline.repositories import inbox_repository",
        "import 已删除的 Workline Inbox repository member",
    ),
    LegacySignature(
        "legacy_workline_service_import",
        "src.app.workline.services.inbox_service",
        "import 已删除的 Workline Inbox service module",
    ),
    LegacySignature(
        "legacy_workline_service_member_import",
        "src.app.workline.services import inbox_service",
        "import 已删除的 Workline Inbox service member",
    ),
    LegacySignature(
        "legacy_workline_batch_processor_import",
        "src.app.workline.services.inbox_batch_processor",
        "import 已删除的 Workline Inbox batch processor module",
    ),
    LegacySignature(
        "legacy_workline_batch_processor_member_import",
        "src.app.workline.services import inbox_batch_processor",
        "import 已删除的 Workline Inbox batch processor member",
    ),
    LegacySignature(
        "legacy_batch_processor_import",
        "src.app.runtime.orchestration.services.inbox.inbox_batch_processor",
        "import 已删除的 Runtime Inbox batch processor module",
    ),
    LegacySignature(
        "legacy_batch_processor_member_import",
        "src.app.runtime.orchestration.services.inbox import inbox_batch_processor",
        "import 已删除的 Runtime Inbox batch processor member",
    ),
    LegacySignature(
        "legacy_consumer_import",
        "src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
        "import 已删除的 Runtime Inbox consumer facade module",
    ),
    LegacySignature(
        "legacy_consumer_member_import",
        "src.app.runtime.orchestration.consumers import runtime_inbox_consumer",
        "import 已删除的 Runtime Inbox consumer facade member",
    ),
    LegacySignature(
        "legacy_consumer_repository_import",
        "src.app.runtime.orchestration.consumers.runtime_inbox_repository",
        "import 已删除的 Runtime Inbox consumer repository module",
    ),
    LegacySignature(
        "legacy_consumer_repository_member_import",
        "src.app.runtime.orchestration.consumers import runtime_inbox_repository",
        "import 已删除的 Runtime Inbox consumer repository member",
    ),
    LegacySignature(
        "legacy_claim_repository_import",
        "src.app.runtime.orchestration.repositories.runtime_inbox_claim_repository",
        "import 已删除的 Runtime Inbox claim repository module",
    ),
    LegacySignature(
        "legacy_claim_repository_member_import",
        "src.app.runtime.orchestration.repositories import runtime_inbox_claim_repository",
        "import 已删除的 Runtime Inbox claim repository member",
    ),
)

# 当前事实源使用显式文件清单；archive/superpowers plan/spec 不属于 current docs。
CURRENT_DOC_FILES = (
    "docs/architecture/file_index.md",
    "docs/architecture/runtime-orchestration-spec.md",
    "docs/architecture/runtime-ownership-map.md",
    "docs/business/e2e_conveyor_plan.md",
    "docs/business/workline_business_data_event_flow_spec.md",
    "docs/business/workline_runtime_workflow_guide.md",
    "docs/contracts/observability-contract.md",
    "docs/architecture/adr/2026-05-26-wms-integration-domain.md",
)

# 精确到“文件 + 签名”的历史/负向证据 allowlist；不跳过整文件。
_ALL_SIGNATURE_KEYS = frozenset(signature.key for signature in LEGACY_SIGNATURES)
ALLOWED_EVIDENCE: dict[str, frozenset[str]] = {
    "scripts/workline_inbox_retirement_guardrail.py": _ALL_SIGNATURE_KEYS,
    "tests/architecture/test_workline_inbox_retirement_guardrail.py": _ALL_SIGNATURE_KEYS,
    "migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py": _ALL_SIGNATURE_KEYS,
    "migrations/versions/retire_workline_inbox.py": _ALL_SIGNATURE_KEYS,
    "tests/integration/test_runtime_inbox_migration_postgresql.py": _ALL_SIGNATURE_KEYS,
    "tests/deployment/test_retire_workline_inbox_migration.py": _ALL_SIGNATURE_KEYS,
    "tests/deployment/test_runtime_inbox_celery_cutover.py": frozenset(
        {"legacy_task", "legacy_task_short", "legacy_enqueue"}
    ),
    "tests/api/test_runtime_inbox_enqueue_contract.py": frozenset({"legacy_enqueue"}),
    "tests/sys/test_outbox_delivery.py": frozenset({"legacy_enqueue"}),
    "tests/deployment/test_reset_runtime_data.py": frozenset({"legacy_symbol", "legacy_table"}),
    "scripts/generate_legacy_matrix.py": frozenset(
        {
            "legacy_batch_processor_symbol",
            "legacy_repository_import",
            "legacy_service_import",
            "legacy_workline_batch_processor_import",
            "legacy_workline_repository_import",
            "legacy_workline_service_import",
        }
    ),
    "scripts/architecture-guardrails.sh": frozenset({"legacy_consumer_repository_import"}),
    "tests/architecture/test_legacy_matrix_contract.py": frozenset(
        {"legacy_batch_processor_symbol", "legacy_workline_batch_processor_import"}
    ),
    "tests/architecture/test_legacy_runtime_import_guardrail.py": frozenset({"legacy_consumer_symbol"}),
    "tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py": frozenset(
        {"legacy_claim_repository_import", "legacy_consumer_repository_import"}
    ),
    "tests/architecture/test_runtime_inbox_processor_ownership.py": frozenset({"legacy_batch_processor_symbol"}),
    "tests/architecture/test_workline_service_shim_contract.py": frozenset({"legacy_batch_processor_symbol"}),
    "docs/architecture/file_index.md": frozenset(
        {"legacy_batch_processor_symbol", "legacy_consumer_symbol", "legacy_symbol"}
    ),
    "docs/architecture/runtime-ownership-map.md": frozenset({"legacy_consumer_symbol"}),
    "docs/architecture/runtime-orchestration-spec.md": frozenset({"legacy_symbol", "legacy_table"}),
}


def _signature_pattern(signature: LegacySignature) -> re.Pattern[str]:
    """生成只跨标点/引号/拼接符的模式，避免把远距离普通单词拼成命中。"""

    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", signature.value)
    words = re.findall(r"[A-Za-z0-9]+", camel_split)
    separator = r"(?:|[\s]*['\"`+._/\\-][\s'\"`+._/\\-]*)" if signature.key.endswith("_symbol") else r"[\s'\"`+._/\\-]*"
    expression = separator.join(re.escape(word) for word in words)
    return re.compile(rf"(?<![A-Za-z0-9_]){expression}(?![A-Za-z0-9_])", re.IGNORECASE)


_SIGNATURE_PATTERNS = tuple((signature, _signature_pattern(signature)) for signature in LEGACY_SIGNATURES)


def _is_archived_or_plan(path: str) -> bool:
    parts = Path(path).parts
    return "archive" in parts or ("superpowers" in parts and ("plans" in parts or "specs" in parts))


def _iter_policy_files(repo_root: Path, roots: tuple[str, ...] | None) -> list[Path]:
    files: set[Path] = set()
    if roots is None:
        policies = (("src", (".py",)), ("tests", (".py",)), ("scripts", (".py", ".sh")))
        for root_name, suffixes in policies:
            root = repo_root / root_name
            if root.exists():
                files.update(path for path in root.rglob("*") if path.is_file() and path.suffix in suffixes)
        files.update(repo_root / relative for relative in CURRENT_DOC_FILES if (repo_root / relative).is_file())
        historical = ("migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py",)
        files.update(repo_root / relative for relative in historical if (repo_root / relative).is_file())
    else:
        for root_name in roots:
            root = repo_root / root_name
            if not root.exists():
                continue
            suffixes = (".py", ".sh") if root_name == "scripts" else ((".md",) if root_name == "docs" else (".py",))
            files.update(path for path in root.rglob("*") if path.is_file() and path.suffix in suffixes)
    return sorted(files)


def find_legacy_references(
    *,
    repo_root: Path = REPO_ROOT,
    roots: tuple[str, ...] | None = None,
) -> list[LegacyReference]:
    """按统一策略返回未被精确证据 allowlist 覆盖的引用。"""

    findings: set[LegacyReference] = set()
    for path in _iter_policy_files(repo_root, roots):
        relative = path.relative_to(repo_root).as_posix()
        if _is_archived_or_plan(relative):
            continue
        content = path.read_text(encoding="utf-8")
        allowed = ALLOWED_EVIDENCE.get(relative, frozenset())
        for signature, pattern in _SIGNATURE_PATTERNS:
            content_match = pattern.search(content)
            path_match = None
            if signature.key in {"legacy_symbol", "legacy_repository_symbol", "legacy_service_symbol"}:
                path_match = pattern.search(path.name)
            elif signature.key.endswith("_import"):
                path_match = pattern.search(relative.replace("/", "."))
            if content_match is None and path_match is None:
                continue
            if signature.key in allowed:
                continue
            line = 1 if content_match is None else content.count("\n", 0, content_match.start()) + 1
            findings.add(LegacyReference(relative, line, signature.key, signature.reason))
    return sorted(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "tsv"), default="text")
    args = parser.parse_args()
    findings = find_legacy_references()
    for finding in findings:
        if args.format == "tsv":
            print(f"{finding.path}\t{finding.line}\t{finding.reason}")
        else:
            print(f"{finding.path}:{finding.line}: {finding.reason}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
