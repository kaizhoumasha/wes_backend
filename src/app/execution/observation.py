"""执行可靠对象的不可变只读投影；不是业务 Fact。"""

from pydantic import BaseModel, ConfigDict

from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus
from src.app.execution.models.wms_confirmation import WmsConfirmationStatus


class ConfirmationObservation(BaseModel):
    model_config = ConfigDict(frozen=True)
    operation: str
    operation_id: str
    status: WmsConfirmationStatus
    attempt_count: int
    retry_eligible: bool
    next_attempt_at: str | None
    deadline_at: str
    last_dispatch_at: str | None
    response_evidence_id: int | None
    response_result: str | None
    updated_at: str | None


class EvidenceObservation(BaseModel):
    model_config = ConfigDict(frozen=True)
    operation: str
    operation_id: str
    apply_status: InboundEvidenceApplyStatus
    received_at: str
    processed_at: str | None
    published_at: str | None
    decision_attempt_count: int
    decision_next_attempt_at: str | None
