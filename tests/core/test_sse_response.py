"""慢发送必须有界结束并释放生成器，不依赖任何业务事件。"""

import asyncio

import pytest

from src.core.sse import BoundedStreamingResponse


async def test_blocked_send_times_out_and_closes_source(monkeypatch) -> None:
    monkeypatch.setattr("src.core.sse.SEND_TIMEOUT_SECONDS", 0.01)
    closed = []

    async def source():
        try:
            yield "data: sample\n\n"
        finally:
            closed.append(True)

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.Event().wait()

    response = BoundedStreamingResponse(source())
    with pytest.raises(TimeoutError):
        await response.stream_response(send)
    assert closed == [True]
