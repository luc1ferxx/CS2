from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.base import (
    AdapterResult,
    RenderWorkerClient,
    completed_payload,
    failed_payload,
    is_api_media_url_path,
)


NO_RENDERER_ERROR = (
    "DEV_FAKE_VIDEO_PATH is not set or does not exist; real renderer is not connected. "
    "This skeleton does not start CS2, Steam, OBS, or ffmpeg."
)


class FakeVideoAdapter:
    def __init__(self, dev_fake_video_path: Path | None):
        self.dev_fake_video_path = dev_fake_video_path

    def process(
        self,
        manifest: dict[str, Any],
        client: RenderWorkerClient,
        *,
        manifest_path: Path | None = None,
    ) -> AdapterResult:
        job_id = str(manifest["jobId"])
        fake_video_path = self.dev_fake_video_path
        fake_path_is_api_url = is_api_media_url_path(fake_video_path)
        if fake_video_path is None or (not fake_path_is_api_url and not fake_video_path.exists()):
            payload = failed_payload(manifest, NO_RENDERER_ERROR)
            client.post_result(job_id, payload)
            return AdapterResult(
                action="failed",
                job_id=job_id,
                message=NO_RENDERER_ERROR,
                manifest_path=manifest_path,
                callback_payload=payload,
            )

        if fake_path_is_api_url:
            video_url = str(fake_video_path)
            storage_key = None
        else:
            uploaded = client.upload_media(job_id, fake_video_path)
            video_url = uploaded.video_url
            storage_key = uploaded.storage_key

        payload = completed_payload(manifest, video_url, storage_key)
        client.post_result(job_id, payload)
        return AdapterResult(
            action="completed",
            job_id=job_id,
            message=f"Completed render callback with fake media {video_url}",
            manifest_path=manifest_path,
            callback_payload=payload,
            output_path=fake_video_path if not fake_path_is_api_url else None,
        )

    def build_plan(self, manifest: dict[str, Any], work_dir: Path) -> dict[str, Any]:
        fake_path = self.dev_fake_video_path
        fake_video_exists = bool(fake_path and (fake_path.exists() or is_api_media_url_path(fake_path)))
        demo_path = Path(str(manifest["demoFilePath"]))
        return {
            "adapter": "fake-video",
            "jobId": manifest["jobId"],
            "demoId": manifest["demoId"],
            "demoFilePath": str(demo_path),
            "demoFileExists": demo_path.exists(),
            "demoStorageKey": manifest.get("demoStorageKey"),
            "tickStart": manifest["tickStart"],
            "tickEnd": manifest["tickEnd"],
            "tickRate": manifest["tickRate"],
            "renderPreset": manifest["renderPreset"],
            "workDir": str(work_dir),
            "devFakeVideoPath": str(fake_path) if fake_path else None,
            "devFakeVideoExists": fake_video_exists,
            "plannedAction": "callback completed with fake mp4"
            if fake_video_exists
            else "callback failed because real renderer is not connected",
        }
