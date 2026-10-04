#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
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
# Chunked upload session (the browser's path): attempts per part, the poll
# interval while `complete` answers 202, and how long complete may take.
PART_ATTEMPTS = 5
UPLOAD_SESSION_POLL_SECONDS = 2
UPLOAD_COMPLETE_TIMEOUT_SECONDS = 600


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
        render_request = render_clip_request(
            replay,
            coaching if isinstance(coaching, list) else [],
            require_pov=not capabilities["devTools"],
        )
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


class HttpStatusFailure(SmokeFailure):
    """An HTTP error response, keeping the status so a caller can explain it."""

    def __init__(self, message: str, *, status: int, detail: str) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


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
        raise HttpStatusFailure(
            f"{method} {path} failed with HTTP {exc.code}: {detail}", status=exc.code, detail=detail
        ) from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc


def check_health() -> None:
    try:
        payload = request_json("GET", "/health")
    except HttpStatusFailure as exc:
        raise SmokeFailure(health_failure_message(exc.status, exc.detail)) from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise SmokeFailure(f"health was not ok: {payload}")
    print("health ok")


def health_failure_message(status: int, detail: str) -> str:
    # /health answers 503 {"status":"degraded"} when a dependency check fails.
    if status == 503:
        return (
            f"API health is degraded (HTTP 503 {detail.strip()}): the database, Redis or "
            "worker configuration check failed; see `docker compose logs api` and, in "
            "development, GET /diagnostics"
        )
    return f"GET /health failed with HTTP {status}: {detail.strip()}"


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


def render_clip_request(
    replay: dict[str, Any],
    coaching: list[Any],
    *,
    require_pov: bool = False,
) -> dict[str, Any]:
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
    request: dict[str, Any] = {
        "eventId": event.get("id"),
        "tickStart": tick_start,
        "tickEnd": tick_end,
        "tickRate": tick_rate,
        "roundNumber": first_round.get("roundNumber"),
        "renderPreset": "cloud_preview_smoke_v1",
    }
    # An external renderer (RENDER_WORKER_MODE=external) refuses a clip without a
    # SteamID64 POV; the in-repo fallback accepts one without a player at all.
    player_id = render_pov_player_id(replay, event)
    if player_id is not None:
        request["playerId"] = player_id
    elif require_pov:
        raise SmokeFailure(
            "render_clip needs a POV player, but no replay player has a 17-digit SteamID64; "
            "the production renderer refuses clips without one, so smoke a sample demo "
            "whose parsed players carry Steam IDs"
        )
    return request


def render_pov_player_id(replay: dict[str, Any], event: dict[str, Any]) -> str | None:
    """Pick a render POV the API will accept, or None when no player qualifies.

    Mirrors RenderLifecycle._resolve_render_pov: the API matches `playerId`
    against replay player ids and needs exactly one match carrying a SteamID64.
    The coaching event's own player and evidence players come first, because
    the clip is centred on that event.
    """
    raw_players = replay.get("players")
    players = [item for item in raw_players if isinstance(item, dict)] if isinstance(raw_players, list) else []
    ids = [player_id if isinstance(player_id := player.get("id"), str) else None for player in players]
    id_counts = Counter(ids)
    usable = [
        player_id
        for player, player_id in zip(players, ids, strict=True)
        if player_id is not None and id_counts[player_id] == 1 and _steam_id64(player) is not None
    ]
    context = event.get("structured_context_json")
    involved = context.get("involvedPlayerIds") if isinstance(context, dict) else None
    preferred = [event.get("player_id"), *(involved if isinstance(involved, list) else [])]
    for candidate in preferred:
        if isinstance(candidate, str) and candidate in usable:
            return candidate
    return usable[0] if usable else None


def _steam_id64(player: dict[str, Any]) -> str | None:
    # Same precedence and shape check as the API: steamId, else the player id.
    value = player.get("steamId") or player.get("id")
    if isinstance(value, str) and len(value) == 17 and value.isascii() and value.isdecimal():
        return value
    return None


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
    """Upload a sample through the browser's chunked upload session; returns the demo id.

    POST /uploads/sessions -> PUT every part (raw bytes, exact Content-Length,
    X-Upload-Token, X-Part-SHA256) -> POST .../complete, sent again while the
    API answers 202 and re-sending any part it reports missing. Parts go one at a
    time: the smoke checks the contract, not throughput.
    """
    path = resolve_sample_demo_path(str(path), require_sample=True)
    if path is None:
        raise SmokeFailure("SAMPLE_DEMO_PATH is required")
    size = path.stat().st_size
    started = time.monotonic()
    session = create_upload_session(path.name, size)
    session_id = required_str(session, "sessionId")
    token = required_str(session, "uploadToken")
    plan = UploadPlan.from_session(session, size)
    stats = UploadStats()
    received = {index for index in session.get("receivedParts") or [] if isinstance(index, int)}
    upload_parts(path, session_id, token, plan, plan.missing(received), stats)
    demo_id = complete_upload_session(path, session_id, token, plan, stats)
    print(
        f"upload session {session_id}: {plan.part_count} part(s) of {plan.part_size} bytes, "
        f"{stats.bytes_sent} bytes sent in {time.monotonic() - started:.1f}s, "
        f"{stats.retries} retr{'y' if stats.retries == 1 else 'ies'}"
    )
    return demo_id


class UploadPlan:
    """The server's part layout for one session: index i covers [i*part_size, ...)."""

    def __init__(self, *, file_size: int, part_size: int, part_count: int) -> None:
        if part_size <= 0 or part_count <= 0:
            raise SmokeFailure(f"upload session has an invalid part layout: {part_size} x {part_count}")
        if (file_size + part_size - 1) // part_size != part_count:
            raise SmokeFailure(
                f"upload session part layout does not cover the file: {part_count} part(s) of "
                f"{part_size} bytes for {file_size} bytes"
            )
        self.file_size = file_size
        self.part_size = part_size
        self.part_count = part_count

    @classmethod
    def from_session(cls, session: dict[str, Any], file_size: int) -> UploadPlan:
        part_size = session.get("partSize")
        part_count = session.get("partCount")
        if not isinstance(part_size, int) or not isinstance(part_count, int):
            raise SmokeFailure(f"upload session response missing partSize/partCount: {session}")
        return cls(file_size=file_size, part_size=part_size, part_count=part_count)

    def part_range(self, index: int) -> tuple[int, int]:
        """(offset, length) of one part; the last part is the remainder."""
        if not 0 <= index < self.part_count:
            raise SmokeFailure(f"part index {index} is outside 0..{self.part_count - 1}")
        offset = index * self.part_size
        return offset, min(self.part_size, self.file_size - offset)

    def missing(self, received: set[int]) -> list[int]:
        return [index for index in range(self.part_count) if index not in received]


class UploadStats:
    def __init__(self) -> None:
        self.bytes_sent = 0
        self.retries = 0


def create_upload_session(filename: str, size: int) -> dict[str, Any]:
    body: dict[str, Any] = {"filename": filename, "size": size, "contentType": "application/octet-stream"}
    try:
        payload = request_json("POST", "/uploads/sessions", body)
    except HttpStatusFailure as exc:
        if exc.status != 409 or error_code(exc) != "upload_session_exists":
            raise
        # A previous smoke run of this owner left an unfinished session; one
        # open session per owner, so replace it (refused while it completes).
        print("replacing an unfinished upload session left by an earlier run")
        payload = request_json("POST", "/uploads/sessions", {**body, "replace": True})
    if not isinstance(payload, dict):
        raise SmokeFailure(f"upload session response was not an object: {payload}")
    return payload


def upload_parts(
    path: Path,
    session_id: str,
    token: str,
    plan: UploadPlan,
    indexes: Sequence[int],
    stats: UploadStats,
) -> None:
    with path.open("rb") as handle:
        for index in indexes:
            offset, length = plan.part_range(index)
            handle.seek(offset)
            data = handle.read(length)
            if len(data) != length:
                raise SmokeFailure(f"sample changed while uploading: part {index} read {len(data)} of {length} bytes")
            put_part_with_retry(session_id, token, index, data, stats)


def put_part_with_retry(session_id: str, token: str, index: int, data: bytes, stats: UploadStats) -> None:
    digest = hashlib.sha256(data).hexdigest()
    for attempt in range(1, PART_ATTEMPTS + 1):
        try:
            status, body, retry_after = put_upload_part(session_id, token, index, data, digest)
        except (OSError, HTTPException) as exc:
            if attempt == PART_ATTEMPTS:
                raise SmokeFailure(f"upload part {index} failed: {exc}") from exc
            stats.retries += 1
            time.sleep(min(2 ** attempt, 10))
            continue
        if status == 200:
            payload = _json_object(body)
            if payload.get("sha256") not in (None, digest):
                raise SmokeFailure(f"upload part {index}: the API stored a different SHA-256")
            stats.bytes_sent += len(data)
            return
        # Part pool or this session's parallel limit is full: wait and resend.
        if status == 503 and attempt < PART_ATTEMPTS:
            stats.retries += 1
            time.sleep(retry_after if retry_after is not None else 2)
            continue
        raise SmokeFailure(f"PUT upload part {index} failed with HTTP {status}: {body}")
    raise SmokeFailure(f"upload part {index} did not succeed after {PART_ATTEMPTS} attempts")


def put_upload_part(
    session_id: str,
    token: str,
    index: int,
    data: bytes,
    digest: str,
) -> tuple[int, str, float | None]:
    """One raw part PUT; returns (status, body, Retry-After seconds)."""
    parsed = urlsplit(API_BASE_URL)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise SmokeFailure(f"API_BASE_URL must be http or https: {API_BASE_URL}")
    request_path = f"{parsed.path.rstrip('/')}/uploads/sessions/{session_id}/parts/{index}"
    connection_class = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=120)
    try:
        # The upload token alone authorizes a part (no cookie, like the
        # browser); production also checks that Origin is the site's own.
        connection.request(
            "PUT",
            request_path,
            body=data,
            headers={
                "X-Upload-Token": token,
                "X-Part-SHA256": digest,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
                "Origin": frontend_origin(),
            },
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body, _retry_after_seconds(response.getheader("Retry-After"))
    finally:
        connection.close()


def complete_upload_session(
    path: Path,
    session_id: str,
    token: str,
    plan: UploadPlan,
    stats: UploadStats,
    *,
    timeout_seconds: float | None = None,
) -> str:
    """POST complete until the API returns the demo; resend parts it reports missing."""
    deadline = time.monotonic() + (
        UPLOAD_COMPLETE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    complete_path = f"/uploads/sessions/{session_id}/complete"
    while time.monotonic() < deadline:
        try:
            payload = request_json("POST", complete_path)
        except HttpStatusFailure as exc:
            code = error_code(exc)
            if exc.status == 409 and code == "upload_parts_missing":
                missing = [index for index in error_field(exc, "missingParts") or [] if isinstance(index, int)]
                if not missing:
                    raise
                print(f"upload session {session_id}: re-sending {len(missing)} missing part(s)")
                upload_parts(path, session_id, token, plan, missing, stats)
                continue
            if (exc.status == 409 and code == "upload_parts_in_flight") or (
                exc.status == 503 and code == "INTAKE_BUSY"
            ):
                stats.retries += 1
                time.sleep(_retry_after_from_detail(exc, default=2))
                continue
            raise
        if isinstance(payload, dict) and isinstance(payload.get("id"), str) and payload["id"]:
            return str(payload["id"])
        if isinstance(payload, dict) and payload.get("state") == "completing":
            # 202: another attempt holds the lease. Complete again rather than
            # only reading the state: after an API restart the lease has ended
            # and only a new `complete` takes the session over; once the other
            # attempt finishes, this answers 200 with its demo (or its error).
            time.sleep(UPLOAD_SESSION_POLL_SECONDS)
            continue
        raise SmokeFailure(f"upload complete returned an unexpected response: {payload}")
    raise SmokeFailure(f"upload session {session_id} did not complete before timeout")


def error_detail(exc: HttpStatusFailure) -> dict[str, Any]:
    """The structured `detail` object of an API error body (the body itself when detail is text), or {}."""
    try:
        body = json.loads(exc.detail)
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def error_code(exc: HttpStatusFailure) -> str | None:
    # Session errors: {"detail": {"code": ...}}; intake errors keep today's
    # {"detail": "...", "errorCode": ...}, for which error_detail is the body.
    detail = error_detail(exc)
    code = detail.get("code") or detail.get("errorCode")
    return code if isinstance(code, str) else None


def error_field(exc: HttpStatusFailure, key: str) -> Any:
    return error_detail(exc).get(key)


def _retry_after_from_detail(exc: HttpStatusFailure, *, default: float) -> float:
    value = error_field(exc, "retryAfterSeconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 60:
        return float(value)
    return default


def _retry_after_seconds(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    if seconds is None or not 0 <= seconds <= 60:
        return None
    return seconds


def _json_object(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
