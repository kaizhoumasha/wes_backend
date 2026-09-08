"""SSE 慢连接发送边界，与事件所属业务无关。"""

import asyncio

from starlette.responses import StreamingResponse
from starlette.types import Message, Send

SEND_TIMEOUT_SECONDS = 5.0


class BoundedStreamingResponse(StreamingResponse):
    async def stream_response(self, send: Send) -> None:
        async def bounded_send(message: Message) -> None:
            async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                await send(message)

        try:
            await super().stream_response(bounded_send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()
