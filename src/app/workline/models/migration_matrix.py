"""跨环境 WorkLine 迁移矩阵与批准证据合同。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from .migration_inventory import WorklineMigrationInventoryReport  # noqa: TC001 - Pydantic 运行时解析嵌套模型

_NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenMigrationMatrixModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorklineMigrationMatrixIssueCode(str, Enum):
    """跨环境 inventory gate 的稳定阻断码。"""

    MISSING_REQUIRED_ENVIRONMENT = "MISSING_REQUIRED_ENVIRONMENT"
    UNEXPECTED_ENVIRONMENT = "UNEXPECTED_ENVIRONMENT"
    INVENTORY_NOT_READY = "INVENTORY_NOT_READY"
    INDEX_DIGEST_MISMATCH = "INDEX_DIGEST_MISMATCH"
    PROVIDER_PROFILE_CATALOG_MISMATCH = "PROVIDER_PROFILE_CATALOG_MISMATCH"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    APPROVAL_DIGEST_MISMATCH = "APPROVAL_DIGEST_MISMATCH"
    APPROVAL_REPORT_MISMATCH = "APPROVAL_REPORT_MISMATCH"


class WorklineInventoryApprovalEvidence(_FrozenMigrationMatrixModel):
    """人工批准与一个不可变单环境 inventory digest 的绑定证据。"""

    environment: _NonBlankString
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_generated_at: AwareDatetime
    approved_by: _NonBlankString
    approved_at: AwareDatetime
    reason: _NonBlankString


class WorklineMigrationMatrixIssue(_FrozenMigrationMatrixModel):
    """跨环境矩阵阻断项。"""

    code: WorklineMigrationMatrixIssueCode
    message: _NonBlankString
    environment: _NonBlankString | None = None


class WorklineMigrationMatrixReport(_FrozenMigrationMatrixModel):
    """T8 可复用但不执行切流的完整 inventory preflight 输入。"""

    schema_version: Literal["workline-migration-matrix.v1"] = "workline-migration-matrix.v1"
    generated_at: AwareDatetime
    matrix_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_gate_ready: bool
    required_environments: tuple[_NonBlankString, ...]
    inventories: tuple[WorklineMigrationInventoryReport, ...]
    approvals: tuple[WorklineInventoryApprovalEvidence, ...]
    issues: tuple[WorklineMigrationMatrixIssue, ...] = ()
