"""Runtime orchestration repository composition root。"""

from src.app.runtime.orchestration.repositories.runtime_inbox_repository import runtime_inbox_repository
from src.app.workline.repositories.workline_repository import WorkLineRepository

workline_repository = WorkLineRepository(runtime_inbox_query=runtime_inbox_repository)
runtime_inbox_query = runtime_inbox_repository


__all__ = ["runtime_inbox_query", "workline_repository"]
