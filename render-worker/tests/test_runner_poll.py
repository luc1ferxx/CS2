import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.base import AdapterConfigError, UploadedMedia
from runner import RunnerConfig, build_parser, poll, renderer_lock, stop_after_current_job


class StopEvent:
    def __init__(self, stop_after_waits=None):
        self.stopped = False
        self.waits = []
        self.stop_after_waits = stop_after_waits

    def is_set(self): return self.stopped
    def set(self): self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        if self.stop_after_waits is not None and len(self.waits) >= self.stop_after_waits:
            self.set()
        return self.stopped


class QueueClient:
    def __init__(self, jobs=()):
        self.jobs = iter(jobs)
        self.claims = []
        self.results = []

    def fetch_next_manifest(self, *, claim=True):
        self.claims.append(claim)
        return next(self.jobs, None)

    def upload_media(self, job_id, media):
        return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

    def post_result(self, job_id, payload):
        self.results.append((job_id, payload))
        return {}


def job(job_id):
    return {"jobId": job_id, "tickStart": 640, "tickEnd": 1920, "tickRate": 64}


class PollRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = RunnerConfig("http://api.test", "never-print-this-token", self.root, 5)

    def test_idle_queue_is_quiet_and_uses_configured_interval(self):
        stop = StopEvent(stop_after_waits=3)
        client = QueueClient()
        emitted = []
        result = poll(self.config, client=client, stop_event=stop, on_result=emitted.append)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(client.claims, [True, True, True])
        self.assertEqual(stop.waits, [5, 5, 5])
        self.assertEqual([item.action for item in emitted], ["polling"])

    def test_jobs_are_serial_and_stop_finishes_current_callback(self):
        media = self.root / "fake.mp4"
        media.write_bytes(b"fixture media")
        config = RunnerConfig("http://api.test", "token", self.root, 5, dev_fake_video_path=media)
        stop = StopEvent()
        client = QueueClient([job("first"), job("second")])
        emitted = []

        def upload(job_id, _media):
            stop.set()  # Represents Ctrl+C while the first clip is in progress.
            return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

        with patch.object(client, "upload_media", side_effect=upload):
            result = poll(config, client=client, stop_event=stop, on_result=emitted.append)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(len(client.claims), 1)
        self.assertEqual(client.results[0][0], "first")
        self.assertEqual(client.results[0][1]["status"], "completed")
        self.assertEqual([item.action for item in emitted], ["polling", "completed"])

    def test_failed_job_does_not_block_next_job(self):
        stop = StopEvent(stop_after_waits=2)
        client = QueueClient([job("first"), job("second")])
        emitted = []
        poll(self.config, client=client, stop_event=stop, on_result=emitted.append)
        self.assertEqual([item[0] for item in client.results], ["first", "second"])
        self.assertEqual([item.action for item in emitted], ["polling", "failed", "failed"])

    def test_existing_game_blocks_claim_without_repeating_status(self):
        config = RunnerConfig("http://api.test", "token", self.root, 5, adapter="csdm")
        stop = StopEvent(stop_after_waits=3)
        client = QueueClient()
        emitted = []
        with patch("runner.csdm_adapter", return_value=SimpleNamespace(config=SimpleNamespace(validate=lambda: None))), patch(
            "runner.assert_game_not_running", side_effect=AdapterConfigError("CS2 is already running")
        ):
            poll(config, client=client, stop_event=stop, on_result=emitted.append)
        self.assertEqual(client.claims, [])
        self.assertEqual([item.action for item in emitted], ["polling", "waiting"])

    def test_api_failure_backoff_does_not_print_raw_error_or_token(self):
        stop = StopEvent(stop_after_waits=5)
        client = QueueClient()
        emitted = []
        with patch.object(client, "fetch_next_manifest", side_effect=RuntimeError("secret backend exception")):
            poll(self.config, client=client, stop_event=stop, on_result=emitted.append)
        self.assertEqual(stop.waits, [10, 20, 40, 60, 60])
        self.assertEqual([item.action for item in emitted], ["polling", "waiting"])
        self.assertNotIn("secret backend exception", str(emitted))
        self.assertNotIn("never-print-this-token", str(emitted))

    def test_poll_retains_exclusive_lock_while_idle_and_releases_after_stop(self):
        stop = StopEvent(stop_after_waits=1)
        client = QueueClient()

        def fetch(*, claim):
            with self.assertRaises(AdapterConfigError):
                with renderer_lock(self.root):
                    self.fail("another consumer acquired the same worker")
            return None

        with patch.object(client, "fetch_next_manifest", side_effect=fetch):
            poll(self.config, client=client, stop_event=stop, on_result=lambda _: None)
        with renderer_lock(self.root):
            pass

    def test_sigint_handler_requests_stop_and_restores_previous_handler(self):
        stop = threading.Event()
        registered = []
        original = object()
        with patch("runner.signal.getsignal", return_value=original), patch("runner.signal.signal", side_effect=lambda sig, handler: registered.append((sig, handler))):
            with stop_after_current_job(stop):
                registered[0][1](None, None)
                self.assertTrue(stop.is_set())
        self.assertIs(registered[-1][1], original)

    def test_poll_command_is_available_and_invalid_intervals_do_not_claim(self):
        self.assertEqual(build_parser().parse_args(["poll"]).command, "poll")
        client = QueueClient()
        config = RunnerConfig("http://api.test", "token", self.root, 0)
        with self.assertRaises(AdapterConfigError):
            poll(config, client=client)
        self.assertEqual(client.claims, [])

    def test_existing_stop_file_prevents_claims_and_is_never_deleted(self):
        flag = self.root / "renderer.stop"
        flag.write_text("operator stop", encoding="utf-8")
        config = RunnerConfig("http://api.test", "token", self.root, 5, stop_file=flag)
        client = QueueClient([job("first")])
        result = poll(config, client=client, on_result=lambda _: None)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(client.claims, [])
        self.assertEqual(flag.read_text(encoding="utf-8"), "operator stop")
        self.assertEqual(build_parser().parse_args(["--stop-file", str(flag), "poll"]).stop_file, str(flag))

    def test_stop_file_created_during_upload_finishes_callback_without_claiming_next(self):
        flag = self.root / "renderer.stop"
        media = self.root / "fake.mp4"
        media.write_bytes(b"fixture media")
        config = RunnerConfig("http://api.test", "token", self.root, 5, dev_fake_video_path=media, stop_file=flag)
        client = QueueClient([job("first"), job("second")])

        def upload(job_id, _media):
            flag.write_text("stop after current clip", encoding="utf-8")
            return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

        with patch.object(client, "upload_media", side_effect=upload):
            result = poll(config, client=client, on_result=lambda _: None)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(len(client.claims), 1)
        self.assertEqual(client.results[0][1]["status"], "completed")
        self.assertEqual(flag.read_text(encoding="utf-8"), "stop after current clip")


if __name__ == "__main__":
    unittest.main()
