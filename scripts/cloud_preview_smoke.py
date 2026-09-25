#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
OWNER_ID = os.getenv("DEV_USER_ID", "cloud-preview-smoke")
AUTH_SESSION_COOKIE = os.getenv("AUTH_SESSION_COOKIE", "").strip()
SAMPLE_DEMO_PATH = os.getenv("SAMPLE_DEMO_PATH")
SAMPLE_DEMO_NAME = os.getenv("SAMPLE_DEMO_NAME")
UPLOAD_CHUNK_BYTES = 1024 * 1024


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    require_sample = require_sample_enabled(args, os.environ)
    sample_path = resolve_sample_demo_path(SAMPLE_DEMO_PATH, require_sample=require_sample)

    check_health()
    check_frontend()
    capabilities = fetch_capabilities()

    if capabilities["devTools"]:
        demo = request_json("POST", "/uploads/mock")
        demo_id = required_str(demo, "id")
        print(f"created mock demo {demo_id}")

        status = wait_for_completed_demo(demo_id)
        print(f"mock demo completed: {status.get('map_name')} / {status.get('round_count')} rounds")
        replay = request_json("GET", f"/demos/{demo_id}/replay")
    else:
        # Production hides the mock upload, so the real sample is the smoke demo.
        if sample_path is None:
            raise SmokeFailure(
                "SAMPLE_DEMO_PATH is required when /auth/me reports devTools=false: "
                "production has no mock upload, so the smoke needs a real sample demo"
            )
        print("mock upload skipped: /auth/me reports devTools=false; using the sample demo")
        demo_id, replay = upload_sample_and_wait(sample_path)

    coaching = request_json("GET", f"/demos/{demo_id}/coaching")
    print(f"loaded replay and {len(coaching) if isinstance(coaching, list) else 0} coaching events")

    video_url = video_url_from_payload(replay)
    if capabilities["renderClips"]:
        render_request = render_clip_request(replay, coaching if isinstance(coaching, list) else [])
        render_response = request_json("POST", f"/demos/{demo_id}/render/clip", render_request)
        job_id = required_str(render_response, "job_id")
        jobs = request_json("GET", f"/demos/{demo_id}/render/jobs")
        if not isinstance(jobs, list) or not any(job.get("job_id") == job_id for job in jobs):
            raise SmokeFailure("render job list did not include the created render_clip job")
        print(f"created render_clip job {job_id}")
        video_url = video_url_from_payload(render_response) or video_url
    else:
        print("render_clip skipped: /auth/me reports renderClips=false")

    check_media_route(video_url)

    if capabilities["devTools"]:
        if sample_path:
            upload_sample_and_wait(sample_path)
        else:
            print(
                "sample demo upload skipped; set SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem "
                "or pass --require-sample for stricter validation"
            )

    print(failure_diagnostics_summary())
    print("cloud preview smoke passed")
    return 0


class SmokeFailure(RuntimeError):
    pass


def fetch_capabilities() -> dict[str, bool]:
    try:
        payload = request_json("GET", "/auth/me")
    except SmokeFailure as exc:
        if AUTH_SESSION_COOKIE:
            raise
        raise SmokeFailure(
            f"{exc}; a production preview needs AUTH_SESSION_COOKIE set to a signed-in "
            "__Host-cs2_session value"
        ) from exc
    capabilities = capabilities_from_auth_me(payload)
    print(
        f"capabilities: devTools={str(capabilities['devTools']).lower()} "
        f"renderClips={str(capabilities['renderClips']).lower()}"
    )
    return capabilities


def capabilities_from_auth_me(payload: Any) -> dict[str, bool]:
    # Missing or malformed capabilities count as off, like the frontend does.
    raw = payload.get("capabilities") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    return {
        "devTools": raw.get("devTools") is True,
        "renderClips": raw.get("renderClips") is True,
    }


def require_sample_enabled(argv: Sequence[str], env: Mapping[str, str]) -> bool:
    return (
        "--require-sample" in argv
        or _truthy(env.get("REQUIRE_SAMPLE_DEMO"))
        or _truthy(env.get("SAMPLE_DEMO_REQUIRED"))
    )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_sample_demo_path(path_value: str | None, *, require_sample: bool) -> Path | None:
    if not path_value:
        if require_sample:
            raise SmokeFailure("SAMPLE_DEMO_PATH is required when sample validation is required")
        return None

    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        raise SmokeFailure(f"SAMPLE_DEMO_PATH does not exist: {path}")
    if not path.is_file():
        raise SmokeFailure(f"SAMPLE_DEMO_PATH is not a file: {path}")
    return path


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = auth_headers(method)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body.decode("utf-8")) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc


def check_health() -> None:
    payload = request_json("GET", "/health")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise SmokeFailure(f"health was not ok: {payload}")
    print("health ok")


def check_frontend() -> None:
    try:
        with urllib.request.urlopen(f"{FRONTEND_URL}/dashboard", timeout=30) as response:
            body = response.read(256).decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"frontend dashboard failed: {exc.reason}") from exc
    if response.status >= 400 or "<" not in body:
        raise SmokeFailure(f"frontend dashboard returned an unexpected response: HTTP {response.status}")
    print(f"frontend reachable: {FRONTEND_URL}/dashboard")


def failure_diagnostics_summary() -> str:
    try:
        payload = request_json("GET", "/diagnostics")
    except SmokeFailure as exc:
        return f"diagnostics unavailable: {exc}"
    return diagnostics_summary(payload)


def diagnostics_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "diagnostics unavailable: endpoint returned a non-object response"

    dependencies = payload.get("dependencies") if isinstance(payload.get("dependencies"), dict) else {}
    worker = payload.get("worker") if isinstance(payload.get("worker"), dict) else {}
    heartbeat = worker.get("heartbeat") if isinstance(worker.get("heartbeat"), dict) else {}
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), dict) else {}
    failures = jobs.get("recentFailures") if isinstance(jobs.get("recentFailures"), list) else []

    queue_name = worker.get("queueName") or "unknown"
    queue_length = worker.get("queueLength")
    heartbeat_label = _heartbeat_label(heartbeat)

    return (
        f"diagnostics: status={payload.get('status') or 'unknown'} "
        f"db={_dependency_label(dependencies, 'database')} "
        f"redis={_dependency_label(dependencies, 'redis')} "
        f"storage={_dependency_label(dependencies, 'storage')} "
        f"workerDeps={_dependency_label(dependencies, 'worker')} "
        f"queue={queue_name} "
        f"queueLength={queue_length if queue_length is not None else 'unknown'} "
        f"heartbeat={heartbeat_label} "
        f"recentFailures={len(failures)}"
    )


def _dependency_label(dependencies: dict[str, Any], key: str) -> str:
    value = dependencies.get(key)
    if not isinstance(value, dict):
        return "unknown"
    return "ok" if value.get("ok") is True else "fail"


def _heartbeat_label(heartbeat: dict[str, Any]) -> str:
    if heartbeat.get("alive") is True:
        return "alive"
    if heartbeat.get("lastSeenAt") or heartbeat.get("ageSeconds") is not None:
        return "stale"
    return "missing"


def wait_for_completed_demo(demo_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        status = request_json("GET", f"/demos/{demo_id}/status")
        if isinstance(status, dict):
            last_status = status
            if status.get("status") == "completed":
                return status
            if status.get("status") == "failed":
                raise SmokeFailure(f"demo {demo_id} failed: {status.get('error_message')}")
        time.sleep(2)
    raise SmokeFailure(f"demo {demo_id} did not complete before timeout: {last_status}")


def rename_demo(demo_id: str, name: str) -> dict[str, Any]:
    normalized = name.strip()
    if normalized:
        payload = request_json("PATCH", f"/demos/{demo_id}", {"name": normalized})
        if isinstance(payload, dict):
            return payload
    status = request_json("GET", f"/demos/{demo_id}/status")
    if isinstance(status, dict):
        return status
    raise SmokeFailure(f"demo {demo_id} status response was not an object: {status}")


def sample_completion_message(
    demo_id: str,
    status: dict[str, Any],
    replay: dict[str, Any] | None = None,
) -> str:
    name = status.get("name") or "sample demo"
    map_name = status.get("map_name") or "unknown"
    round_count = status.get("round_count") or 0
    coaching_count = status.get("coaching_event_count") or 0
    calibration = sample_calibration_label(replay)
    return (
        f"sample demo completed: {demo_id} / {name} / {map_name} / "
        f"{round_count} rounds / {coaching_count} coaching events / {calibration}"
    )


def sample_calibration_label(replay: dict[str, Any] | None) -> str:
    metadata = replay.get("mapMetadata") if isinstance(replay, dict) else None
    if not isinstance(metadata, dict):
        return "map calibration unknown"

    display_name = metadata.get("displayName") or metadata.get("mapName") or "unknown map"
    confidence = metadata.get("confidence") or "unknown"
    calibrated = metadata.get("calibrated")
    calibration_state = "calibrated" if calibrated is True else "uncalibrated"
    return f"{display_name} {confidence} {calibration_state}"


def render_clip_request(replay: dict[str, Any], coaching: list[Any]) -> dict[str, Any]:
    tick_rate = int(replay.get("tickRate") or (replay.get("video") or {}).get("tickRate") or 64)
    rounds = replay.get("rounds") if isinstance(replay.get("rounds"), list) else []
    first_round = rounds[0] if rounds else {}
    start_tick = int(first_round.get("startTick") or 0)
    end_tick = int(first_round.get("endTick") or (start_tick + tick_rate * 10))
    event = coaching[0] if coaching and isinstance(coaching[0], dict) else {}
    event_tick = int(event.get("tick_start") or start_tick)
    tick_start = max(start_tick, event_tick - tick_rate * 5)
    tick_end = min(end_tick, max(tick_start + tick_rate, event_tick + tick_rate * 5))
    if tick_end <= tick_start:
        tick_end = tick_start + tick_rate
    return {
        "eventId": event.get("id"),
        "tickStart": tick_start,
        "tickEnd": tick_end,
        "tickRate": tick_rate,
        "roundNumber": first_round.get("roundNumber"),
        "renderPreset": "cloud_preview_smoke_v1",
    }


def video_url_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    video = payload.get("video")
    if isinstance(video, dict) and isinstance(video.get("url"), str):
        return video["url"]
    return None


def check_media_route(video_url: str | None) -> None:
    if video_url:
        url = video_url if video_url.startswith(("http://", "https://")) else f"{API_BASE_URL}{video_url}"
        expected = {200, 206}
    else:
        url = f"{API_BASE_URL}/demos/__cloud_preview_smoke_missing__/media/video"
        expected = {404}
    request = urllib.request.Request(url, headers=auth_headers("GET"), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if status not in expected:
        raise SmokeFailure(f"media route returned HTTP {status}, expected {sorted(expected)}")
    print(f"media route returned expected HTTP {status}")


def upload_sample_and_wait(sample_path: Path) -> tuple[str, Any]:
    print(f"uploading sample demo: {sample_path.name}")
    sample_demo_id = upload_sample_demo(sample_path)
    status = wait_for_completed_demo(sample_demo_id, timeout_seconds=120)
    if SAMPLE_DEMO_NAME:
        status = rename_demo(sample_demo_id, SAMPLE_DEMO_NAME)
    sample_replay = request_json("GET", f"/demos/{sample_demo_id}/replay")
    print(sample_completion_message(sample_demo_id, status, sample_replay))
    return sample_demo_id, sample_replay


def upload_sample_demo(path: Path) -> str:
    path = resolve_sample_demo_path(str(path), require_sample=True)
    if path is None:
        raise SmokeFailure("SAMPLE_DEMO_PATH is required")
    boundary = f"----cloud-preview-smoke-{uuid.uuid4().hex}"
    preamble = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
        ]
    )
    closing = f"\r\n--{boundary}--\r\n".encode()

    try:
        response_status, response_body = post_multipart_file(
            "/uploads/demo",
            path,
            boundary=boundary,
            preamble=preamble,
            closing=closing,
        )
    except OSError as exc:
        raise SmokeFailure(f"sample upload failed: {exc}") from exc

    if response_status >= 400:
        raise SmokeFailure(f"sample upload failed with HTTP {response_status}: {response_body}")
    payload = json.loads(response_body)
    return required_str(payload, "id")


def post_multipart_file(
    path: str,
    file_path: Path,
    *,
    boundary: str,
    preamble: bytes,
    closing: bytes,
) -> tuple[int, str]:
    parsed = urlsplit(API_BASE_URL)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise SmokeFailure(f"API_BASE_URL must be http or https: {API_BASE_URL}")

    base_path = parsed.path.rstrip("/")
    request_path = f"{base_path}{path}"
    connection_class = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=120)
    content_length = len(preamble) + file_path.stat().st_size + len(closing)
    try:
        connection.putrequest("POST", request_path)
        for header_name, header_value in auth_headers("POST").items():
            connection.putheader(header_name, header_value)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        connection.send(preamble)
        with file_path.open("rb") as handle:
            while chunk := handle.read(UPLOAD_CHUNK_BYTES):
                connection.send(chunk)
        connection.send(closing)
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body
    except HTTPException as exc:
        raise SmokeFailure(f"sample upload failed: {exc}") from exc
    finally:
        connection.close()


def required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SmokeFailure(f"response missing {key}: {payload}")
    return value


def auth_headers(method: str) -> dict[str, str]:
    if AUTH_SESSION_COOKIE:
        headers = {"Cookie": f"__Host-cs2_session={AUTH_SESSION_COOKIE}"}
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers["Origin"] = frontend_origin()
        return headers
    return {"X-Dev-User-Id": OWNER_ID}


def frontend_origin() -> str:
    parsed = urlsplit(FRONTEND_URL)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise SmokeFailure(f"FRONTEND_URL must be http or https: {FRONTEND_URL}")
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SmokeFailure as exc:
        print(f"cloud preview smoke failed: {exc}", file=sys.stderr)
        print(failure_diagnostics_summary(), file=sys.stderr)
        raise SystemExit(1) from exc
