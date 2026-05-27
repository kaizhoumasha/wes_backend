from typing import Any


class DeviceCommandGateway:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def dispatch(self, db: Any, outbox: Any) -> bool:
        _ = db, outbox
        return False

    async def reserve_sandbox_command(self, db: Any, outbox: Any) -> bool:
        _ = db, outbox
        return False
