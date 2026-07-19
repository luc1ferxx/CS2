from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse


DEMO_ENVELOPE_LIMIT_BYTES = (1024 * 1024 * 1024) + (8 * 1024 * 1024)
VIDEO_ENVELOPE_LIMIT_BYTES = (2 * 1024 * 1024 * 1024) + (8 * 1024 * 1024)
WORKER_MEDIA_ENVELOPE_LIMIT_BYTES = VIDEO_ENVELOPE_LIMIT_BYTES
WORKER_RESULT_ENVELOPE_LIMIT_BYTES = 64 * 1024
STEAM_CREDENTIALS_JSON_LIMIT_BYTES = 4 * 1024

_VIDEO_UPLOAD_PATH = re.compile(r"^/demos/[^/]+/video/upload$")
_WORKER_MEDIA_PATH = re.compile(r"^/render-worker/jobs/[^/]+/media$")
_WORKER_RESULT_PATH = re.compile(r"^/render-worker/jobs/[^/]+/result$")

AsgiMessage = dict[str, Any]
Receive = Callable[[], Awaitable[AsgiMessage]]
Send = Callable[[AsgiMessage], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


class _RequestBodyTooLarge(Exception):
    pass


class MultipartRequestLimitMiddleware:
    def __init__(
        self,
        app: AsgiApp,
        *,
        demo_envelope_limit_bytes: int = DEMO_ENVELOPE_LIMIT_BYTES,
        video_envelope_limit_bytes: int = VIDEO_ENVELOPE_LIMIT_BYTES,
        worker_media_envelope_limit_bytes: int = WORKER_MEDIA_ENVELOPE_LIMIT_BYTES,
        worker_result_envelope_limit_bytes: int = WORKER_RESULT_ENVELOPE_LIMIT_BYTES,
        render_worker_token: str | None = None,
        max_concurrent_uploads: int = 1,
    ) -> None:
        if max_concurrent_uploads <= 0:
            raise ValueError("max_concurrent_uploads must be positive")
        self.app = app
        self.demo_envelope_limit_bytes = demo_envelope_limit_bytes
        self.video_envelope_limit_bytes = video_envelope_limit_bytes
        self.worker_media_envelope_limit_bytes = worker_media_envelope_limit_bytes
        self.worker_result_envelope_limit_bytes = worker_result_envelope_limit_bytes
        self.render_worker_token = render_worker_token
        self.max_concurrent_uploads = max_concurrent_uploads
        self._active_uploads = 0

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if self._is_worker_post_scope(scope) and not self._valid_worker_token(
            scope.get("headers", ())
        ):
            await _send_invalid_worker_token(scope, receive, send)
            return

        limit = self._limit_for_scope(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        if _has_trustworthy_oversized_content_length(
            scope.get("headers", ()),
            limit,
        ):
            await _send_request_too_large(scope, receive, send)
            return

        if self._active_uploads >= self.max_concurrent_uploads:
            await _send_upload_busy(scope, receive, send)
            return

        self._active_uploads += 1

        received_bytes = 0

        async def limited_receive() -> AsgiMessage:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await _send_request_too_large(scope, receive, send)
        finally:
            self._active_uploads -= 1

    def _limit_for_scope(self, scope: dict[str, Any]) -> int | None:
        if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
            return None
        path = scope.get("path", "")
        if path == "/uploads/demo":
            return self.demo_envelope_limit_bytes
        if _VIDEO_UPLOAD_PATH.fullmatch(path):
            return self.video_envelope_limit_bytes
        if _WORKER_MEDIA_PATH.fullmatch(path):
            return self.worker_media_envelope_limit_bytes
        if _WORKER_RESULT_PATH.fullmatch(path):
            return self.worker_result_envelope_limit_bytes
        return None

    @staticmethod
    def _is_worker_post_scope(scope: dict[str, Any]) -> bool:
        return (
            scope.get("type") == "http"
            and scope.get("method", "").upper() == "POST"
            and scope.get("path", "").startswith("/render-worker/")
        )

    def _valid_worker_token(
        self,
        headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...],
    ) -> bool:
        if self.render_worker_token is None:
            return True
        values = [
            value
            for name, value in headers
            if name.lower() == b"x-render-worker-token"
        ]
        if len(values) != 1:
            return False
        try:
            candidate = values[0].decode("utf-8")
        except UnicodeDecodeError:
            return False
        return secrets.compare_digest(candidate, self.render_worker_token)


class SensitiveJsonRequestLimitMiddleware:
    def __init__(
        self,
        app: AsgiApp,
        *,
        steam_credentials_limit_bytes: int = STEAM_CREDENTIALS_JSON_LIMIT_BYTES,
    ) -> None:
        if steam_credentials_limit_bytes <= 0:
            raise ValueError("steam_credentials_limit_bytes must be positive")
        self.app = app
        self.steam_credentials_limit_bytes = steam_credentials_limit_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if not (
            scope.get("type") == "http"
            and scope.get("method", "").upper() == "POST"
            and scope.get("path") == "/steam/connection/credentials"
        ):
            await self.app(scope, receive, send)
            return

        if _has_trustworthy_oversized_content_length(
            scope.get("headers", ()),
            self.steam_credentials_limit_bytes,
        ):
            await _send_sensitive_request_too_large(scope, receive, send)
            return

        received_bytes = 0

        async def limited_receive() -> AsgiMessage:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.steam_credentials_limit_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await _send_sensitive_request_too_large(scope, receive, send)


def _has_trustworthy_oversized_content_length(
    headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...],
    limit: int,
) -> bool:
    values = [value.strip() for name, value in headers if name.lower() == b"content-length"]
    if len(values) != 1 or not values[0].isdigit():
        return False

    normalized = values[0].lstrip(b"0") or b"0"
    limit_digits = str(limit).encode("ascii")
    return len(normalized) > len(limit_digits) or (
        len(normalized) == len(limit_digits) and normalized > limit_digits
    )


async def _send_request_too_large(
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=413,
        content={
            "detail": "Request body too large",
            "errorCode": "INTAKE_TOO_LARGE",
        },
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie, Origin"},
    )
    await response(scope, receive, send)


async def _send_invalid_worker_token(
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=401,
        content={"detail": "Invalid render worker token"},
        headers={"Cache-Control": "private, no-store"},
    )
    await response(scope, receive, send)


async def _send_sensitive_request_too_large(
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=413,
        content={
            "detail": "Request body too large",
            "errorCode": "REQUEST_TOO_LARGE",
        },
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie, Origin"},
    )
    await response(scope, receive, send)


async def _send_upload_busy(
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=503,
        content={
            "detail": "Artifact upload capacity is busy",
            "errorCode": "INTAKE_BUSY",
        },
        headers={"Cache-Control": "private, no-store", "Retry-After": "5"},
    )
    await response(scope, receive, send)
