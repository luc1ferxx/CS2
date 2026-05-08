import json
import sys
import tempfile
import unittest
from pathlib import Path


RENDER_WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(RENDER_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(RENDER_WORKER_ROOT))

from adapters.base import AdapterConfigError
from adapters.cs2_manual import CS2ManualAdapter
from adapters.fake_video import FakeVideoAdapter


class FakeVideoAdapterTest(unittest.TestCase):
    def test_missing_fake_video_posts_failed_callback(self) -> None:
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeVideoAdapter(dev_fake_video_path=Path(directory) / "missing.mp4")

            result = adapter.process(manifest("fake-missing"), client)

        self.assertEqual(result.action, "failed")
        self.assertEqual(client.uploaded_media, [])
        self.assertEqual(len(client.posted_results), 1)
        job_id, payload = client.posted_results[0]
        self.assertEqual(job_id, "fake-missing")
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["videoUrl"])
        self.assertIn("DEV_FAKE_VIDEO_PATH", payload["errorMessage"])

    def test_existing_fake_video_posts_completed_callback(self) -> None:
        client = FakeClient(uploaded_video_url="/media/videos/demo-1/fake-render.mp4")
        with tempfile.TemporaryDirectory() as directory:
            fake_video = Path(directory) / "fake.mp4"
            fake_video.write_bytes(b"fake mp4 bytes")
            adapter = FakeVideoAdapter(dev_fake_video_path=fake_video)

            result = adapter.process(manifest("fake-completed"), client)

        self.assertEqual(result.action, "completed")
        self.assertEqual(client.uploaded_media, [("fake-completed", fake_video)])
        self.assertEqual(len(client.posted_results), 1)
        _, payload = client.posted_results[0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["videoUrl"], "/media/videos/demo-1/fake-render.mp4")
        self.assertEqual(payload["tickStart"], 640)
        self.assertEqual(payload["tickEnd"], 1280)
        self.assertEqual(payload["tickRate"], 64)
        self.assertEqual(payload["durationSeconds"], 10)


class CS2ManualAdapterTest(unittest.TestCase):
    def test_prepare_job_generates_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cs2_dir = root / "cs2"
            steam_dir = root / "steam-userdata"
            work_dir = root / "work"
            cs2_dir.mkdir()
            steam_dir.mkdir()
            adapter = CS2ManualAdapter(
                cs2_install_dir=cs2_dir,
                steam_user_data_dir=steam_dir,
                work_dir=work_dir,
            )

            result = adapter.prepare(manifest("manual-prepare"))

            workspace = work_dir / "jobs" / "manual-prepare"
            self.assertEqual(result.action, "prepared")
            self.assertEqual(result.workspace_path, workspace)
            self.assertTrue((workspace / "manifest.json").exists())
            self.assertTrue((workspace / "instructions.md").exists())
            self.assertTrue((workspace / "expected_output.json").exists())
            self.assertTrue((workspace / "status.json").exists())

            saved_manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
            instructions = (workspace / "instructions.md").read_text(encoding="utf-8")
            expected_output = json.loads((workspace / "expected_output.json").read_text(encoding="utf-8"))
            status = json.loads((workspace / "status.json").read_text(encoding="utf-8"))
            expected_video_path = workspace / "output" / "manual-prepare.mp4"

            self.assertEqual(saved_manifest["jobId"], "manual-prepare")
            self.assertIn("/data/uploads/demo-1/source.dem", instructions)
            self.assertIn("t-entry", instructions)
            self.assertIn("76561190000000001", instructions)
            self.assertIn("tickStart: 640", instructions)
            self.assertIn("tickEnd: 1280", instructions)
            self.assertIn("Open CS2 manually", instructions)
            self.assertIn("Do not automate Steam or CS2", instructions)
            self.assertEqual(expected_output["outputPath"], str(expected_video_path))
            self.assertEqual(expected_output["expectedVideoFilename"], "manual-prepare.mp4")
            self.assertEqual(expected_output["expectedVideoPath"], str(expected_video_path))
            self.assertEqual(expected_output["callbackPayload"]["status"], "completed")
            self.assertEqual(expected_output["callbackPayload"]["tickStart"], 640)
            self.assertEqual(status["state"], "waiting_for_manual_recording")
            self.assertEqual(status["expectedVideoPath"], str(expected_video_path))
            self.assertIn("preparedAt", status)
            self.assertIn("complete-prepared-job", status["nextAction"])

    def test_prepare_job_missing_config_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam_dir = root / "steam-userdata"
            steam_dir.mkdir()
            adapter = CS2ManualAdapter(
                cs2_install_dir=None,
                steam_user_data_dir=steam_dir,
                work_dir=root / "work",
            )

            with self.assertRaisesRegex(AdapterConfigError, "CS2_INSTALL_DIR"):
                adapter.prepare(manifest("manual-missing-config"))

    def test_complete_prepared_job_uses_existing_output_mp4_for_callback(self) -> None:
        client = FakeClient(uploaded_video_url="/media/videos/demo-1/manual-render.mp4")
        with tempfile.TemporaryDirectory() as directory:
            adapter = prepared_manual_adapter(Path(directory), "manual-complete")
            workspace = adapter.job_workspace("manual-complete")
            output_path = workspace / "output" / "manual-complete.mp4"
            output_path.write_bytes(b"manual render bytes")

            result = adapter.complete_prepared_job("manual-complete", client)

        self.assertEqual(result.action, "completed")
        self.assertEqual(client.uploaded_media, [("manual-complete", output_path)])
        self.assertEqual(len(client.posted_results), 1)
        job_id, payload = client.posted_results[0]
        self.assertEqual(job_id, "manual-complete")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["videoUrl"], "/media/videos/demo-1/manual-render.mp4")
        self.assertEqual(payload["tickStart"], 640)
        self.assertEqual(payload["tickEnd"], 1280)
        self.assertEqual(payload["durationSeconds"], 10)

    def test_complete_prepared_job_without_output_mp4_waits_without_callback(self) -> None:
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            adapter = prepared_manual_adapter(Path(directory), "manual-waiting")

            result = adapter.complete_prepared_job("manual-waiting", client)

        self.assertEqual(result.action, "waiting")
        self.assertIn("Output mp4 not found", result.message)
        self.assertEqual(client.uploaded_media, [])
        self.assertEqual(client.posted_results, [])


class FakeClient:
    def __init__(self, uploaded_video_url: str = "/media/videos/demo-1/uploaded.mp4"):
        self.uploaded_video_url = uploaded_video_url
        self.uploaded_media: list[tuple[str, Path]] = []
        self.posted_results: list[tuple[str, dict]] = []

    def upload_media(self, job_id: str, media_path: Path) -> str:
        self.uploaded_media.append((job_id, media_path))
        return self.uploaded_video_url

    def post_result(self, job_id: str, payload: dict) -> dict:
        self.posted_results.append((job_id, payload))
        return {"job": {"job_id": job_id, "status": payload["status"]}, "video": {}}


def prepared_manual_adapter(root: Path, job_id: str) -> CS2ManualAdapter:
    cs2_dir = root / "cs2"
    steam_dir = root / "steam-userdata"
    work_dir = root / "work"
    cs2_dir.mkdir()
    steam_dir.mkdir()
    adapter = CS2ManualAdapter(
        cs2_install_dir=cs2_dir,
        steam_user_data_dir=steam_dir,
        work_dir=work_dir,
    )
    adapter.prepare(manifest(job_id))
    return adapter


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
        "povSteamId": "76561190000000001",
        "tickStart": 640,
        "tickEnd": 1280,
        "tickRate": 64,
        "roundNumber": 1,
        "renderPreset": "event_clip_v1",
    }


if __name__ == "__main__":
    unittest.main()
