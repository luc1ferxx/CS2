"""A render job whose match was deleted answers 404/410: terminal, not a failure.

No failed callback (it would 404 too), no retry backoff, and the runner removes
what it kept for the job -- the downloaded .dem and any rendered MP4 must not
outlive the match they came from.
"""

import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import (
    RenderWorkerApiClient,
    RunnerConfig,
    complete_prepared_job,
    poll,
    prepare_job,
    process_job,
    process_manifest,
)

from adapters.base import RenderJobGoneError, UploadedMedia


def csdm_manifest(job_id: str = "clip-1") -> dict:
    return {
        "jobId": job_id, "demoId": "demo-1", "jobType": "render_clip", "status": "rendering",
        "tickStart": 640, "tickEnd": 1920, "tickRate": 64, "povSteamId": "76561198998266210",
        "demoDownloadPath": f"/render-worker/jobs/{job_id}/source", "sourceSizeBytes": 4,
        "sourceSha256": hashlib.sha256(b"demo").hexdigest(),
    }


def fake_manifest(job_id: str) -> dict:
    return {
        "manifestVersion": "render_worker_v1", "jobId": job_id, "demoId": "demo-1",
        "jobType": "render_clip", "status": "rendering", "demoFilePath": "",
        "demoStorageKey": "artifact://source", "originalFilename": "source.dem",
        "mapName": "de_dust2", "eventId": None, "playerId": "p1", "povSteamId": None,
        "tickStart": 640, "tickEnd": 1920, "tickRate": 64, "roundNumber": 1,
        "renderPreset": "coach_default",
    }


class GoneClient:
    """Answers like the API does once the job's match has been deleted at `gone_at`."""

    def __init__(self, manifest: dict | None = None, *, gone_at: str = "manifest"):
        self.manifest = manifest
        self.gone_at = gone_at
        self.downloads: list[Path] = []
        self.uploads: list[tuple[str, Path]] = []
        self.results: list[tuple[str, dict]] = []

    def fetch_manifest(self, job_id, *, claim=True):
        if self.gone_at == "manifest":
            raise RenderJobGoneError(job_id)
        return self.manifest

    def fetch_next_manifest(self, *, claim=True):
        return self.manifest

    def download_source(self, manifest, destination):
        if self.gone_at == "source":
            raise RenderJobGoneError(manifest["jobId"])
        self.downloads.append(destination)
        destination.write_bytes(b"demo")

    def upload_media(self, job_id, media_path):
        if self.gone_at == "media":
            raise RenderJobGoneError(job_id)
        self.uploads.append((job_id, media_path))
        return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

    def post_result(self, job_id, payload):
        if self.gone_at == "result":
            raise RenderJobGoneError(job_id)
        self.results.append((job_id, payload))
        return {}


class RunnerJobGoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"

    def leave_previous_workspace(self, job_id: str) -> tuple[Path, Path]:
        workspace = self.work / "jobs" / job_id
        (workspace / "output").mkdir(parents=True)
        (workspace / "source.dem").write_bytes(b"a deleted user's demo")
        (workspace / "output" / "clip.mp4").write_bytes(b"a deleted user's clip")
        manifest_path = self.work / "manifests" / f"{job_id}.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("{}", encoding="utf-8")
        return workspace, manifest_path

    def test_gone_manifest_removes_the_workspace_without_any_callback(self) -> None:
        workspace, manifest_path = self.leave_previous_workspace("clip-1")
        client = GoneClient(gone_at="manifest")
        config = RunnerConfig("http://api.test", "token", self.work, 5)

        result = process_job(config, "clip-1", client=client)

        self.assertEqual(result.action, "gone")
        self.assertEqual(client.results, [])
        self.assertFalse(workspace.exists())
        self.assertFalse(manifest_path.exists())

    def test_a_dry_run_reports_a_gone_job_but_removes_nothing(self) -> None:
        workspace, manifest_path = self.leave_previous_workspace("clip-1")
        config = RunnerConfig("http://api.test", "token", self.work, 5)

        result = process_job(config, "clip-1", client=GoneClient(gone_at="manifest"), dry_run=True)

        self.assertEqual(result.action, "gone")
        self.assertTrue(workspace.exists())
        self.assertTrue(manifest_path.exists())

    def test_fake_video_upload_to_a_deleted_job_posts_no_failed_callback(self) -> None:
        media = self.root / "fake.mp4"
        media.write_bytes(b"fixture media")
        config = RunnerConfig("http://api.test", "token", self.work, 5, dev_fake_video_path=media)
        client = GoneClient(fake_manifest("clip-2"), gone_at="media")

        result = process_manifest(config, fake_manifest("clip-2"), client=client)

        self.assertEqual(result.action, "gone")
        self.assertEqual(client.results, [])
        self.assertFalse((self.work / "manifests" / "clip-2.json").exists())
        self.assertTrue(media.exists(), "The operator's own fixture is not the job's workspace")

    def test_csdm_job_deleted_mid_render_leaves_no_source_or_clip_behind(self) -> None:
        executable = self.root / "tool.exe"
        executable.touch()
        config = RunnerConfig(
            "http://api.test", "token", self.work, 5, adapter="csdm",
            csdm_executable=executable, ffmpeg_executable=executable, ffprobe_executable=executable,
        )
        probe = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                              "pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "duration": "20.000",
                              "start_time": "0.000", "nb_read_frames": "600"}],
                 "format": {"format_name": "mov,mp4", "duration": "20.033"}}

        def command(args, log, timeout, env=None):
            if "video" in args:
                output = Path(args[args.index("--output") + 1])
                (output / "sequence-1-tick-640-to-1920.mp4").write_bytes(b"rendered clip")

        for gone_at in ("source", "media", "result"):
            with self.subTest(gone_at=gone_at):
                client = GoneClient(csdm_manifest(), gone_at=gone_at)
                with patch("adapters.csdm.assert_game_not_running"), patch(
                    "adapters.csdm.run_command", side_effect=command,
                ), patch(
                    "adapters.csdm.subprocess.run",
                    return_value=SimpleNamespace(stdout=json.dumps(probe).encode()),
                ):
                    result = process_manifest(config, csdm_manifest(), client=client)

                self.assertEqual(result.action, "gone")
                self.assertEqual(
                    [payload["status"] for _, payload in client.results], [],
                    "A deleted job gets no callback, failed or otherwise",
                )
                self.assertFalse((self.work / "jobs" / "clip-1").exists())
                self.assertFalse((self.work / "manifests" / "clip-1.json").exists())

    def test_manual_adapter_cleans_up_a_prepared_job_that_was_deleted(self) -> None:
        config = RunnerConfig(
            "http://api.test", "token", self.work, 5,
            cs2_install_dir=self.root / "cs2", steam_user_data_dir=self.root / "steam",
        )
        prepared = prepare_job(config, "clip-3", client=GoneClient(fake_manifest("clip-3"), gone_at="none"))
        self.assertEqual(prepared.action, "prepared")
        prepared.output_path.write_bytes(b"operator recording")

        result = complete_prepared_job(config, "clip-3", client=GoneClient(gone_at="media"))

        self.assertEqual(result.action, "gone")
        self.assertFalse((self.work / "jobs" / "clip-3").exists())

        gone_before_prepare = prepare_job(config, "clip-4", client=GoneClient(gone_at="manifest"))
        self.assertEqual(gone_before_prepare.action, "gone")
        self.assertFalse((self.work / "jobs" / "clip-4").exists())

    def test_a_gone_job_costs_the_poll_loop_no_backoff(self) -> None:
        media = self.root / "fake.mp4"
        media.write_bytes(b"fixture media")
        config = RunnerConfig("http://api.test", "token", self.work, 5, dev_fake_video_path=media)
        jobs = iter([fake_manifest("first"), fake_manifest("second")])
        client = GoneClient(gone_at="none")
        client.fetch_next_manifest = lambda *, claim=True: next(jobs, None)
        uploads: list[str] = []

        def upload(job_id, media_path):
            uploads.append(job_id)
            if job_id == "first":
                raise RenderJobGoneError(job_id)
            return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

        client.upload_media = upload
        stop = StopAfterWaits(2)
        emitted = []

        poll(config, client=client, stop_event=stop, on_result=emitted.append)

        self.assertEqual(uploads, ["first", "second"])
        self.assertEqual([job_id for job_id, _ in client.results], ["second"])
        self.assertEqual([item.action for item in emitted], ["polling", "gone", "completed"])
        self.assertEqual(stop.waits, [5, 5], "A deleted job is not a service failure to back off from")


class ApiClientGoneStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = RenderWorkerApiClient(RunnerConfig("http://api.test", "token", Path(self.temp.name), 5))

    def answer(self, code: int, body: bytes = b'{"detail":"Render clip job not found"}'):
        def open_request(request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "status", {}, io.BytesIO(body))
        return patch.object(self.client, "_open_request", side_effect=open_request)

    def test_404_and_410_on_job_routes_are_terminal(self) -> None:
        media = Path(self.temp.name) / "clip.mp4"
        media.write_bytes(b"clip")
        calls = {
            "manifest": lambda: self.client.fetch_manifest("clip-1"),
            "result": lambda: self.client.post_result("clip-1", {"status": "failed"}),
            "media": lambda: self.client.upload_media("clip-1", media),
            "source": lambda: self.client.download_source(csdm_manifest(), Path(self.temp.name) / "source.dem"),
        }
        for code in (404, 410):
            for name, call in calls.items():
                with self.subTest(code=code, route=name), self.answer(code):
                    with self.assertRaises(RenderJobGoneError) as raised:
                        call()
                    self.assertEqual(raised.exception.job_id, "clip-1")
        self.assertFalse((Path(self.temp.name) / "source.dem").exists())

    def test_a_404_that_is_not_the_apis_own_is_a_misconfiguration_not_a_deletion(self) -> None:
        media = Path(self.temp.name) / "clip.mp4"
        media.write_bytes(b"clip")
        foreign_404s = (
            b"<!DOCTYPE html><html><body>404: This page could not be found.</body></html>",
            b'{"detail":"Not Found"}',
            b"",
        )
        calls = {
            "manifest": lambda: self.client.fetch_manifest("clip-1"),
            "result": lambda: self.client.post_result("clip-1", {"status": "failed"}),
            "media": lambda: self.client.upload_media("clip-1", media),
            "source": lambda: self.client.download_source(csdm_manifest(), Path(self.temp.name) / "source.dem"),
        }
        for body in foreign_404s:
            for name, call in calls.items():
                with self.subTest(body=body[:20], route=name), self.answer(404, body):
                    with self.assertRaises(Exception) as raised:
                        call()
                    self.assertNotIsInstance(raised.exception, RenderJobGoneError)

    def test_a_wrong_api_url_never_deletes_the_operators_recording(self) -> None:
        work = Path(self.temp.name) / "work"
        config = RunnerConfig(
            "http://localhost:3000", "token", work, 5,
            cs2_install_dir=Path(self.temp.name) / "cs2", steam_user_data_dir=Path(self.temp.name) / "steam",
        )
        prepared = prepare_job(config, "clip-9", client=GoneClient(fake_manifest("clip-9"), gone_at="none"))
        prepared.output_path.write_bytes(b"operator recording")
        client = RenderWorkerApiClient(config)

        def open_request(request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"<html>404: This page could not be found.</html>"),
            )

        with patch.object(client, "_open_request", side_effect=open_request), self.assertRaises(RuntimeError) as raised:
            complete_prepared_job(config, "clip-9", client=client)

        self.assertNotIsInstance(raised.exception, RenderJobGoneError)
        self.assertEqual(prepared.output_path.read_bytes(), b"operator recording")

    def test_other_errors_and_the_next_route_stay_retryable(self) -> None:
        with self.answer(500), self.assertRaises(RuntimeError) as raised:
            self.client.fetch_manifest("clip-1")
        self.assertNotIsInstance(raised.exception, RenderJobGoneError)
        with self.answer(404), self.assertRaises(RuntimeError) as raised:
            self.client.fetch_next_manifest()
        self.assertNotIsInstance(raised.exception, RenderJobGoneError)


class StopAfterWaits:
    def __init__(self, count: int):
        self.count = count
        self.waits: list[int] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, seconds: int) -> bool:
        self.waits.append(seconds)
        if len(self.waits) >= self.count:
            self.stopped = True
        return self.stopped


if __name__ == "__main__":
    unittest.main()
