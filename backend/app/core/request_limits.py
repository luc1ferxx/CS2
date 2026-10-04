from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.upload_slots import IntakeSlot, PartPool, is_upload_part_request

if TYPE_CHECKING:
    from app.services.upload_quota import UploadQuotaExceeded

logger = logging.getLogger(__name__)

DEMO_ENVELOPE_LIMIT_BYTES = (1024 * 1024 * 1024) + (8 * 1024 * 1024)
VIDEO_ENVELOPE_LIMIT_BYTES = (2 * 1024 * 1024 * 1024) + (8 * 1024 * 1024)
WORKER_MEDIA_ENVELOPE_LIMIT_BYTES = VIDEO_ENVELOPE_LIMIT_BYTES
WORKER_RESULT_ENVELOPE_LIMIT_BYTES = 64 * 1024
STEAM_CREDENTIALS_JSON_LIMIT_BYTES = 4 * 1024
# A chunked-upload part body is raw bytes; the slack only covers a client that
# miscounts, the route still requires the exact part length.
PART_ENVELOPE_OVERHEAD_BYTES = 64 * 1024
UPLOAD_PART_ENVELOPE_LIMIT_BYTES = (8 * 1024 * 1024) + PART_ENVELOPE_OVERHEAD_BYTES
DEFAULT_PART_POOL_SIZE = 8
INTAKE_BUSY_RETRY_AFTER_SECONDS = 5
PART_BUSY_RETRY_AFTER_SECONDS = 2

_VIDEO_UPLOAD_PATH = re.compile(r"^/demos/[^/]+/video/upload$")
_WORKER_MEDIA_PATH = re.compile(r"^/render-worker/jobs/[^/]+/media$")
_WORKER_RESULT_PATH = re.compile(r"^/render-worker/jobs/[^/]+/result$")


class _RequestBodyTooLarge(Exception):
    pass


class MultipartRequestLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        demo_envelope_limit_bytes: int = DEMO_ENVELOPE_LIMIT_BYTES,
        video_envelope_limit_bytes: int = VIDEO_ENVELOPE_LIMIT_BYTES,
        worker_media_envelope_limit_bytes: int = WORKER_MEDIA_ENVELOPE_LIMIT_BYTES,
        worker_result_envelope_limit_bytes: int = WORKER_RESULT_ENVELOPE_LIMIT_BYTES,
        render_worker_token: str | None = None,
        max_concurrent_uploads: int = 1,
        manual_video_upload_enabled: bool = True,
        demo_upload_precheck: Callable[[str], UploadQuotaExceeded | None] | None = None,
        upload_part_envelope_limit_bytes: int = UPLOAD_PART_ENVELOPE_LIMIT_BYTES,
        part_pool_size: int = DEFAULT_PART_POOL_SIZE,
        intake_slot: IntakeSlot | None = None,
        part_pool: PartPool | None = None,
    ) -> None:
        if max_concurrent_uploads <= 0:
            raise ValueError("max_concurrent_uploads must be positive")
        if part_pool_size <= 0:
            raise ValueError("part_pool_size must be positive")
        self.app = app
        self.demo_envelope_limit_bytes = demo_envelope_limit_bytes
        self.video_envelope_limit_bytes = video_envelope_limit_bytes
        self.worker_media_envelope_limit_bytes = worker_media_envelope_limit_bytes
        self.worker_result_envelope_limit_bytes = worker_result_envelope_limit_bytes
        self.render_worker_token = render_worker_token
        self.max_concurrent_uploads = max_concurrent_uploads
        self.manual_video_upload_enabled = manual_video_upload_enabled
        self.demo_upload_precheck = demo_upload_precheck
        self.upload_part_envelope_limit_bytes = upload_part_envelope_limit_bytes
        # The app passes the process-wide slot shared with the chunked upload's
        # `complete` route; a bare middleware (tests) gets its own.
        self.intake_slot = intake_slot if intake_slot is not None else IntakeSlot(max_concurrent_uploads)
        self.part_pool = part_pool if part_pool is not None else PartPool(part_pool_size)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if self._is_worker_post_scope(scope) and not self._valid_worker_token(
            scope.get("headers", ())
        ):
            await _send_invalid_worker_token(scope, receive, send)
            return

        # FastAPI parses a multipart body before any route dependency runs, so
        # the route's own 404 would come only after streaming the whole video
        # while holding the shared upload slot.
        if not self.manual_video_upload_enabled and self._is_manual_video_upload_scope(scope):
            await _send_not_found(scope, receive, send)
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

        # The route's own quota check would also run only after the whole file
        # has streamed in, so an owner already at a limit is turned away here
        # first. SessionCsrfMiddleware sets the owner in production only; the
        # route still re-checks authoritatively.
        owner_id = _authenticated_owner_id(scope)
        if (
            self.demo_upload_precheck is not None
            and scope.get("path") == "/uploads/demo"
            and owner_id is not None
        ):
            try:
                exceeded = await run_in_threadpool(self.demo_upload_precheck, owner_id)
            except Exception as exc:
                logger.warning(
                    "Upload quota precheck failed (%s); rejecting the upload",
                    type(exc).__name__,
                )
                await _send_upload_quota_unavailable(scope, receive, send)
                return
            if exceeded is not None:
                await exceeded.to_response()(scope, receive, send)
                return

        # A part PUT holds one part in memory, never a whole file: it takes the
        # part pool, not the intake slot, so parts keep flowing while one
        # `complete` (or a legacy upload) runs its intake.
        slot: IntakeSlot | PartPool
        if is_upload_part_request(scope.get("method", ""), scope.get("path", "")):
            slot = self.part_pool
            retry_after = PART_BUSY_RETRY_AFTER_SECONDS
        else:
            slot = self.intake_slot
            retry_after = INTAKE_BUSY_RETRY_AFTER_SECONDS
        if not slot.try_acquire():
            await _send_upload_busy(scope, receive, send, retry_after_seconds=retry_after)
            return

        received_bytes = 0

        async def limited_receive() -> Message:
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
            slot.release()

    def _limit_for_scope(self, scope: Scope) -> int | None:
        if scope.get("type") != "http":
            return None
        method = scope.get("method", "").upper()
        path = scope.get("path", "")
        if is_upload_part_request(method, path):
            return self.upload_part_envelope_limit_bytes
        if method != "POST":
            return None
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
    def _is_manual_video_upload_scope(scope: Scope) -> bool:
        return (
            scope.get("type") == "http"
            and scope.get("method", "").upper() == "POST"
            and _VIDEO_UPLOAD_PATH.fullmatch(scope.get("path", "")) is not None
        )

    @staticmethod
    def _is_worker_post_scope(scope: Scope) -> bool:
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
        app: ASGIApp,
        *,
        steam_credentials_limit_bytes: int = STEAM_CREDENTIALS_JSON_LIMIT_BYTES,
    ) -> None:
        if steam_credentials_limit_bytes <= 0:
            raise ValueError("steam_credentials_limit_bytes must be positive")
        self.app = app
        self.steam_credentials_limit_bytes = steam_credentials_limit_bytes

    async def __call__(
        self,
        scope: Scope,
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

        async def limited_receive() -> Message:
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


def _authenticated_owner_id(scope: Scope) -> str | None:
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    owner_id = state.get("authenticated_owner_id")
    return owner_id if isinstance(owner_id, str) and owner_id else None


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
    scope: Scope,
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
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=401,
        content={"detail": "Invalid render worker token"},
        headers={"Cache-Control": "private, no-store"},
    )
    await response(scope, receive, send)


async def _send_not_found(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=404,
        content={"detail": "Not found"},
        headers={"Cache-Control": "private, no-store"},
    )
    await response(scope, receive, send)


async def _send_sensitive_request_too_large(
    scope: Scope,
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


async def _send_upload_quota_unavailable(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "upload_quota_unavailable",
                "message": "Upload quota could not be checked. Try again shortly.",
                "retryAfterSeconds": 30,
            }
        },
        headers={"Cache-Control": "private, no-store", "Retry-After": "30"},
    )
    await response(scope, receive, send)


async def _send_upload_busy(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    retry_after_seconds: int = INTAKE_BUSY_RETRY_AFTER_SECONDS,
) -> None:
    response = JSONResponse(
        status_code=503,
        content={
            "detail": "Artifact upload capacity is busy",
            "errorCode": "INTAKE_BUSY",
        },
        headers={"Cache-Control": "private, no-store", "Retry-After": str(retry_after_seconds)},
    )
    await response(scope, receive, send)
