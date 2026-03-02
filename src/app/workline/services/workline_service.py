"""WorkLine Service 层"""

from src.app.workline.models import WorkLine
from src.app.workline.repositories import WorkLineRepository, workline_repository
from src.core.base_service import BaseService


class WorkLineService(BaseService[WorkLine, WorkLineRepository]):
    """作业线业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            workline_repository,
            enable_cache=True,
            cache_prefix="app:workline:detail",
        )


# 创建单例
workline_service = WorkLineService()
