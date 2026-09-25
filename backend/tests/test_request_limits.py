import asyncio
import json
import unittest
from collections.abc import Iterable
from typing import Any

from app.core.request_limits import (
    DEMO_ENVELOPE_LIMIT_BYTES,
    STEAM_CREDENTIALS_JSON_LIMIT_BYTES,
    VIDEO_ENVELOPE_LIMIT_BYTES,
    WORKER_MEDIA_ENVELOPE_LIMIT_BYTES,
    WORKER_RESULT_ENVELOPE_LIMIT_BYTES,
    MultipartRequestLimitMiddleware,
    SensitiveJsonRequestLimitMiddleware,
)


class RecordingBodyApp:
    def __init__(self) -> None:
        self.called = False
        self.received_body = bytearray()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        self.called = True
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            self.received_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        payload = json.dumps({"received": len(self.received_body)}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def invoke_asgi(
    app: Any,
    *,
    path: str,
    chunks: Iterable[bytes] = (),
    headers: Iterable[tuple[bytes, bytes]] = (),
) -> tuple[int, dict[str, str], bytes, int]:
    materialized_chunks = list(chunks)
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(materialized_chunks) - 1,
        }
        for index, chunk in enumerate(materialized_chunks)
    ]
    if not messages:
        messages.append({"type": "http.request", "body": b"", "more_body": False})
    receive_calls = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))

    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        name.decode().lower(): value.decode() for name, value in start["headers"]
    }
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, body, receive_calls


class MultipartRequestLimitMiddlewareTest(unittest.TestCase):
    def test_default_envelope_limits_include_bounded_multipart_overhead(self) -> None:
        self.assertEqual(DEMO_ENVELOPE_LIMIT_BYTES, (1024**3) + (8 * 1024**2))
        self.assertEqual(VIDEO_ENVELOPE_LIMIT_BYTES, (2 * 1024**3) + (8 * 1024**2))
        self.assertEqual(WORKER_MEDIA_ENVELOPE_LIMIT_BYTES, VIDEO_ENVELOPE_LIMIT_BYTES)
        self.assertEqual(WORKER_RESULT_ENVELOPE_LIMIT_BYTES, 64 * 1024)
        self.assertEqual(STEAM_CREDENTIALS_JSON_LIMIT_BYTES, 4 * 1024)

    def test_steam_credentials_json_has_a_small_streaming_limit(self) -> None:
        downstream = RecordingBodyApp()
        app = SensitiveJsonRequestLimitMiddleware(
            downstream,
            steam_credentials_limit_bytes=8,
        )

        status, headers, body, receive_calls = invoke_asgi(
            app,
            path="/steam/connection/credentials",
            chunks=[b"1234", b"56789"],
            headers=[(b"content-length", b"4")],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "REQUEST_TOO_LARGE")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(receive_calls, 2)
        self.assertEqual(downstream.received_body, b"1234")

    def test_steam_credentials_limit_does_not_cover_other_json_routes(self) -> None:
        downstream = RecordingBodyApp()
        app = SensitiveJsonRequestLimitMiddleware(
            downstream,
            steam_credentials_limit_bytes=8,
        )

        status, _, body, _ = invoke_asgi(
            app,
            path="/steam/sync",
            chunks=[b"123456789"],
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": 9})

    def test_rejects_trustworthy_oversized_content_length_before_receiving_body(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, headers, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"not-read"],
            headers=[(b"content-length", b"9")],
        )

        self.assertEqual(status, 413)
        self.assertEqual(
            json.loads(body),
            {
                "detail": "Request body too large",
                "errorCode": "INTAKE_TOO_LARGE",
            },
        )
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(headers["vary"], "Cookie, Origin")
        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)

    def test_rejects_oversized_numeric_content_length_without_unbounded_integer_parse(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"not-read"],
            headers=[(b"content-length", b"9" * 10_000)],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)

    def test_rejects_actual_multichunk_body_when_content_length_is_underreported(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, headers, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"1234", b"56789"],
            headers=[(b"content-length", b"4")],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(receive_calls, 2)
        self.assertEqual(downstream.received_body, b"1234")

    def test_enforces_video_envelope_limit_on_user_and_worker_media_routes(self) -> None:
        for path in (
            "/demos/demo-123/video/upload",
            "/render-worker/jobs/job-123/media",
        ):
            with self.subTest(path=path):
                downstream = RecordingBodyApp()
                app = MultipartRequestLimitMiddleware(
                    downstream,
                    demo_envelope_limit_bytes=8,
                    video_envelope_limit_bytes=16,
                    worker_media_envelope_limit_bytes=16,
                )

                status, _, body, receive_calls = invoke_asgi(
                    app,
                    path=path,
                    chunks=[b"not-read"],
                    headers=[(b"content-length", b"17")],
                )

                self.assertEqual(status, 413)
                self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
                self.assertEqual(receive_calls, 0)
                self.assertFalse(downstream.called)

    def test_rejects_invalid_worker_token_before_receiving_media_body(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
            render_worker_token="expected-token",
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/render-worker/jobs/job-123/media",
            chunks=[b"must-not-be-read"],
            headers=[(b"x-render-worker-token", b"wrong-token")],
        )

        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"detail": "Invalid render worker token"})
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)

    def test_valid_worker_token_keeps_streaming_limit_active(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
            render_worker_token="expected-token",
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/render-worker/jobs/job-123/media",
            chunks=[b"12345678", b"123456789"],
            headers=[(b"x-render-worker-token", b"expected-token")],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
        self.assertEqual(receive_calls, 2)

    def test_worker_result_rejects_bad_token_before_reading_json(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            render_worker_token="expected-token",
            worker_result_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/render-worker/jobs/job-123/result",
            chunks=[b'{"secret":"must-not-be-read"}'],
        )

        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"detail": "Invalid render worker token"})
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)

    def test_worker_result_json_has_small_actual_byte_envelope(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            render_worker_token="expected-token",
            worker_result_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/render-worker/jobs/job-123/result",
            chunks=[b"12345678", b"123456789"],
            headers=[(b"x-render-worker-token", b"expected-token")],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
        self.assertEqual(receive_calls, 2)

    def test_rejects_actual_body_over_limit_when_content_length_is_missing(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"123", b"456", b"789"],
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
        self.assertEqual(receive_calls, 3)
        self.assertEqual(downstream.received_body, b"123456")

    def test_streams_request_at_exact_limit_to_downstream_without_buffering(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"123", b"45678"],
            headers=[(b"content-length", b"8")],
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": 8})
        self.assertEqual(receive_calls, 2)
        self.assertEqual(downstream.received_body, b"12345678")

    def test_disabled_manual_video_upload_is_not_found_before_body_or_upload_slot(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            video_envelope_limit_bytes=16,
            max_concurrent_uploads=1,
            manual_video_upload_enabled=False,
        )
        # A demo upload in flight holds the only slot: the 404 must neither
        # wait for it (503 INTAKE_BUSY) nor claim it.
        app._active_uploads = 1

        status, headers, body, receive_calls = invoke_asgi(
            app,
            path="/demos/demo-123/video/upload",
            chunks=[b"must-not-be-read"],
            headers=[(b"content-length", b"999")],
        )

        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"detail": "Not found"})
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)
        self.assertEqual(app._active_uploads, 1)

    def test_disabled_manual_video_upload_leaves_other_upload_routes_streaming(self) -> None:
        for path, headers in (
            ("/uploads/demo", []),
            (
                "/render-worker/jobs/job-123/media",
                [(b"x-render-worker-token", b"expected-token")],
            ),
            ("/demos/demo-123/video", []),
        ):
            with self.subTest(path=path):
                downstream = RecordingBodyApp()
                app = MultipartRequestLimitMiddleware(
                    downstream,
                    demo_envelope_limit_bytes=8,
                    worker_media_envelope_limit_bytes=16,
                    render_worker_token="expected-token",
                    manual_video_upload_enabled=False,
                )

                status, _, body, _ = invoke_asgi(
                    app,
                    path=path,
                    chunks=[b"1234"],
                    headers=headers,
                )

                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"received": 4})

    def test_non_target_route_passes_through_without_request_envelope_limit(self) -> None:
        downstream = RecordingBodyApp()
        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            video_envelope_limit_bytes=16,
            worker_media_envelope_limit_bytes=16,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/mock",
            chunks=[b"123456789"],
            headers=[(b"content-length", b"999")],
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": 9})
        self.assertEqual(receive_calls, 1)
        self.assertEqual(downstream.received_body, b"123456789")


if __name__ == "__main__":
    unittest.main()
