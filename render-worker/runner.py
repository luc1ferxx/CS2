from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_RENDER_WORKER_TOKEN = "dev-render-worker-token"
DEFAULT_WORK_DIR = ".render-worker-work"
DEFAULT_POLL_INTERVAL_SECONDS = 5
NO_RENDERER_ERROR = (
    "DEV_FAKE_VIDEO_PATH is not set or does not exist; real renderer is not connected. "
    "This skeleton does not start CS2, Steam, OBS, or ffmpeg."
)


class WorkerClient(Protocol):
    def fetch_manifest(self, job_id: str) -> dict[str, Any]:
        ...

    def fetch_next_manifest(self) -> dict[str, Any] | None:
        ...

    def upload_media(self, job_id: str, media_path: Path) -> str:
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

    @classmethod
    def from_env(cls) -> RunnerConfig:
        fake_path = os.getenv("DEV_FAKE_VIDEO_PATH")
        return cls(
            api_base_url=os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            render_worker_token=os.getenv("RENDER_WORKER_TOKEN", DEFAULT_RENDER_WORKER_TOKEN),
            work_dir=Path(os.getenv("WORK_DIR", DEFAULT_WORK_DIR)),
            poll_interval_seconds=int(
                os.getenv("POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS))
            ),
            dev_fake_video_path=Path(fake_path) if fake_path else None,
        )


@dataclass(frozen=True)
class WorkerRunResult:
    action: str
    job_id: str | None
    message: str
    manifest_path: Path | None = None
    callback_payload: dict[str, Any] | None = None


class RenderWorkerApiClient:
    def __init__(self, config: RunnerConfig):
        self.config = config

    def fetch_manifest(self, job_id: str) -> dict[str, Any]:
        status, payload = self._request_json("GET", f"/render-worker/jobs/{job_id}/manifest")
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected manifest response for {job_id}: HTTP {status}")
        return payload

    def fetch_next_manifest(self) -> dict[str, Any] | None:
        status, payload = self._request_json("GET", "/render-worker/jobs/next")
        if status == 204:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected next-job response: HTTP {status}")
        return payload

    def upload_media(self, job_id: str, media_path: Path) -> str:
        status, payload = self._upload_file(f"/render-worker/jobs/{job_id}/media", media_path)
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected media upload response for {job_id}: HTTP {status}")
        video_url = payload.get("videoUrl")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError("Media upload response did not include videoUrl")
        return video_url

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
            with urllib.request.urlopen(request, timeout=30) as response:
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
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    'Content-Disposition: form-data; name="file"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                b"Content-Type: video/mp4\r\n\r\n",
                media_path.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        request = urllib.request.Request(
            f"{self.config.api_base_url}{path}",
            data=body,
            headers={
                "X-Render-Worker-Token": self.config.render_worker_token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_body = response.read()
                return response.status, json.loads(response_body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {path}: {detail}") from exc


def process_job(
    config: RunnerConfig,
    job_id: str,
    *,
    client: WorkerClient | None = None,
    dry_run: bool = False,
) -> WorkerRunResult:
    worker_client = client or RenderWorkerApiClient(config)
    manifest = worker_client.fetch_manifest(job_id)
    return process_manifest(config, manifest, client=worker_client, dry_run=dry_run)


def poll_once(
    config: RunnerConfig,
    *,
    client: WorkerClient | None = None,
    dry_run: bool = False,
) -> WorkerRunResult:
    worker_client = client or RenderWorkerApiClient(config)
    manifest = worker_client.fetch_next_manifest()
    if manifest is None:
        return WorkerRunResult(
            action="no-job",
            job_id=None,
            message="No queued render_clip job is available.",
        )
    return process_manifest(config, manifest, client=worker_client, dry_run=dry_run)


def process_manifest(
    config: RunnerConfig,
    manifest: dict[str, Any],
    *,
    client: WorkerClient,
    dry_run: bool = False,
) -> WorkerRunResult:
    job_id = str(manifest["jobId"])
    manifest_path = write_manifest_snapshot(config, manifest)
    plan = build_plan(config, manifest)
    if dry_run:
        return WorkerRunResult(
            action="dry-run",
            job_id=job_id,
            message=json.dumps(plan, indent=2, sort_keys=True),
            manifest_path=manifest_path,
        )

    fake_video_path = config.dev_fake_video_path
    fake_path_is_api_url = is_api_media_url_path(fake_video_path)
    if fake_video_path is None or (not fake_path_is_api_url and not fake_video_path.exists()):
        payload = failed_payload(manifest, NO_RENDERER_ERROR)
        client.post_result(job_id, payload)
        return WorkerRunResult(
            action="failed",
            job_id=job_id,
            message=NO_RENDERER_ERROR,
            manifest_path=manifest_path,
            callback_payload=payload,
        )

    if fake_path_is_api_url:
        video_url = str(fake_video_path)
    else:
        video_url = client.upload_media(job_id, fake_video_path)

    payload = completed_payload(manifest, video_url)
    client.post_result(job_id, payload)
    return WorkerRunResult(
        action="completed",
        job_id=job_id,
        message=f"Completed render callback with fake media {video_url}",
        manifest_path=manifest_path,
        callback_payload=payload,
    )


def build_plan(config: RunnerConfig, manifest: dict[str, Any]) -> dict[str, Any]:
    demo_path = Path(str(manifest["demoFilePath"]))
    fake_path = config.dev_fake_video_path
    return {
        "jobId": manifest["jobId"],
        "demoId": manifest["demoId"],
        "demoFilePath": str(demo_path),
        "demoFileExists": demo_path.exists(),
        "demoStorageKey": manifest.get("demoStorageKey"),
        "tickStart": manifest["tickStart"],
        "tickEnd": manifest["tickEnd"],
        "tickRate": manifest["tickRate"],
        "renderPreset": manifest["renderPreset"],
        "workDir": str(config.work_dir),
        "devFakeVideoPath": str(fake_path) if fake_path else None,
        "devFakeVideoExists": bool(
            fake_path and (fake_path.exists() or is_api_media_url_path(fake_path))
        ),
        "plannedAction": "callback completed with fake mp4"
        if fake_path and (fake_path.exists() or is_api_media_url_path(fake_path))
        else "callback failed because real renderer is not connected",
    }


def completed_payload(manifest: dict[str, Any], video_url: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "videoUrl": video_url,
        "localMediaPath": None,
        "tickStart": int(manifest["tickStart"]),
        "tickEnd": int(manifest["tickEnd"]),
        "tickRate": int(manifest["tickRate"]),
        "timeOriginSeconds": 0,
        "durationSeconds": clip_duration_seconds(manifest),
        "errorMessage": None,
    }


def failed_payload(manifest: dict[str, Any], error_message: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "videoUrl": None,
        "localMediaPath": None,
        "tickStart": int(manifest["tickStart"]),
        "tickEnd": int(manifest["tickEnd"]),
        "tickRate": int(manifest["tickRate"]),
        "timeOriginSeconds": 0,
        "durationSeconds": clip_duration_seconds(manifest),
        "errorMessage": error_message,
    }


def clip_duration_seconds(manifest: dict[str, Any]) -> float:
    tick_rate = max(1, int(manifest["tickRate"]))
    return round((int(manifest["tickEnd"]) - int(manifest["tickStart"])) / tick_rate, 3)


def write_manifest_snapshot(config: RunnerConfig, manifest: dict[str, Any]) -> Path:
    manifest_dir = config.work_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{manifest['jobId']}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def is_api_media_url_path(path: Path | None) -> bool:
    return path is not None and str(path).startswith("/media/videos/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render Worker V1 skeleton runner")
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--dev-fake-video-path", default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    poll_once_parser = subparsers.add_parser("poll-once", help="Fetch and process one queued job")
    poll_once_parser.add_argument("--dry-run", action="store_true")

    process_parser = subparsers.add_parser("process-job", help="Process one job by id")
    process_parser.add_argument("job_id")
    process_parser.add_argument("--dry-run", action="store_true")

    dry_run_parser = subparsers.add_parser("dry-run", help="Print manifest and plan without callback")
    dry_run_parser.add_argument("job_id", nargs="?")

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
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    if args.command == "poll-once":
        result = poll_once(config, dry_run=args.dry_run)
    elif args.command == "process-job":
        result = process_job(config, args.job_id, dry_run=args.dry_run)
    elif args.command == "dry-run":
        if args.job_id:
            result = process_job(config, args.job_id, dry_run=True)
        else:
            result = poll_once(config, dry_run=True)
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
        },
        indent=2,
        sort_keys=True,
    )


if __name__ == "__main__":
    sys.exit(main())
