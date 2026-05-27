from unittest.mock import AsyncMock, patch

import pytest

from src.app.sys.services.outbox_delivery import dispatch_external_http, dispatch_internal_signal


@pytest.mark.asyncio
async def test_dispatch_external_http_success() -> None:
    # We will implement this correctly when extracting
    pass


@pytest.mark.asyncio
async def test_dispatch_internal_signal_success() -> None:
    pass
