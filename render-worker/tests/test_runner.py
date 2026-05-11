import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("render_worker_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RenderWorkerRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner()

    def test_process_job_dry_run_prints_plan_without_callback(self) -> None:
        client = FakeClient(manifest=manifest("render-job-dry-run"))
        with tempfile.TemporaryDirectory() as directory:
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=Path(directory),
                poll_interval_seconds=5,
                dev_fake_video_path=None,
            )

            result = self.runner.process_job(config, "render-job-dry-run", client=client, dry_run=True)

        self.assertEqual(result.action, "dry-run")
        self.assertEqual(client.fetched_job_ids, ["render-job-dry-run"])
        self.assertEqual(client.fetch_claims, [False])
        self.assertEqual(client.posted_results, [])
        self.assertIn("real renderer is not connected", result.message)

    def test_poll_once_dry_run_reads_next_manifest_without_callback(self) -> None:
        client = FakeClient(manifest=manifest("render-job-next"))
        with tempfile.TemporaryDirectory() as directory:
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=Path(directory),
                poll_interval_seconds=5,
                dev_fake_video_path=None,
            )

            result = self.runner.poll_once(config, client=client, dry_run=True)

        self.assertEqual(result.action, "dry-run")
        self.assertEqual(result.job_id, "render-job-next")
        self.assertEqual(client.next_fetches, 1)
        self.assertEqual(client.next_claims, [False])
        self.assertEqual(client.posted_results, [])

    def test_missing_fake_video_posts_failed_callback(self) -> None:
        client = FakeClient(manifest=manifest("render-job-missing-video"))
        with tempfile.TemporaryDirectory() as directory:
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=Path(directory),
                poll_interval_seconds=5,
                dev_fake_video_path=Path(directory) / "missing.mp4",
            )

            result = self.runner.process_job(config, "render-job-missing-video", client=client)

        self.assertEqual(result.action, "failed")
        self.assertEqual(client.fetch_claims, [True])
        self.assertEqual(len(client.posted_results), 1)
        job_id, payload = client.posted_results[0]
        self.assertEqual(job_id, "render-job-missing-video")
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["videoUrl"])
        self.assertEqual(payload["tickStart"], 640)
        self.assertEqual(payload["tickEnd"], 1280)
        self.assertEqual(payload["tickRate"], 64)
        self.assertIn("DEV_FAKE_VIDEO_PATH", payload["errorMessage"])

    def test_fake_video_upload_posts_completed_callback_payload(self) -> None:
        client = FakeClient(
            manifest=manifest("render-job-fake-video"),
            uploaded_video_url="/media/videos/demo-1/render-job-fake-video.mp4",
        )
        with tempfile.TemporaryDirectory() as directory:
            fake_video = Path(directory) / "fake.mp4"
            fake_video.write_bytes(b"fake mp4 bytes")
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=Path(directory),
                poll_interval_seconds=5,
                dev_fake_video_path=fake_video,
            )

            result = self.runner.process_job(config, "render-job-fake-video", client=client)

        self.assertEqual(result.action, "completed")
        self.assertEqual(client.fetch_claims, [True])
        self.assertEqual(client.uploaded_media, [("render-job-fake-video", fake_video)])
        self.assertEqual(len(client.posted_results), 1)
        job_id, payload = client.posted_results[0]
        self.assertEqual(job_id, "render-job-fake-video")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["videoUrl"], "/media/videos/demo-1/render-job-fake-video.mp4")
        self.assertEqual(payload["tickStart"], 640)
        self.assertEqual(payload["tickEnd"], 1280)
        self.assertEqual(payload["tickRate"], 64)
        self.assertEqual(payload["durationSeconds"], 10)
        self.assertIsNone(payload["errorMessage"])

    def test_prepare_job_command_function_creates_manual_workspace(self) -> None:
        client = FakeClient(manifest=manifest("render-job-prepare"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cs2_dir = root / "cs2"
            steam_dir = root / "steam"
            work_dir = root / "work"
            cs2_dir.mkdir()
            steam_dir.mkdir()
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=work_dir,
                poll_interval_seconds=5,
                cs2_install_dir=cs2_dir,
                steam_user_data_dir=steam_dir,
            )

            result = self.runner.prepare_job(config, "render-job-prepare", client=client)
            self.assertTrue((result.workspace_path / "manifest.json").exists())
            self.assertTrue((result.workspace_path / "instructions.md").exists())
            self.assertTrue((result.workspace_path / "expected_output.json").exists())

        self.assertEqual(result.action, "prepared")
        self.assertEqual(client.fetched_job_ids, ["render-job-prepare"])
        self.assertEqual(client.fetch_claims, [True])

    def test_complete_prepared_job_command_function_callbacks_with_existing_output(self) -> None:
        client = FakeClient(
            manifest=manifest("render-job-manual-complete"),
            uploaded_video_url="/media/videos/demo-1/manual-complete.mp4",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cs2_dir = root / "cs2"
            steam_dir = root / "steam"
            work_dir = root / "work"
            cs2_dir.mkdir()
            steam_dir.mkdir()
            config = self.runner.RunnerConfig(
                api_base_url="http://api.test",
                render_worker_token="token",
                work_dir=work_dir,
                poll_interval_seconds=5,
                cs2_install_dir=cs2_dir,
                steam_user_data_dir=steam_dir,
            )
            prepared = self.runner.prepare_job(config, "render-job-manual-complete", client=client)
            output_path = prepared.output_path
            output_path.write_bytes(b"manual render bytes")

            result = self.runner.complete_prepared_job(
                config,
                "render-job-manual-complete",
                client=client,
            )

        self.assertEqual(result.action, "completed")
        self.assertEqual(client.uploaded_media, [("render-job-manual-complete", output_path)])
        self.assertEqual(client.posted_results[-1][1]["status"], "completed")
        self.assertEqual(client.posted_results[-1][1]["videoUrl"], "/media/videos/demo-1/manual-complete.mp4")


class FakeClient:
    def __init__(self, *, manifest: dict, uploaded_video_url: str = "/media/videos/demo-1/fake.mp4"):
        self.manifest = manifest
        self.uploaded_video_url = uploaded_video_url
        self.fetched_job_ids: list[str] = []
        self.fetch_claims: list[bool] = []
        self.next_fetches = 0
        self.next_claims: list[bool] = []
        self.uploaded_media: list[tuple[str, Path]] = []
        self.posted_results: list[tuple[str, dict]] = []

    def fetch_manifest(self, job_id: str, *, claim: bool = True) -> dict:
        self.fetched_job_ids.append(job_id)
        self.fetch_claims.append(claim)
        return self.manifest

    def fetch_next_manifest(self, *, claim: bool = True) -> dict | None:
        self.next_fetches += 1
        self.next_claims.append(claim)
        return self.manifest

    def upload_media(self, job_id: str, media_path: Path) -> str:
        self.uploaded_media.append((job_id, media_path))
        return self.uploaded_video_url

    def post_result(self, job_id: str, payload: dict) -> dict:
        self.posted_results.append((job_id, payload))
        return {"job": {"job_id": job_id, "status": payload["status"]}, "video": {}}


def manifest(job_id: str) -> dict:
    return {
        "manifestVersion": "render_worker_v1",
        "jobId": job_id,
        "demoId": "demo-1",
        "jobType": "render_clip",
        "status": "queued",
        "demoFilePath": "/data/uploads/demo-1/source.dem",
        "demoStorageKey": "local://uploads/demo-1/source.dem",
        "originalFilename": "source.dem",
        "mapName": "de_dust2",
        "eventId": "event-1",
        "playerId": "t-entry",
        "povSteamId": None,
        "tickStart": 640,
        "tickEnd": 1280,
        "tickRate": 64,
        "roundNumber": 1,
        "renderPreset": "event_clip_v1",
    }


if __name__ == "__main__":
    unittest.main()
