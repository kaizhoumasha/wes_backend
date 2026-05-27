from typing import Any


class OutboxDispatchService:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def dispatch(self, db: Any, limit: int = 50) -> dict[str, int]:
        _ = db, limit
        return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
