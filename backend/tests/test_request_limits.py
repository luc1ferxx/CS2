import asyncio
import json
import unittest
from collections.abc import Iterable
from typing import Any

from app.core.request_limits import (
    DEMO_ENVELOPE_LIMIT_BYTES,
    STEAM_CREDENTIALS_JSON_LIMIT_BYTES,
    UPLOAD_PART_ENVELOPE_LIMIT_BYTES,
    VIDEO_ENVELOPE_LIMIT_BYTES,
    WORKER_MEDIA_ENVELOPE_LIMIT_BYTES,
    WORKER_RESULT_ENVELOPE_LIMIT_BYTES,
    MultipartRequestLimitMiddleware,
    SensitiveJsonRequestLimitMiddleware,
)
from app.core.upload_slots import IntakeSlot, PartPool
from app.services.upload_quota import UploadQuotaExceeded

PART_PATH = "/uploads/sessions/" + "0123456789abcdef" * 2 + "/parts/7"


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
    state: dict[str, Any] | None = None,
    method: str = "POST",
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
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    if state is not None:
        scope["state"] = state
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
        self.assertTrue(app.intake_slot.try_acquire())

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
        self.assertEqual(app.intake_slot.in_use, 1)

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

    def test_demo_upload_precheck_rejects_an_owner_at_a_quota_before_the_body(self) -> None:
        downstream = RecordingBodyApp()
        calls: list[str] = []

        def precheck(owner_id: str) -> UploadQuotaExceeded:
            calls.append(owner_id)
            return UploadQuotaExceeded(429, "upload_daily_limit", "Daily upload limit reached.", 1234)

        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=64,
            demo_upload_precheck=precheck,
        )
        # A quota rejection must not queue behind (or take) the upload slot.
        self.assertTrue(app.intake_slot.try_acquire())

        status, headers, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"must-not-be-read"],
            headers=[(b"content-length", b"16")],
            state={"authenticated_owner_id": "owner_v1_at_limit"},
        )

        self.assertEqual(status, 429)
        self.assertEqual(
            json.loads(body),
            {
                "detail": {
                    "code": "upload_daily_limit",
                    "message": "Daily upload limit reached.",
                    "retryAfterSeconds": 1234,
                }
            },
        )
        self.assertEqual(headers["retry-after"], "1234")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(calls, ["owner_v1_at_limit"])
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)
        self.assertEqual(app.intake_slot.in_use, 1)

    def test_demo_upload_precheck_that_raises_fails_closed_before_the_body(self) -> None:
        downstream = RecordingBodyApp()

        def precheck(_owner_id: str) -> None:
            raise ConnectionError("database unavailable")

        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=64,
            demo_upload_precheck=precheck,
        )

        with self.assertLogs("app.core.request_limits", level="WARNING") as logs:
            status, headers, body, receive_calls = invoke_asgi(
                app,
                path="/uploads/demo",
                chunks=[b"must-not-be-read"],
                state={"authenticated_owner_id": "owner_v1_secret_owner"},
            )

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["detail"]["code"], "upload_quota_unavailable")
        self.assertEqual(json.loads(body)["detail"]["retryAfterSeconds"], 30)
        self.assertEqual(headers["retry-after"], "30")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)
        self.assertEqual(app.intake_slot.in_use, 0)
        logged = " ".join(logs.output)
        self.assertNotIn("owner_v1_secret_owner", logged)
        self.assertNotIn("database unavailable", logged)

    def test_demo_upload_precheck_passes_allowed_owners_through_to_the_stream(self) -> None:
        downstream = RecordingBodyApp()
        calls: list[str] = []

        def precheck(owner_id: str) -> None:
            calls.append(owner_id)

        app = MultipartRequestLimitMiddleware(
            downstream,
            demo_envelope_limit_bytes=8,
            demo_upload_precheck=precheck,
        )

        status, _, body, receive_calls = invoke_asgi(
            app,
            path="/uploads/demo",
            chunks=[b"1234"],
            state={"authenticated_owner_id": "owner_v1_allowed"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": 4})
        self.assertEqual(calls, ["owner_v1_allowed"])
        self.assertEqual(receive_calls, 1)
        self.assertEqual(app.intake_slot.in_use, 0)

    def test_demo_upload_precheck_is_skipped_without_owner_other_paths_or_oversize(self) -> None:
        for path, headers, state, expected_status in (
            ("/uploads/demo", [], None, 200),
            ("/uploads/demo", [], {}, 200),
            ("/uploads/demo", [(b"content-length", b"9")], {"authenticated_owner_id": "o"}, 413),
            ("/demos/demo-123/video/upload", [], {"authenticated_owner_id": "o"}, 200),
            ("/uploads/mock", [], {"authenticated_owner_id": "o"}, 200),
        ):
            with self.subTest(path=path, headers=headers, state=state):
                downstream = RecordingBodyApp()
                calls: list[str] = []

                def precheck(owner_id: str, calls: list[str] = calls) -> UploadQuotaExceeded:
                    calls.append(owner_id)
                    return UploadQuotaExceeded(429, "active_parse_limit", "Busy.", 60)

                app = MultipartRequestLimitMiddleware(
                    downstream,
                    demo_envelope_limit_bytes=8,
                    video_envelope_limit_bytes=8,
                    demo_upload_precheck=precheck,
                )

                status, _, _, _ = invoke_asgi(
                    app,
                    path=path,
                    chunks=[b"1234"],
                    headers=headers,
                    state=state,
                )

                self.assertEqual(status, expected_status)
                self.assertEqual(calls, [])

    def test_upload_part_put_has_its_own_envelope_checked_before_and_while_reading(self) -> None:
        self.assertEqual(UPLOAD_PART_ENVELOPE_LIMIT_BYTES, (8 * 1024 * 1024) + (64 * 1024))
        for headers, chunks, expected_status, expected_receives in (
            ([(b"content-length", b"9")], [b"must-not-be-read"], 413, 0),
            ([(b"content-length", b"4")], [b"1234", b"56789"], 413, 2),
            ([], [b"12345678"], 200, 1),
        ):
            with self.subTest(headers=headers, chunks=chunks):
                downstream = RecordingBodyApp()
                app = MultipartRequestLimitMiddleware(downstream, upload_part_envelope_limit_bytes=8)

                status, response_headers, body, receive_calls = invoke_asgi(
                    app,
                    path=PART_PATH,
                    method="PUT",
                    chunks=chunks,
                    headers=headers,
                )

                self.assertEqual(status, expected_status)
                self.assertEqual(receive_calls, expected_receives)
                if expected_status == 413:
                    self.assertEqual(json.loads(body)["errorCode"], "INTAKE_TOO_LARGE")
                    self.assertEqual(response_headers["cache-control"], "private, no-store")
                self.assertEqual(app.part_pool.in_use, 0)
                self.assertEqual(app.intake_slot.in_use, 0)

    def test_only_an_exact_part_put_gets_the_part_envelope(self) -> None:
        for method, path in (
            ("POST", PART_PATH),
            ("PUT", PART_PATH + "/"),
            ("PUT", PART_PATH.upper()),
            ("PUT", "/uploads/sessions/" + "a" * 32 + "/parts/123456"),
            ("PUT", "/uploads/sessions/" + "a" * 31 + "/parts/1"),
            ("PATCH", PART_PATH),
        ):
            with self.subTest(method=method, path=path):
                downstream = RecordingBodyApp()
                app = MultipartRequestLimitMiddleware(downstream, upload_part_envelope_limit_bytes=2)

                status, _, body, _ = invoke_asgi(app, path=path, method=method, chunks=[b"123456"])

                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"received": 6})

    def test_a_full_part_pool_answers_busy_before_the_body_and_ignores_the_intake_slot(self) -> None:
        downstream = RecordingBodyApp()
        pool = PartPool(2)
        slot = IntakeSlot()
        app = MultipartRequestLimitMiddleware(downstream, part_pool=pool, intake_slot=slot)
        self.assertTrue(pool.try_acquire())
        self.assertTrue(pool.try_acquire())

        status, headers, body, receive_calls = invoke_asgi(
            app, path=PART_PATH, method="PUT", chunks=[b"must-not-be-read"]
        )

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["errorCode"], "INTAKE_BUSY")
        self.assertEqual(headers["retry-after"], "2")
        self.assertEqual(headers["cache-control"], "private, no-store")
        self.assertEqual(receive_calls, 0)
        self.assertFalse(downstream.called)
        self.assertEqual(pool.in_use, 2)

        # A busy intake slot (a 1 GiB intake running) does not stop parts.
        pool.release()
        self.assertTrue(slot.try_acquire())
        status, _, body, _ = invoke_asgi(app, path=PART_PATH, method="PUT", chunks=[b"1234"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": 4})
        self.assertEqual((pool.in_use, slot.in_use), (1, 1))

    def test_whole_file_uploads_share_the_intake_slot_the_app_passes_in(self) -> None:
        slot = IntakeSlot()
        app = MultipartRequestLimitMiddleware(
            RecordingBodyApp(), intake_slot=slot, render_worker_token="expected-token"
        )
        self.assertTrue(slot.try_acquire())
        for path, headers in (
            ("/uploads/demo", []),
            ("/demos/demo-123/video/upload", []),
            ("/render-worker/jobs/job-123/media", [(b"x-render-worker-token", b"expected-token")]),
        ):
            with self.subTest(path=path):
                status, response_headers, body, receive_calls = invoke_asgi(
                    app, path=path, chunks=[b"1234"], headers=headers
                )
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body)["errorCode"], "INTAKE_BUSY")
                self.assertEqual(response_headers["retry-after"], "5")
                self.assertEqual(receive_calls, 0)
        slot.release()
        status, _, _, _ = invoke_asgi(app, path="/uploads/demo", chunks=[b"1234"])
        self.assertEqual(status, 200)
        self.assertEqual(slot.in_use, 0)

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
