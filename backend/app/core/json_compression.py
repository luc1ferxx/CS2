"""Gzip for JSON API responses only.

Starlette's GZipMiddleware compresses every response type. That would include
the private-media responses, whose Range offsets and Content-Length must stay
byte-exact, and the streamed render-worker downloads. This one compresses a
response only when all of these hold: the client accepts gzip, the request
carries no Range header, the body is JSON, it arrives as one complete message
(streamed bodies pass through untouched), nothing has encoded it already, and
it is at least ``minimum_size`` bytes.
"""

from __future__ import annotations

import gzip

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Compressing a replay (tens of MB) takes ~0.1 s; keep that off the event loop.
_THREADPOOL_THRESHOLD_BYTES = 64 * 1024


class JsonGzipMiddleware:
    def __init__(self, app: ASGIApp, *, minimum_size: int = 1024, compresslevel: int = 5) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_headers = Headers(scope=scope)
        if "range" in request_headers or not accepts_gzip(request_headers.get("accept-encoding", "")):
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        passthrough = False

        async def send_compressed(message: Message) -> None:
            nonlocal start_message, passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                start_message = message
                return
            if message["type"] != "http.response.body" or start_message is None:
                await send(message)
                return

            body: bytes = message.get("body", b"")
            if message.get("more_body", False) or not self._compressible(start_message, body):
                passthrough = True
                await send(start_message)
                await send(message)
                return

            if len(body) >= _THREADPOOL_THRESHOLD_BYTES:
                compressed = await run_in_threadpool(self._compress, body)
            else:
                compressed = self._compress(body)
            headers = MutableHeaders(scope=start_message)
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(compressed))
            headers.add_vary_header("Accept-Encoding")
            await send(start_message)
            await send({"type": "http.response.body", "body": compressed, "more_body": False})

        await self.app(scope, receive, send_compressed)

    def _compress(self, body: bytes) -> bytes:
        return gzip.compress(body, compresslevel=self.compresslevel, mtime=0)

    def _compressible(self, start_message: Message, body: bytes) -> bool:
        if len(body) < self.minimum_size:
            return False
        if start_message.get("status") in {204, 206, 304}:
            return False
        headers = Headers(raw=start_message.get("headers", []))
        if "content-encoding" in headers or "content-range" in headers:
            return False
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return media_type == "application/json" or media_type.endswith("+json")


def accepts_gzip(accept_encoding: str) -> bool:
    """True when the header lists gzip with a non-zero quality."""
    for entry in accept_encoding.split(","):
        coding, _, params = entry.partition(";")
        if coding.strip().lower() != "gzip":
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False
