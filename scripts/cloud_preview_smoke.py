#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
OWNER_ID = os.getenv("DEV_USER_ID", "cloud-preview-smoke")
SAMPLE_DEMO_PATH = os.getenv("SAMPLE_DEMO_PATH")


def main() -> int:
    check_health()
    check_frontend()

    demo = request_json("POST", "/uploads/mock")
    demo_id = required_str(demo, "id")
    print(f"created mock demo {demo_id}")

    status = wait_for_completed_demo(demo_id)
    print(f"mock demo completed: {status.get('map_name')} / {status.get('round_count')} rounds")

    replay = request_json("GET", f"/demos/{demo_id}/replay")
    coaching = request_json("GET", f"/demos/{demo_id}/coaching")
    print(f"loaded replay and {len(coaching) if isinstance(coaching, list) else 0} coaching events")

    render_request = render_clip_request(replay, coaching if isinstance(coaching, list) else [])
    render_response = request_json("POST", f"/demos/{demo_id}/render/clip", render_request)
    job_id = required_str(render_response, "job_id")
    jobs = request_json("GET", f"/demos/{demo_id}/render/jobs")
    if not isinstance(jobs, list) or not any(job.get("job_id") == job_id for job in jobs):
        raise SmokeFailure("render job list did not include the created render_clip job")
    print(f"created render_clip job {job_id}")

    video_url = video_url_from_payload(render_response) or video_url_from_payload(replay)
    check_media_route(video_url)

    if SAMPLE_DEMO_PATH:
        sample_demo_id = upload_sample_demo(Path(SAMPLE_DEMO_PATH))
        wait_for_completed_demo(sample_demo_id, timeout_seconds=90)
        print(f"sample demo completed: {sample_demo_id}")
    else:
        print("sample demo upload skipped; set SAMPLE_DEMO_PATH to include it")

    print("cloud preview smoke passed")
    return 0


class SmokeFailure(RuntimeError):
    pass


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"X-Dev-User-Id": OWNER_ID}
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
    public_urls = payload.get("publicUrls") or {}
    print(
        "health ok: "
        f"backend={public_urls.get('backendPublicUrl')} "
        f"media={public_urls.get('effectiveMediaUrlBase')}"
    )


def check_frontend() -> None:
    try:
        with urllib.request.urlopen(f"{FRONTEND_URL}/dashboard", timeout=30) as response:
            body = response.read(256).decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"frontend dashboard failed: {exc.reason}") from exc
    if response.status >= 400 or "<" not in body:
        raise SmokeFailure(f"frontend dashboard returned an unexpected response: HTTP {response.status}")
    print(f"frontend reachable: {FRONTEND_URL}/dashboard")


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
        url = f"{API_BASE_URL}/media/videos/__cloud_preview_smoke_missing__.mp4"
        expected = {404}
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if status not in expected:
        raise SmokeFailure(f"media route returned HTTP {status}, expected {sorted(expected)}")
    print(f"media route returned expected HTTP {status}")


def upload_sample_demo(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise SmokeFailure(f"SAMPLE_DEMO_PATH does not exist: {path}")
    boundary = f"----cloud-preview-smoke-{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        f"{API_BASE_URL}/uploads/demo",
        data=body,
        headers={
            "X-Dev-User-Id": OWNER_ID,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"sample upload failed with HTTP {exc.code}: {detail}") from exc
    return required_str(payload, "id")


def required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SmokeFailure(f"response missing {key}: {payload}")
    return value


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"cloud preview smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
