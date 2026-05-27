from typing import Any


async def dispatch_external_http(outbox: Any, endpoint_registry: Any, http_sender: Any) -> bool:
    _ = outbox, endpoint_registry, http_sender
    return False


async def dispatch_internal_signal(outbox: Any) -> bool:
    _ = outbox
    return False
