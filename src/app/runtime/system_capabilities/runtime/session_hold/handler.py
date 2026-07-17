"""普通 Session Hold LOCAL_TRANSACTIONAL handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import SessionHoldAdmission, SessionHoldInput, SessionHoldOutput


class SessionHoldHandler:
    async def __call__(self, request: SessionHoldInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.session_hold_mutation_service import (
            StaleSessionPrecondition,
            session_hold_mutation_service,
        )

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, SessionHoldAdmission):
            raise TypeError("session hold effect requires typed admission")
        try:
            await session_hold_mutation_service.hold(
                ctx["db"],
                session=ctx["session"],
                failure_domain=request.failure_domain,
                reason_code=request.reason_code,
                message=request.message,
                fact_version=admission.fact_version,
                expected_status=admission.precondition.expected_status,
            )
        except StaleSessionPrecondition:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="session fact changed")
        return Success(payload=SessionHoldOutput(held=True, reason_code=request.reason_code))


__all__ = ["SessionHoldHandler"]
