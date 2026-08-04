"""粗分机 SCAN 前置 Q19 admission 事实解析。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.workline_plugins.pre_attempt import PreAttemptResolution
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import optional_int, optional_str, resolve_required_pk


async def resolve_pre_attempt_facts(
    db: Any,
    *,
    session: Any,
    workline: Any,
    dispatch_request: Any,
    services: Any,
) -> PreAttemptResolution:
    """在粗分扫码 attempt 产生任何正常入料臂命令前锁定首次 Q19 decision。"""

    if getattr(dispatch_request, "logical_route", None) != "SCAN_COMPLETED":
        return PreAttemptResolution.not_applicable()
    raw_input = dispatch_request.raw_input
    data = payload_dict(raw_input.get("data"))
    reel_diameter = raw_input.get(
        "reel_diameter_mm",
        raw_input.get("reel_diameter", data.get("reel_diameter_mm", data.get("reel_diameter"))),
    )
    reel_thickness = raw_input.get(
        "reel_thickness_mm",
        raw_input.get("reel_thickness", data.get("reel_thickness_mm", data.get("reel_thickness"))),
    )
    six_in_one = {
        field_name: data.get(field_name) for field_name in ("HHPN", "MfrPN", "Qty", "DateCode", "LotCode", "PkgID")
    }
    raw_code = (
        optional_str(getattr(session, "barcode", None))
        or optional_str(raw_input.get("raw_code"))
        or optional_str(data.get("PkgID"))
    )
    session_id = optional_int(getattr(session, "id", None))
    session_code = optional_str(getattr(session, "session_code", None))
    station_code = optional_str(getattr(workline, "line_code", None))
    if (
        raw_code is None
        or session_id is None
        or session_code is None
        or station_code is None
        or reel_diameter is None
        or reel_thickness is None
    ):
        return PreAttemptResolution.blocked("WMS_Q19_REQUEST_FACTS_MISSING")

    from src.app.runtime.capabilities.material_flow.rough_sorter_q19_admission_service import (
        RoughSorterQ19AdmissionService,
    )
    from src.app.wms_integration.ports.document_operations import ValidateRoughSorterAdmissionRequest
    from src.app.wms_integration.ports.query_outcome import QuerySuccess

    try:
        request = ValidateRoughSorterAdmissionRequest.model_validate(
            {
                "raw_code": raw_code,
                "six_in_one": six_in_one,
                "reel_diameter_mm": reel_diameter,
                "reel_thickness_mm": reel_thickness,
                "station_code": station_code,
                "workline_id": resolve_required_pk(workline, "workline", "id", "workline_id"),
                "correlation_id": f"workline-session:{session_code}",
            }
        )
    except (TypeError, ValueError):
        return PreAttemptResolution.blocked("WMS_Q19_REQUEST_INVALID")
    q19_service = getattr(services, "rough_sorter_q19_admission_service", None)
    if q19_service is None:
        query_runtime = getattr(services, "wms_query_execution_port", None)
        if query_runtime is None or not callable(getattr(query_runtime, "project", None)):
            return PreAttemptResolution.blocked("WMS_Q19_RUNTIME_UNAVAILABLE")
        q19_service = RoughSorterQ19AdmissionService(query_runtime)
    outcome = await q19_service.resolve(db, session_id=session_id, request=request)
    if isinstance(outcome, QuerySuccess):
        return PreAttemptResolution.facts_changed()
    return PreAttemptResolution.blocked(
        optional_str(getattr(outcome, "reason_code", None)) or "WMS_Q19_OUTCOME_INVALID"
    )


__all__ = ["resolve_pre_attempt_facts"]
