from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Protocol

RUNNER_ROOT = Path(__file__).resolve().parent
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

from adapters.base import AdapterConfigError, AdapterResult, UploadedMedia
from adapters.csdm import CSDMAdapter, CSDMConfig, assert_game_not_running, safe_job_id, validate_manifest
from adapters.cs2_manual import CS2ManualAdapter
from adapters.fake_video import FakeVideoAdapter


DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_RENDER_WORKER_TOKEN = "dev-render-worker-token"
DEFAULT_WORK_DIR = ".render-worker-work"
DEFAULT_POLL_INTERVAL_SECONDS = 5
MEDIA_UPLOAD_CHUNK_BYTES = 1024 * 1024


class WorkerClient(Protocol):
    def download_source(self, manifest: dict[str, Any], destination: Path) -> None:
        ...

    def fetch_manifest(self, job_id: str, *, claim: bool = True) -> dict[str, Any]:
        ...

    def fetch_next_manifest(self, *, claim: bool = True) -> dict[str, Any] | None:
        ...

    def upload_media(self, job_id: str, media_path: Path) -> UploadedMedia:
        ...

    def post_result(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class RunnerConfig:
    api_base_url: str
    render_worker_token: str
    work_dir: Path
    poll_interval_seconds: int
    dev_fake_video_path: Path | None = None
    cs2_install_dir: Path | None = None
    steam_user_data_dir: Path | None = None
    cs2_manual_output_filename: str = "{job_id}.mp4"
    adapter: str = "fake-video"
    csdm_executable: Path | None = None
    csdm_cli_entrypoint: Path | None = None
    ffmpeg_executable: Path | None = None
    ffprobe_executable: Path | None = None
    csdm_timeout_seconds: int = 1800
    csdm_recording_system: str = "HLAE"
    max_source_bytes: int = 2 * 1024 * 1024 * 1024
    stop_file: Path | None = None

    @classmethod
    def from_env(cls) -> RunnerConfig:
        fake_path = os.getenv("DEV_FAKE_VIDEO_PATH")
        cs2_install_dir = os.getenv("CS2_INSTALL_DIR")
        steam_user_data_dir = os.getenv("STEAM_USER_DATA_DIR")
        return cls(
            api_base_url=os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            render_worker_token=os.getenv("RENDER_WORKER_TOKEN", DEFAULT_RENDER_WORKER_TOKEN),
            work_dir=Path(os.getenv("WORK_DIR", DEFAULT_WORK_DIR)),
            poll_interval_seconds=int(
                os.getenv("POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS))
            ),
            dev_fake_video_path=Path(fake_path) if fake_path else None,
            cs2_install_dir=Path(cs2_install_dir) if cs2_install_dir else None,
            steam_user_data_dir=Path(steam_user_data_dir) if steam_user_data_dir else None,
            cs2_manual_output_filename=os.getenv("CS2_MANUAL_OUTPUT_FILENAME", "{job_id}.mp4"),
            adapter=os.getenv("RENDER_ADAPTER", "fake-video"),
            csdm_executable=_env_path("CSDM_EXECUTABLE"),
            csdm_cli_entrypoint=_env_path("CSDM_CLI_ENTRYPOINT"),
            ffmpeg_executable=_env_path("FFMPEG_EXECUTABLE"),
            ffprobe_executable=_env_path("FFPROBE_EXECUTABLE"),
            csdm_timeout_seconds=int(os.getenv("CSDM_TIMEOUT_SECONDS", "1800")),
            csdm_recording_system=os.getenv("CSDM_RECORDING_SYSTEM", "HLAE"),
            max_source_bytes=int(os.getenv("MAX_SOURCE_BYTES", str(2 * 1024 * 1024 * 1024))),
            stop_file=_env_path("RENDER_STOP_FILE"),
        )


WorkerRunResult = AdapterResult


class RenderWorkerApiClient:
    def __init__(self, config: RunnerConfig):
        self.config = config

    def _open_request(self, request: urllib.request.Request, *, timeout: int):
        return urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout)

    def download_source(self, manifest: dict[str, Any], destination: Path) -> None:
        validate_manifest(manifest)
        expected_size = manifest["sourceSizeBytes"]
        if not 0 < expected_size <= self.config.max_source_bytes:
            raise RuntimeError("Source size exceeds the configured worker download limit")
        if destination.exists():
            raise RuntimeError("Source destination already exists")
        request = urllib.request.Request(
            f"{self.config.api_base_url}{manifest['demoDownloadPath']}",
            headers={"X-Render-Worker-Token": self.config.render_worker_token},
            method="GET",
        )
        # Never follow a redirect carrying the worker token, even to another local service.
        partial = destination.with_suffix(".dem.part")
        created = False
        try:
            with self._open_request(request, timeout=120) as response:
                if response.status != 200:
                    raise RuntimeError("Source download did not return HTTP 200")
                if response.headers.get("Content-Length") != str(expected_size):
                    raise RuntimeError("Source download length does not match its accepted artifact")
                expected_hash = manifest["sourceSha256"].lower()
                if response.headers.get("X-Content-SHA256", "").lower() != expected_hash:
                    raise RuntimeError("Source download checksum header does not match its accepted artifact")
                digest, received = hashlib.sha256(), 0
                with partial.open("xb") as output:
                    created = True
                    while chunk := response.read(MEDIA_UPLOAD_CHUNK_BYTES):
                        received += len(chunk)
                        if received > expected_size:
                            raise RuntimeError("Source download exceeded its accepted size")
                        digest.update(chunk)
                        output.write(chunk)
                if received != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_hash):
                    raise RuntimeError("Source download failed accepted artifact integrity verification")
            partial.replace(destination)
        finally:
            if created and partial.exists():
                partial.unlink()

    def fetch_manifest(self, job_id: str, *, claim: bool = True) -> dict[str, Any]:
        status, payload = self._request_json(
            "GET",
            f"/render-worker/jobs/{job_id}/manifest?claim={_bool_query(claim)}",
        )
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected manifest response for {job_id}: HTTP {status}")
        return payload

    def fetch_next_manifest(self, *, claim: bool = True) -> dict[str, Any] | None:
        status, payload = self._request_json("GET", f"/render-worker/jobs/next?claim={_bool_query(claim)}")
        if status == 204:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected next-job response: HTTP {status}")
        return payload

    def upload_media(self, job_id: str, media_path: Path) -> UploadedMedia:
        status, payload = self._upload_file(f"/render-worker/jobs/{job_id}/media", media_path)
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected media upload response for {job_id}: HTTP {status}")
        video_url = payload.get("videoUrl")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError("Media upload response did not include videoUrl")
        storage_key = payload.get("storageKey")
        if not isinstance(storage_key, str) or not storage_key:
            raise RuntimeError("Media upload response did not include storageKey")
        return UploadedMedia(video_url=video_url, storage_key=storage_key)

    def post_result(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        status, response = self._request_json(
            "POST",
            f"/render-worker/jobs/{job_id}/result",
            payload,
        )
        if status != 200 or not isinstance(response, dict):
            raise RuntimeError(f"Unexpected result callback response for {job_id}: HTTP {status}")
        return response

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        data = None
        headers = {"X-Render-Worker-Token": self.config.render_worker_token}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.config.api_base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._open_request(request, timeout=30) as response:
                body = response.read()
                return response.status, json.loads(body.decode("utf-8")) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code == 204:
                return 204, None
            detail = body.decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {path}: {detail}") from exc

    def _upload_file(self, path: str, media_path: Path) -> tuple[int, Any]:
        boundary = f"----render-worker-{uuid.uuid4().hex}"
        filename = media_path.name
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")

        with media_path.open("rb") as media_file:
            media_size = os.fstat(media_file.fileno()).st_size
            request = urllib.request.Request(
                f"{self.config.api_base_url}{path}",
                data=_iter_multipart_upload(media_file, prefix, suffix),
                headers={
                    "X-Render-Worker-Token": self.config.render_worker_token,
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(prefix) + media_size + len(suffix)),
                },
                method="POST",
            )
            try:
                with self._open_request(request, timeout=120) as response:
                    response_body = response.read()
                    return response.status, json.loads(response_body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} from {path}: {detail}") from exc


def _iter_multipart_upload(
    media_file: BinaryIO,
    prefix: bytes,
    suffix: bytes,
) -> Iterator[bytes]:
    yield prefix
    while chunk := media_file.read(MEDIA_UPLOAD_CHUNK_BYTES):
        yield chunk
    yield suffix


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _env_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value) if value else None


@contextmanager
def renderer_lock(work_dir: Path):
    """An OS lock is released on process exit, including crashes; the file is reusable."""
    work_dir.mkdir(parents=True, exist_ok=True)
    with (work_dir / "renderer.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AdapterConfigError("Another renderer is using this WORK_DIR") from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _processing_guard(config: RunnerConfig, dry_run: bool):
    if config.adapter not in {"fake-video", "csdm"}:
        raise AdapterConfigError("RENDER_ADAPTER must be fake-video or csdm")
    if config.adapter == "csdm" and not dry_run:
        csdm_adapter(config).config.validate()
        assert_game_not_running()
        return renderer_lock(config.work_dir)
    return nullcontext()


def process_job(
    config: RunnerConfig,
    job_id: str,
    *,
    client: WorkerClient | None = None,
    dry_run: bool = False,
) -> WorkerRunResult:
    worker_client = client or RenderWorkerApiClient(config)
    with _processing_guard(config, dry_run):
        manifest = worker_client.fetch_manifest(job_id, claim=not dry_run)
        return process_manifest(config, manifest, client=worker_client, dry_run=dry_run)


def poll_once(
    config: RunnerConfig,
    *,
    client: WorkerClient | None = None,
    dry_run: bool = False,
) -> WorkerRunResult:
    worker_client = client or RenderWorkerApiClient(config)
    with _processing_guard(config, dry_run):
        return _process_next_available(config, worker_client, dry_run=dry_run)


def _process_next_available(config: RunnerConfig, client: WorkerClient, *, dry_run: bool = False) -> WorkerRunResult:
    manifest = client.fetch_next_manifest(claim=not dry_run)
    if manifest is None:
        return WorkerRunResult("no-job", None, "No queued render_clip job is available.")
    return process_manifest(config, manifest, client=client, dry_run=dry_run)


def poll(
    config: RunnerConfig,
    *,
    client: WorkerClient | None = None,
    stop_event: threading.Event | None = None,
    on_result: Callable[[WorkerRunResult], None] | None = None,
) -> WorkerRunResult:
    """Run one foreground queue consumer; finish a current clip before stopping."""
    if config.adapter not in {"fake-video", "csdm"}:
        raise AdapterConfigError("RENDER_ADAPTER must be fake-video or csdm")
    if not 1 <= config.poll_interval_seconds <= 60:
        raise AdapterConfigError("POLL_INTERVAL_SECONDS must be between 1 and 60")
    if config.stop_file is not None and not config.stop_file.is_absolute():
        raise AdapterConfigError("RENDER_STOP_FILE / --stop-file must be an absolute operator file path")
    worker_client = client or RenderWorkerApiClient(config)
    stop = stop_event if stop_event is not None else threading.Event()
    emit = on_result or (lambda result: print(format_result(result), flush=True))
    last_waiting_message: str | None = None
    retry_delay = config.poll_interval_seconds

    def stop_requested() -> bool:
        return stop.is_set() or (config.stop_file is not None and config.stop_file.exists())

    # Hold the same lock while idle as while recording; a second consumer cannot compete.
    with renderer_lock(config.work_dir):
        emit(WorkerRunResult("polling", None, "Waiting for render_clip jobs; Ctrl+C stops after the current clip finishes."))
        while not stop_requested():
            try:
                if config.adapter == "csdm":
                    csdm_adapter(config).config.validate()
                    assert_game_not_running()
                if stop_requested():
                    break
                result = _process_next_available(config, worker_client)
                last_waiting_message = None
                retry_delay = config.poll_interval_seconds
                if result.action != "no-job":
                    emit(result)
            except AdapterConfigError as exc:
                message = str(exc)
                if message != last_waiting_message:
                    emit(WorkerRunResult("waiting", None, message))
                    last_waiting_message = message
            except (OSError, RuntimeError):
                message = "Render service request failed; inspect the API and operator job status before retrying."
                if message != last_waiting_message:
                    emit(WorkerRunResult("waiting", None, message))
                    last_waiting_message = message
                retry_delay = min(60, max(config.poll_interval_seconds, retry_delay * 2))
            if stop_requested():
                break
            stop.wait(retry_delay)
    return WorkerRunResult("stopped", None, "Renderer stopped; no further jobs will be claimed.")


@contextmanager
def stop_after_current_job(stop: threading.Event):
    """SIGINT requests an orderly stop instead of abandoning a claimed recording."""
    watched_signals = [signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):
        watched_signals.append(signal.SIGBREAK)
    previous = {sig: signal.getsignal(sig) for sig in watched_signals}

    def request_stop(_signal, _frame):
        stop.set()

    try:
        for sig in watched_signals:
            signal.signal(sig, request_stop)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def process_manifest(
    config: RunnerConfig,
    manifest: dict[str, Any],
    *,
    client: WorkerClient,
    dry_run: bool = False,
) -> WorkerRunResult:
    job_id = str(manifest["jobId"])
    manifest_path = write_manifest_snapshot(config, manifest)
    adapter = csdm_adapter(config) if config.adapter == "csdm" else fake_video_adapter(config)
    if dry_run:
        plan = adapter.build_plan(manifest, config.work_dir)
        return WorkerRunResult(
            action="dry-run",
            job_id=job_id,
            message=json.dumps(plan, indent=2, sort_keys=True),
            manifest_path=manifest_path,
        )

    return adapter.process(manifest, client, manifest_path=manifest_path)


def prepare_job(
    config: RunnerConfig,
    job_id: str,
    *,
    adapter_name: str = "cs2-manual",
    client: WorkerClient | None = None,
) -> WorkerRunResult:
    if adapter_name != "cs2-manual":
        raise ValueError("prepare-job currently supports only --adapter cs2-manual")
    worker_client = client or RenderWorkerApiClient(config)
    manifest = worker_client.fetch_manifest(job_id, claim=True)
    return cs2_manual_adapter(config).prepare(manifest)


def complete_prepared_job(
    config: RunnerConfig,
    job_id: str,
    *,
    video_path: Path | None = None,
    client: WorkerClient | None = None,
) -> WorkerRunResult:
    worker_client = client or RenderWorkerApiClient(config)
    return cs2_manual_adapter(config).complete_prepared_job(
        job_id,
        worker_client,
        video_path=video_path,
    )


def fake_video_adapter(config: RunnerConfig) -> FakeVideoAdapter:
    return FakeVideoAdapter(dev_fake_video_path=config.dev_fake_video_path)


def csdm_adapter(config: RunnerConfig) -> CSDMAdapter:
    return CSDMAdapter(CSDMConfig(
        executable=config.csdm_executable,
        ffmpeg_executable=config.ffmpeg_executable,
        ffprobe_executable=config.ffprobe_executable,
        timeout_seconds=config.csdm_timeout_seconds,
        cli_entrypoint=config.csdm_cli_entrypoint,
        recording_system=config.csdm_recording_system,
        cs2_install_dir=config.cs2_install_dir,
    ), config.work_dir)


def cs2_manual_adapter(config: RunnerConfig) -> CS2ManualAdapter:
    return CS2ManualAdapter(
        cs2_install_dir=config.cs2_install_dir,
        steam_user_data_dir=config.steam_user_data_dir,
        work_dir=config.work_dir,
        output_filename_template=config.cs2_manual_output_filename,
    )


def _bool_query(value: bool) -> str:
    return "true" if value else "false"


def write_manifest_snapshot(config: RunnerConfig, manifest: dict[str, Any]) -> Path:
    manifest_dir = config.work_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{safe_job_id(manifest['jobId'])}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render Worker adapter runner")
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--dev-fake-video-path", default=None)
    parser.add_argument("--cs2-install-dir", default=None)
    parser.add_argument("--steam-user-data-dir", default=None)
    parser.add_argument("--cs2-manual-output-filename", default=None)
    parser.add_argument("--adapter", choices=["fake-video", "csdm"], default=None)
    parser.add_argument("--csdm-executable", default=None)
    parser.add_argument("--csdm-cli-entrypoint", default=None)
    parser.add_argument("--csdm-recording-system", choices=["HLAE", "CS"], default=None)
    parser.add_argument("--ffmpeg-executable", default=None)
    parser.add_argument("--ffprobe-executable", default=None)
    parser.add_argument("--stop-file", default=None, help="Absolute operator stop flag path for the poll command")

    subparsers = parser.add_subparsers(dest="command", required=True)

    poll_once_parser = subparsers.add_parser("poll-once", help="Fetch and process one queued job")
    poll_once_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("poll", help="Process queued clips serially until Ctrl+C; finish the current clip before stopping")

    process_parser = subparsers.add_parser("process-job", help="Process one job by id")
    process_parser.add_argument("job_id")
    process_parser.add_argument("--dry-run", action="store_true")

    dry_run_parser = subparsers.add_parser("dry-run", help="Print manifest and plan without callback")
    dry_run_parser.add_argument("job_id", nargs="?")

    prepare_parser = subparsers.add_parser("prepare-job", help="Prepare a manual adapter workspace")
    prepare_parser.add_argument("--job-id", required=True)
    prepare_parser.add_argument("--adapter", default="cs2-manual", choices=["cs2-manual"])

    complete_parser = subparsers.add_parser(
        "complete-prepared-job",
        help="Upload a prepared manual mp4 and post completed callback",
    )
    complete_parser.add_argument("--job-id", required=True)
    complete_parser.add_argument("--video-path", default=None)

    return parser


def config_from_args(args: argparse.Namespace) -> RunnerConfig:
    config = RunnerConfig.from_env()
    return RunnerConfig(
        api_base_url=(args.api_base_url or config.api_base_url).rstrip("/"),
        render_worker_token=args.token or config.render_worker_token,
        work_dir=Path(args.work_dir) if args.work_dir else config.work_dir,
        poll_interval_seconds=config.poll_interval_seconds,
        dev_fake_video_path=Path(args.dev_fake_video_path)
        if args.dev_fake_video_path
        else config.dev_fake_video_path,
        cs2_install_dir=Path(args.cs2_install_dir)
        if args.cs2_install_dir
        else config.cs2_install_dir,
        steam_user_data_dir=Path(args.steam_user_data_dir)
        if args.steam_user_data_dir
        else config.steam_user_data_dir,
        cs2_manual_output_filename=args.cs2_manual_output_filename
        or config.cs2_manual_output_filename,
        adapter=args.adapter or config.adapter,
        csdm_executable=Path(args.csdm_executable) if args.csdm_executable else config.csdm_executable,
        csdm_cli_entrypoint=Path(args.csdm_cli_entrypoint) if args.csdm_cli_entrypoint else config.csdm_cli_entrypoint,
        ffmpeg_executable=Path(args.ffmpeg_executable) if args.ffmpeg_executable else config.ffmpeg_executable,
        ffprobe_executable=Path(args.ffprobe_executable) if args.ffprobe_executable else config.ffprobe_executable,
        csdm_timeout_seconds=config.csdm_timeout_seconds,
        csdm_recording_system=args.csdm_recording_system or config.csdm_recording_system,
        max_source_bytes=config.max_source_bytes,
        stop_file=Path(args.stop_file) if args.stop_file else config.stop_file,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    if args.command == "poll":
        stop = threading.Event()
        with stop_after_current_job(stop):
            result = poll(config, stop_event=stop)
    elif args.command == "poll-once":
        result = poll_once(config, dry_run=args.dry_run)
    elif args.command == "process-job":
        result = process_job(config, args.job_id, dry_run=args.dry_run)
    elif args.command == "dry-run":
        if args.job_id:
            result = process_job(config, args.job_id, dry_run=True)
        else:
            result = poll_once(config, dry_run=True)
    elif args.command == "prepare-job":
        result = prepare_job(config, args.job_id, adapter_name=args.adapter)
    elif args.command == "complete-prepared-job":
        result = complete_prepared_job(
            config,
            args.job_id,
            video_path=Path(args.video_path) if args.video_path else None,
        )
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(format_result(result), flush=True)
    return 0 if result.action != "failed" else 2


def format_result(result: WorkerRunResult) -> str:
    return json.dumps(
        {
            "action": result.action,
            "jobId": result.job_id,
            "message": result.message,
            "manifestPath": str(result.manifest_path) if result.manifest_path else None,
            "callbackPayload": result.callback_payload,
            "workspacePath": str(result.workspace_path) if result.workspace_path else None,
            "outputPath": str(result.output_path) if result.output_path else None,
        },
        indent=2,
        sort_keys=True,
    )


if __name__ == "__main__":
    sys.exit(main())
