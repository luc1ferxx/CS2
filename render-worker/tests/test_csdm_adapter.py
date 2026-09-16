import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import NoRedirectHandler, RenderWorkerApiClient, RunnerConfig, csdm_adapter, process_job, renderer_lock

from adapters.base import AdapterConfigError, UploadedMedia
from adapters.csdm import (
    CSDMAdapter,
    CSDMConfig,
    RenderError,
    assert_native_capture_workspace_empty,
    probe_video,
    run_command,
    validate_manifest,
)


def manifest():
    return {
        "jobId": "clip-1", "demoId": "demo-1", "jobType": "render_clip", "status": "rendering",
        "tickStart": 640, "tickEnd": 1920, "tickRate": 64, "povSteamId": "76561198998266210",
        "demoDownloadPath": "/render-worker/jobs/clip-1/source", "sourceSizeBytes": 4,
        "sourceSha256": hashlib.sha256(b"demo").hexdigest(),
    }


def probe_report(duration="20.000", frames="600"):
    return {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                         "pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "duration": duration,
                         "start_time": "0.000", "nb_read_frames": frames}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "20.033"}}


class Client:
    def __init__(self):
        self.downloads, self.uploads, self.results, self.claims = [], [], [], []

    def fetch_manifest(self, job_id, claim=True):
        self.claims.append(claim)
        return manifest()

    def download_source(self, job, destination):
        self.downloads.append(destination)
        destination.write_bytes(b"demo")

    def upload_media(self, job_id, path):
        self.uploads.append(path)
        return UploadedMedia("/demos/demo-1/media/video", "artifact://video/output")

    def post_result(self, job_id, payload):
        self.results.append(payload)
        return {}


class CSDMAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / "tool.exe"
        self.executable.touch()
        self.config = CSDMConfig(self.executable, self.executable, self.executable)
        self.adapter = CSDMAdapter(self.config, self.root / "work")

    def test_manifest_rejects_wrong_source_binding_invalid_pov_and_early_ticks(self):
        for changes in (
            {"demoDownloadPath": "https://evil.test/source"}, {"demoDownloadPath": "/render-worker/jobs/other/source"},
            {"jobId": "../../escaped"}, {"povSteamId": None}, {"povSteamId": "xelex"}, {"tickStart": 95},
            {"tickRate": 0}, {"tickEnd": 10000}, {"sourceSha256": "unknown"}, {"sourceSizeBytes": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(AdapterConfigError):
                validate_manifest({**manifest(), **changes})

    def test_electron_cli_is_argument_array_and_env_not_shell_script(self):
        archive = self.root / "app.asar"
        archive.touch()
        config = CSDMConfig(self.executable, self.executable, self.executable, cli_entrypoint=archive / "cli.js")
        config.validate()
        adapter = CSDMAdapter(config, self.root / "work")
        command = adapter.video_command(manifest(), self.root / "has spaces.dem", self.root / "out")
        self.assertEqual(command[:3], [str(self.executable), str(archive / "cli.js"), "video"])
        self.assertEqual(command[command.index("--focus-player") + 1], "76561198998266210")
        self.assertEqual(command[command.index("--recording-system") + 1], "HLAE")
        self.assertIn("--no-show-only-death-notices", command)
        self.assertIn("--no-show-x-ray", command)
        self.assertEqual(config.command_env()["ELECTRON_RUN_AS_NODE"], "1")
        bad_script = self.root / "csdm.cmd"
        bad_script.touch()
        with self.assertRaises(AdapterConfigError):
            CSDMConfig(bad_script, self.executable, self.executable).validate()

    def test_native_cs_config_reaches_cli_and_preserves_browser_pixel_format(self):
        with patch.dict("os.environ", {"CSDM_RECORDING_SYSTEM": "CS"}):
            config = RunnerConfig.from_env()
        adapter = csdm_adapter(config)
        command = adapter.video_command(manifest(), self.root / "source.dem", self.root / "output")
        self.assertEqual(command[command.index("--recording-system") + 1], "CS")
        self.assertEqual(command[command.index("--recording-output") + 1], "video")
        output_parameters = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--ffmpeg-output-parameters="))
        self.assertIn("-pix_fmt yuv420p", output_parameters)
        self.assertEqual(adapter.build_plan(manifest(), self.root)["recordingSystem"], "CS")
        with self.assertRaises(AdapterConfigError):
            CSDMConfig(self.executable, self.executable, self.executable, recording_system="automatic").validate()

    def test_native_capture_refuses_existing_recordings_without_claim_or_deletion(self):
        install = self.root / "CS2"
        game_exe = install / "game/bin/win64/cs2.exe"
        game_exe.parent.mkdir(parents=True)
        game_exe.touch()
        assert_native_capture_workspace_empty(install)
        for relative in ("game/csgo/movie", "game/csgo/csdm/movie"):
            folder = install / relative
            folder.mkdir(parents=True)
            assert_native_capture_workspace_empty(install)
            original = folder / "existing.tga"
            original.write_bytes(b"user recording")
            config = RunnerConfig("http://api.test", "token", self.root, 5, adapter="csdm",
                                  csdm_executable=self.executable, ffmpeg_executable=self.executable,
                                  ffprobe_executable=self.executable, csdm_recording_system="CS",
                                  cs2_install_dir=install)
            client = Client()
            with self.assertRaises(AdapterConfigError):
                process_job(config, "clip-1", client=client)
            self.assertEqual(client.claims, [])
            self.assertEqual(original.read_bytes(), b"user recording")
            original.unlink()

    def test_native_recording_requires_installed_cs2_path(self):
        for path in (None, self.root / "missing", Path("relative")):
            with self.subTest(path=path), self.assertRaises(AdapterConfigError):
                assert_native_capture_workspace_empty(path)

    @unittest.skipUnless(shutil.which("node"), "Node is required to exercise CSDM's argument parser")
    def test_csdm_node_parser_accepts_ffmpeg_options_beginning_with_dash(self):
        args = self.adapter.video_command(manifest(), self.root / "source.dem", self.root / "output")[1:]
        script = (
            "const {parseArgs}=require('node:util');"
            "const result=parseArgs({args:JSON.parse(process.argv[1]),strict:false,allowPositionals:true,"
            "options:{'ffmpeg-input-parameters':{type:'string'},'ffmpeg-output-parameters':{type:'string'}}});"
            "process.stdout.write(JSON.stringify(result.values));"
        )
        result = subprocess.run([shutil.which("node"), "-e", script, json.dumps(args)], capture_output=True, check=True, timeout=10)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["ffmpeg-output-parameters"], "-crf 23 -movflags +faststart")
        self.assertEqual(parsed["ffmpeg-input-parameters"], "")

    def test_duration_frame_count_codec_resolution_must_match_clip(self):
        for update in (
            {"duration": "23.0"}, {"nb_read_frames": "500"}, {"avg_frame_rate": "60/1"},
            {"duration": "nan"}, {"start_time": "2"}, {"width": 1920}, {"codec_name": "hevc"},
        ):
            report = probe_report()
            report["streams"][0].update(update)
            with self.subTest(update=update), patch("adapters.csdm.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(report).encode())):
                with self.assertRaises(RenderError):
                    probe_video(self.executable, self.root / "clip.mp4", manifest())

    @patch("adapters.csdm.assert_game_not_running")
    def test_real_flow_verifies_matching_final_sequence_before_upload(self, _game):
        client = Client()
        commands = []

        def command(args, log, timeout, env=None):
            commands.append(args)
            if "video" in args:
                output = Path(args[args.index("--output") + 1]) / "csdm-video-id"
                output.mkdir()
                (output / "sequence-1-tick-640-to-1920.mp4").write_bytes(b"test media")
                (output / "video.mp4").write_bytes(b"raw temporary media")

        with patch("adapters.csdm.run_command", side_effect=command), patch(
            "adapters.csdm.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(probe_report()).encode())
        ):
            result = self.adapter.process(manifest(), client)
        self.assertEqual(result.action, "completed")
        self.assertEqual(commands[0][1], "analyze")
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(client.uploads[0].name, "sequence-1-tick-640-to-1920.mp4")
        self.assertEqual(client.results[0]["durationSeconds"], 20.033)
        self.assertEqual(client.results[0]["timeOriginSeconds"], 0)
        self.assertEqual(client.results[0]["storageKey"], "artifact://video/output")
        self.assertEqual(json.loads((result.workspace_path / "status.json").read_text())["state"], "completed")

    @patch("adapters.csdm.assert_game_not_running")
    def test_render_timeout_posts_failure_without_upload_or_reusing_media(self, _game):
        client = Client()
        with patch("adapters.csdm.run_command", side_effect=RenderError("CSDM command timed out")):
            result = self.adapter.process(manifest(), client)
        self.assertEqual(result.action, "failed")
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.results[0]["status"], "failed")

    @patch("adapters.csdm.assert_game_not_running")
    def test_stale_workspace_cannot_publish_old_mp4(self, _game):
        output = self.root / "work" / "jobs" / "clip-1" / "output"
        output.mkdir(parents=True)
        (output / "sequence-1-tick-640-to-1920.mp4").write_bytes(b"old")
        client = Client()
        result = self.adapter.process(manifest(), client)
        self.assertEqual(result.action, "failed")
        self.assertEqual(client.downloads, [])
        self.assertEqual(client.uploads, [])
        self.assertEqual((output / "sequence-1-tick-640-to-1920.mp4").read_bytes(), b"old")

    def test_missing_configuration_does_not_claim_job(self):
        config = RunnerConfig("http://api.test", "token", self.root, 5, adapter="csdm")
        client = Client()
        with self.assertRaises(AdapterConfigError):
            process_job(config, "clip-1", client=client)
        self.assertEqual(client.claims, [])

    def test_existing_game_does_not_claim_job(self):
        config = RunnerConfig("http://api.test", "token", self.root, 5, adapter="csdm",
                              csdm_executable=self.executable, ffmpeg_executable=self.executable,
                              ffprobe_executable=self.executable)
        client = Client()
        with patch("runner.assert_game_not_running", side_effect=AdapterConfigError("CS2 is already running")):
            with self.assertRaises(AdapterConfigError):
                process_job(config, "clip-1", client=client)
        self.assertEqual(client.claims, [])

    @patch("adapters.csdm.assert_game_not_running")
    def test_wrong_duration_is_not_uploaded(self, _game):
        client = Client()

        def command(args, log, timeout, env=None):
            if "video" in args:
                output = Path(args[args.index("--output") + 1])
                (output / "sequence-1-tick-640-to-1920.mp4").write_bytes(b"wrong duration")

        with patch("adapters.csdm.run_command", side_effect=command), patch(
            "adapters.csdm.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(probe_report(duration="25", frames="750")).encode())
        ):
            result = self.adapter.process(manifest(), client)
        self.assertEqual(result.action, "failed")
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.results[0]["status"], "failed")

    @patch("adapters.csdm.assert_game_not_running")
    def test_uncertain_completed_callback_does_not_send_failed_callback(self, _game):
        client = Client()

        def command(args, log, timeout, env=None):
            if "video" in args:
                output = Path(args[args.index("--output") + 1])
                (output / "sequence-1-tick-640-to-1920.mp4").write_bytes(b"media")

        with patch("adapters.csdm.run_command", side_effect=command), patch(
            "adapters.csdm.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(probe_report()).encode())
        ), patch.object(client, "post_result", side_effect=RuntimeError("connection lost")) as callback:
            with self.assertRaises(RuntimeError):
                self.adapter.process(manifest(), client)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(callback.call_args.args[1]["status"], "completed")

    def test_single_renderer_lock_conflicts_then_releases(self):
        with renderer_lock(self.root):
            with self.assertRaises(AdapterConfigError):
                with renderer_lock(self.root):
                    self.fail("second lock acquired")
        with renderer_lock(self.root):
            pass

    def test_run_command_times_out_only_owned_process(self):
        with self.assertRaises(RenderError):
            run_command([sys.executable, "-c", "import time;time.sleep(0.2)"], self.root / "command.log", 0.05)


class Response:
    def __init__(self, chunks, job):
        self.status = 200
        self.chunks = iter(chunks)
        self.headers = {"Content-Length": str(job["sourceSizeBytes"]), "X-Content-SHA256": job["sourceSha256"]}

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return next(self.chunks, b"")


class SourceDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = RenderWorkerApiClient(RunnerConfig("http://api.test", "token", self.root, 5))

    def test_download_streams_verifies_and_uses_only_job_route(self):
        job = manifest()
        with patch("runner.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = Response([b"de", b"mo"], job)
            destination = self.root / "source.dem"
            self.client.download_source(job, destination)
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://api.test/render-worker/jobs/clip-1/source")
        self.assertEqual(request.get_header("X-render-worker-token"), "token")
        self.assertEqual(destination.read_bytes(), b"demo")
        self.assertFalse(destination.with_suffix(".dem.part").exists())

    def test_checksum_truncated_and_oversized_downloads_leave_no_source(self):
        for chunks in ([b"xxxx"], [b"de"], [b"demo", b"more"]):
            with self.subTest(chunks=chunks), patch("runner.urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = Response(chunks, manifest())
                destination = self.root / "source.dem"
                with self.assertRaises(RuntimeError):
                    self.client.download_source(manifest(), destination)
                self.assertFalse(destination.exists())
                self.assertFalse(destination.with_suffix(".dem.part").exists())

    def test_wrong_header_and_limit_rejected_before_writing(self):
        response = Response([b"demo"], manifest())
        response.headers["X-Content-SHA256"] = "0" * 64
        with patch("runner.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(RuntimeError):
                self.client.download_source(manifest(), self.root / "source.dem")
        with self.assertRaises(RuntimeError):
            self.client.download_source({**manifest(), "sourceSizeBytes": 3 * 1024**3}, self.root / "source.dem")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_token_requests_disable_redirects(self):
        handler = NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.test/"))
        with patch("runner.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError("http://api.test", 302, "Found", {}, None)
            with self.assertRaises(RuntimeError):
                self.client.fetch_manifest("clip-1")
            self.assertIsInstance(opener.call_args.args[0], NoRedirectHandler)


if __name__ == "__main__":
    unittest.main()
