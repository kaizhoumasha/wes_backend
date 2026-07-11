"""Runtime orchestration repository composition root。"""

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import runtime_inbox_repository
from src.app.runtime.orchestration.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.workline.repositories.workline_repository import WorkLineRepository

workline_repository = WorkLineRepository(runtime_inbox_query=runtime_inbox_repository)
smt_inbound_handoff_repository = SmtInboundHandoffRepository(runtime_inbox_query=runtime_inbox_repository)


__all__ = ["smt_inbound_handoff_repository", "workline_repository"]
