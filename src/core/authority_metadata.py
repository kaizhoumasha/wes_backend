"""Authority Metadata 契约（CEO-005 / C3 不变量）。

为外部权威 QueryPort（WMS MasterData / Document / InventoryQuery /
ReconciliationQuery）和作业期投影查询响应提供统一的 authority 标记，
防止本地 active projection 冒充 WMS 全局库存（主计划 §7.5 C3 / §3.4
Authority Matrix / 影子 WMS 威胁 §7.1）。

四个必填字段：
- scope: 数据作用域（WORKLINE_LOCAL / WMS_GLOBAL / SESSION / ...）
- authority: 权威系统（WMS / ECS / WES / ...）
- source: 来源标识（wms_inventory_query / local_projection / ...）
- evidence_at: 证据时间（aware ISO UTC）

外部权威 QueryPort response 额外必填 source_version（主计划 §3.4 /
§6.5 drift 检测），由 ExternalAuthorityMetadata 强制。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

Scope = str
Authority = str


class AuthorityMetadata(BaseModel):
    """C3 不变量：查询响应强制带 scope/authority/source/evidence_at。

    用于作业期投影、本地配置查询等非外部权威响应；外部权威 QueryPort
    response 使用 ExternalAuthorityMetadata（额外强制 source_version）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(min_length=1, description="数据作用域，如 WORKLINE_LOCAL / SESSION")
    authority: str = Field(
        min_length=1,
        description="权威系统，如 WMS / ECS / WES",
    )
    source: str = Field(
        min_length=1,
        description="来源标识，如 wms_inventory_query / local_projection",
    )
    evidence_at: str = Field(
        min_length=1,
        description="证据时间，aware ISO UTC（如 2026-06-26T10:00:00Z）",
    )


class ExternalAuthorityMetadata(AuthorityMetadata):
    """外部权威 QueryPort response 的 authority metadata。

    额外强制 source_version（主计划 §3.4 / §6.5 drift 检测），
    用于 WMS MasterData / Document / InventoryQuery / ReconciliationQuery。
    """

    source_version: str = Field(
        min_length=1,
        description="外部权威事实版本，用于 drift 检测与增量对账",
    )


__all__ = ["AuthorityMetadata", "ExternalAuthorityMetadata"]
