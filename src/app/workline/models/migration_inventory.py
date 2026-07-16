"""作业线迁移清单的稳定值对象合同。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

_NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


class _FrozenInventoryModel(BaseModel):
    """迁移清单值对象的不可变、严格输入边界。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorklineMigrationInventorySeverity(str, Enum):
    """迁移问题严重程度。"""

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"


class WorklineMigrationInventoryIssueCode(str, Enum):
    """迁移清单已知问题代码；未知值必须拒绝。"""

    ACTIVE_WITHOUT_PLUGIN = "ACTIVE_WITHOUT_PLUGIN"
    ACTIVE_WITHOUT_CONTRACT_VERSION = "ACTIVE_WITHOUT_CONTRACT_VERSION"
    UNKNOWN_PLUGIN = "UNKNOWN_PLUGIN"
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
    RUNTIME_REFERENCES_PRESENT = "RUNTIME_REFERENCES_PRESENT"


class WorklineRuntimeReferenceType(str, Enum):
    """阻塞迁移的运行态引用类型。"""

    SESSION = "SESSION"
    COMMAND = "COMMAND"
    OUTBOX = "OUTBOX"
    INBOX = "INBOX"
    RUNTIME_HOLD = "RUNTIME_HOLD"
    WORK_ITEM = "WORK_ITEM"
    INTENT = "INTENT"


class WorklineRuntimeReferenceSample(_FrozenInventoryModel):
    """运行态引用样本。"""

    type: WorklineRuntimeReferenceType
    reference: _NonBlankString
    status: _NonBlankString


class WorklineRuntimeReferenceSummary(_FrozenInventoryModel):
    """作业线关联运行态记录的计数摘要。"""

    sessions: _NonNegativeStrictInt
    commands: _NonNegativeStrictInt
    outboxes: _NonNegativeStrictInt
    inboxes: _NonNegativeStrictInt
    runtime_holds: _NonNegativeStrictInt
    total: _NonNegativeStrictInt
    sample: WorklineRuntimeReferenceSample | None = None


class WorklineRuntimeExtensionReference(_FrozenInventoryModel):
    """WorkItem/Intent 对插件 binding 与生成索引的只读引用。"""

    type: Literal[WorklineRuntimeReferenceType.WORK_ITEM, WorklineRuntimeReferenceType.INTENT]
    reference: _NonBlankString
    plugin_key: _NonBlankString | None = None
    plugin_binding_id: int | None = None
    plugin_binding_version: int | None = None
    plugin_config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plugin_index_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class WorklineMigrationInventoryIssue(_FrozenInventoryModel):
    """迁移清单问题。"""

    code: WorklineMigrationInventoryIssueCode
    severity: WorklineMigrationInventorySeverity
    message: _NonBlankString
    workline_id: int | None = None
    line_code: _NonBlankString | None = None


class WorklineMigrationInventoryItem(_FrozenInventoryModel):
    """单条作业线迁移盘点结果。"""

    workline_id: int
    line_code: _NonBlankString
    is_active: bool
    plugin_key: _NonBlankString | None
    configured_contract_version: _NonBlankString | None
    catalog_contract_version: _NonBlankString | None
    run_mode: _NonBlankString
    active_plugin_binding_id: int | None = None
    active_plugin_binding_version: int | None = None
    active_plugin_config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    active_plugin_index_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_requirements: tuple[_NonBlankString, ...] = ()
    port_requirements: tuple[_NonBlankString, ...] = ()
    runtime_extension_references: tuple[WorklineRuntimeExtensionReference, ...] = ()
    runtime_references: WorklineRuntimeReferenceSummary
    foundation_ready: bool
    issues: tuple[WorklineMigrationInventoryIssue, ...] = ()


class WorklineProviderProfileInventoryItem(_FrozenInventoryModel):
    """Provider profile 目录中的稳定迁移视图。"""

    provider_code: _NonBlankString
    contract_version: _NonBlankString
    environment: _NonBlankString
    runtime_capabilities_query: tuple[_NonBlankString, ...]
    runtime_capabilities_effect: tuple[_NonBlankString, ...]


class WorklineMigrationInventoryReport(_FrozenInventoryModel):
    """作业线迁移清单报告。"""

    schema_version: Literal["workline-migration-inventory-foundation.v1"] = "workline-migration-inventory-foundation.v1"
    environment: _NonBlankString
    generated_at: AwareDatetime
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    foundation_ready: bool
    worklines: tuple[WorklineMigrationInventoryItem, ...] = ()
    provider_profile_catalog: tuple[WorklineProviderProfileInventoryItem, ...] = ()
    issues: tuple[WorklineMigrationInventoryIssue, ...] = ()
