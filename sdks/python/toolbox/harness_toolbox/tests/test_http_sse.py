import asyncio

import httpx
import pytest
from harness_common import ClientManager

from harness_toolbox.http import HTTPClientProvider
from harness_toolbox.sse import stream_sse


async def test_http_borrowers_share_by_policy_and_owner_closes():
    async with ClientManager() as owner:
        provider = HTTPClientProvider(3, 2)
        a, b = await asyncio.gather(owner.get(provider), owner.get(provider))
        observation = await owner.get(HTTPClientProvider(3, 2, pool="observation"))
        assert a is b and a is not observation
        assert not a.is_closed
    assert a.is_closed and observation.is_closed


class Fragments(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_sse_fragmented_utf8_crlf_multiline_done_and_whitespace():
    body = "data: 你好  \r\ndata: world\r\n\r\ndata: [DONE]\n\n".encode()
    stream = Fragments([bytes([b]) for b in body])
    frames = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        outcome = await stream_sse(
            client,
            "https://example.test/chat",
            done_marker=b"[DONE]",
            on_frame=lambda _, data: frames.append(data.decode()),
        )
        assert not client.is_closed  # observer borrows, never disposes the pool
    assert frames == ["你好  \nworld", "[DONE]"]
    assert outcome.events == 2 and outcome.nbytes == len(body)
    assert outcome.meta == {"saw_done": True}
    assert "first_byte_ms" in outcome.metrics and "ttft_ms" not in outcome.metrics
    assert stream.closed


async def test_sse_bounds_unterminated_event_and_closes_response():
    stream = Fragments([b"data: " + b"x" * 100])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        outcome = await stream_sse(client, "https://example.test/chat", max_event_bytes=32)
    assert outcome.meta["exc"] == "ValueError" and stream.closed


async def test_sse_cancellation_propagates_and_closes_response():
    started = asyncio.Event()

    class Pending(Fragments):
        async def __aiter__(self):
            yield b"data: partial\n\n"
            started.set()
            await asyncio.Event().wait()

    stream = Pending([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        task = asyncio.create_task(stream_sse(client, "https://example.test/chat"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.is_closed and stream.closed
