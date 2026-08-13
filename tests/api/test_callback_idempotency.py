"""External callback 幂等性专项测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.api import callback_test_support


@pytest.fixture(autouse=True)
def mock_fast_fail_check():
    yield from callback_test_support.mock_fast_fail_check.__wrapped__()


@pytest.mark.asyncio
async def test_runtime_duplicate_external_callback_skips_second_enqueue() -> None:
    db = callback_test_support.db_session.__wrapped__()
    request = callback_test_support.build_request.__wrapped__()
    runtime_write = AsyncMock(
        side_effect=[
            SimpleNamespace(created=True, record=SimpleNamespace(id=901)),
            SimpleNamespace(created=False, record=SimpleNamespace(id=901)),
        ]
    )
    payload = callback_test_support.create_external_payload()

    with (
        patch(
            "src.app.callback.services.callback_orchestration_service."
            "callback_orchestration_service._runtime_inbox_writer.write_external_callback",
            new=runtime_write,
        ),
        patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as enqueue,
        patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-ext-101", "req-ext-102"]),
    ):
        from src.app.callback.v1.callback import callback_external

        first = await callback_external(
            request=request(body=payload, path="/api/v1/callback/external"),
            db=db,
        )
        second = await callback_external(
            request=request(body=payload, path="/api/v1/callback/external"),
            db=db,
        )

    assert first["code"] == "1000"
    assert second["code"] == "1000"
    assert second["data"].status == "duplicate"
    assert enqueue.call_count == 1
    assert runtime_write.await_count == 2
