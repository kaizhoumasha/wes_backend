"""inventory QUERY production cutover 的持久化 readiness 门禁。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from src.app.runtime.system_capabilities.shadow_models import (
    QueryShadowReadinessApprovalRecord,
    QueryShadowReadinessReportRecord,
)
from src.app.runtime.system_capabilities.shadow_readiness import (
    QueryShadowReadinessApproval,
    QueryShadowReadinessReport,
    ReadinessGateError,
    require_approved_readiness_report,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_MATERIAL_FLOW_CONTRACT_VERSION
from src.app.wms_integration.ports.query_inventory_operation import OPERATION_IDENTITY

QUERY_INVENTORY_PRODUCTION_PROFILE = f"wms.{WMS_MATERIAL_FLOW_CONTRACT_VERSION}.production"


class QueryInventoryCutoverReadinessRepository:
    """只从 append-only report/approval 表加载最新目标授权。"""

    async def load_latest_authorization(
        self,
        db: Any,
        *,
        provider_profile_identity: str,
        operation_identity: str,
    ) -> tuple[QueryShadowReadinessReport, QueryShadowReadinessApproval] | None:
        report_result = await db.execute(
            select(QueryShadowReadinessReportRecord)
            .where(
                QueryShadowReadinessReportRecord.provider_profile_identity == provider_profile_identity,
                QueryShadowReadinessReportRecord.operation_identity == operation_identity,
            )
            .order_by(
                QueryShadowReadinessReportRecord.generated_at.desc(),
                QueryShadowReadinessReportRecord.report_id.desc(),
            )
            .limit(1)
        )
        report_record = report_result.scalar_one_or_none()
        if report_record is None:
            return None
        approval_result = await db.execute(
            select(QueryShadowReadinessApprovalRecord).where(
                QueryShadowReadinessApprovalRecord.report_id == report_record.report_id
            )
        )
        approval_record = approval_result.scalar_one_or_none()
        if approval_record is None:
            return None
        try:
            report = QueryShadowReadinessReport.model_validate(report_record.report_json)
            persisted_generated_at = _normalize_db_timestamp(report_record.generated_at)
        except (TypeError, ValidationError) as exc:
            raise ReadinessGateError("persisted readiness report metadata is malformed") from exc
        if (
            report.report_id != report_record.report_id
            or report.generated_at != persisted_generated_at
            or report.provider_profile_identity != report_record.provider_profile_identity
            or report.operation_identity != report_record.operation_identity
            or report.verdict.value != report_record.verdict
        ):
            raise ReadinessGateError(
                "persisted readiness report generated_at or metadata does not match immutable content"
            )
        approval = QueryShadowReadinessApproval(
            report_id=approval_record.report_id,
            decision=approval_record.decision,
            approved_by=approval_record.approved_by,
            approved_at=approval_record.approved_at,
        )
        return report, approval


def _normalize_db_timestamp(value: object) -> datetime:
    """数据库时间是 naive UTC；比较 immutable JSON 前统一成 aware UTC。"""

    if not isinstance(value, datetime):
        raise TypeError("persisted timestamp must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class QueryInventoryCutoverReadinessService:
    """production 启动/发布只能由同一 immutable READY+GO report 放行。"""

    def __init__(self, repository: QueryInventoryCutoverReadinessRepository | Any | None = None) -> None:
        self._repository = repository or query_inventory_cutover_readiness_repository

    async def require_ready(self, db: Any, *, app_env: str) -> None:
        if app_env != "prod":
            return
        authorization = await self._repository.load_latest_authorization(
            db,
            provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
            operation_identity=OPERATION_IDENTITY,
        )
        if authorization is None:
            raise ReadinessGateError("inventory QUERY cutover authorization is missing")
        report, approval = authorization
        if (
            report.provider_profile_identity != QUERY_INVENTORY_PRODUCTION_PROFILE
            or report.operation_identity != OPERATION_IDENTITY
        ):
            raise ReadinessGateError("readiness report does not authorize the production inventory QUERY cutover")
        require_approved_readiness_report(report=report, approval=approval)


query_inventory_cutover_readiness_repository = QueryInventoryCutoverReadinessRepository()
query_inventory_cutover_readiness_service = QueryInventoryCutoverReadinessService(
    query_inventory_cutover_readiness_repository
)

__all__ = [
    "QUERY_INVENTORY_PRODUCTION_PROFILE",
    "QueryInventoryCutoverReadinessRepository",
    "QueryInventoryCutoverReadinessService",
    "query_inventory_cutover_readiness_repository",
    "query_inventory_cutover_readiness_service",
]
