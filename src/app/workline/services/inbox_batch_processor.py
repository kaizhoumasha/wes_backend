from typing import Any


class InboxBatchProcessor:
    def __init__(self, write_back_service: Any = None) -> None:
        self.write_back_service = write_back_service

    async def process_batch(self, db: Any, limit: int = 10) -> dict[str, int]:
        _ = db, limit
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}
