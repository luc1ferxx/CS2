import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("render_worker_runner_media_upload", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RenderWorkerMediaUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner()

    def test_upload_media_streams_multiple_chunks_without_reading_the_whole_file(self) -> None:
        captured: dict[str, object] = {}
        response_payload = {
            "videoUrl": "/demos/demo-1/media/video",
            "storageKey": "artifact://v1/accepted/video/owner/demo/artifact",
        }

        def fake_urlopen(request, timeout):
            chunks = list(request.data)
            captured.update(request=request, timeout=timeout, chunks=chunks)
            return FakeResponse(200, response_payload)

        with tempfile.TemporaryDirectory() as directory:
            media_path = Path(directory) / "render.mp4"
            media_bytes = b"a" * (2 * 1024 * 1024 + 257)
            media_path.write_bytes(media_bytes)
            config = self.runner.RunnerConfig(
                api_base_url="https://api.example.test",
                render_worker_token="worker-token",
                work_dir=Path(directory),
                poll_interval_seconds=5,
            )
            client = self.runner.RenderWorkerApiClient(config)

            with (
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("media upload must not call Path.read_bytes"),
                ),
                patch.object(client, "_open_request", side_effect=fake_urlopen),
            ):
                uploaded = client.upload_media("render-job-1", media_path)

        self.assertEqual(uploaded.video_url, response_payload["videoUrl"])
        self.assertEqual(uploaded.storage_key, response_payload["storageKey"])
        self.assertEqual(captured["timeout"], 120)

        request = captured["request"]
        self.assertEqual(request.full_url, "https://api.example.test/render-worker/jobs/render-job-1/media")
        self.assertEqual(request.get_method(), "POST")
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["x-render-worker-token"], "worker-token")
        self.assertTrue(headers["content-type"].startswith("multipart/form-data; boundary="))

        chunks = captured["chunks"]
        self.assertGreaterEqual(len(chunks), 5)
        self.assertEqual(int(headers["content-length"]), sum(len(chunk) for chunk in chunks))
        self.assertIn(
            b'Content-Disposition: form-data; name="file"; filename="render.mp4"',
            chunks[0],
        )
        self.assertEqual(b"".join(chunks[1:-1]), media_bytes)
        self.assertTrue(chunks[-1].endswith(b"--\r\n"))


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


if __name__ == "__main__":
    unittest.main()
