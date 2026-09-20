from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from adapters.base import (
    AdapterConfigError,
    AdapterResult,
    RenderWorkerClient,
    completed_payload,
    failed_payload,
    write_json,
)


class CSDMClient(RenderWorkerClient, Protocol):
    def download_source(self, manifest: dict[str, Any], destination: Path) -> None: ...


class RenderError(RuntimeError):
    """A safe operator-facing message; command output remains in local logs."""


@dataclass(frozen=True)
class CSDMConfig:
    executable: Path | None
    ffmpeg_executable: Path | None
    ffprobe_executable: Path | None
    timeout_seconds: int = 1800
    cli_entrypoint: Path | None = None
    recording_system: str = "HLAE"
    cs2_install_dir: Path | None = None

    def validate(self) -> None:
        if self.recording_system not in {"HLAE", "CS"}:
            raise AdapterConfigError("CSDM_RECORDING_SYSTEM must be HLAE or CS")
        if self.recording_system == "CS":
            assert_native_capture_workspace_empty(self.cs2_install_dir)
        for name, path in (
            ("CSDM_EXECUTABLE", self.executable),
            ("FFMPEG_EXECUTABLE", self.ffmpeg_executable),
            ("FFPROBE_EXECUTABLE", self.ffprobe_executable),
        ):
            if path is None or not path.is_absolute() or not path.is_file():
                raise AdapterConfigError(f"{name} must be an absolute path to an installed executable")
            if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
                raise AdapterConfigError(f"{name} must be an executable, not a shell script")
        if not 30 <= self.timeout_seconds <= 7200:
            raise AdapterConfigError("CSDM_TIMEOUT_SECONDS must be between 30 and 7200")
        if self.cli_entrypoint is not None:
            entrypoint = self.cli_entrypoint
            # Electron resolves files inside app.asar even though Windows cannot stat them.
            in_asar = entrypoint.name == "cli.js" and entrypoint.parent.suffix == ".asar" and entrypoint.parent.is_file()
            if not entrypoint.is_absolute() or not (entrypoint.is_file() or in_asar):
                raise AdapterConfigError("CSDM_CLI_ENTRYPOINT must point to CSDM's installed cli.js")

    def command_prefix(self) -> list[str]:
        prefix = [str(self.executable)]
        if self.cli_entrypoint is not None:
            prefix.append(str(self.cli_entrypoint))
        return prefix

    def command_env(self) -> dict[str, str] | None:
        return {**os.environ, "ELECTRON_RUN_AS_NODE": "1"} if self.cli_entrypoint else None


def safe_job_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise AdapterConfigError("Invalid render job identifier")
    return value


def clear_previous_attempt(workspace: Path) -> None:
    """Remove the workspace a previous attempt at this job left behind.

    A retried or reclaimed job keeps its id, and the workspace is keyed by that
    id, so arriving at an existing directory is normal rather than suspicious.
    Only an ordinary directory is removed: anything else was not left here by a
    previous attempt, and descending into it could delete files outside WORK_DIR.
    """
    if not workspace.exists():
        return
    if workspace.is_symlink() or workspace.is_junction() or not workspace.is_dir():
        raise RenderError("Render job workspace exists but is not an ordinary directory")
    shutil.rmtree(workspace)


def assert_native_capture_workspace_empty(install_dir: Path | None) -> None:
    if install_dir is None or not install_dir.is_absolute() or not (install_dir / "game/bin/win64/cs2.exe").is_file():
        raise AdapterConfigError("Native CS recording requires CS2_INSTALL_DIR pointing to the installed game")
    for relative in ("game/csgo/movie", "game/csgo/csdm/movie"):
        capture_dir = install_dir / relative
        if not capture_dir.resolve().is_relative_to(install_dir.resolve()) or capture_dir.is_symlink() or capture_dir.is_junction():
            raise AdapterConfigError("Native CS recording requires ordinary capture directories inside CS2_INSTALL_DIR")
        if capture_dir.exists() and (not capture_dir.is_dir() or any(capture_dir.iterdir())):
            # CSDM deletes both directories at the beginning and end of native recording.
            raise AdapterConfigError("Existing CS2 movie capture files must be preserved elsewhere before native recording")


def validate_manifest(manifest: dict[str, Any]) -> None:
    job_id = safe_job_id(manifest.get("jobId"))
    if manifest.get("jobType") != "render_clip":
        raise AdapterConfigError("CSDM supports only render_clip jobs")
    if not re.fullmatch(r"7656[0-9]{13}", str(manifest.get("povSteamId", ""))):
        raise AdapterConfigError("Render job requires the selected player's SteamID64")
    for key in ("tickStart", "tickEnd", "tickRate"):
        if type(manifest.get(key)) is not int:
            raise AdapterConfigError("Render job requires integer tick bounds and tick rate")
    start, end, rate = (manifest[key] for key in ("tickStart", "tickEnd", "tickRate"))
    if start < 96 or end <= start or not 1 <= rate <= 256 or (end - start) / rate > 60:
        raise AdapterConfigError("CSDM requires a tick range starting at tick 96 or later and lasting at most 60 seconds")
    if manifest.get("demoDownloadPath") != f"/render-worker/jobs/{job_id}/source":
        raise AdapterConfigError("Render job is missing its authenticated source download path")
    if type(manifest.get("sourceSizeBytes")) is not int or manifest["sourceSizeBytes"] <= 0:
        raise AdapterConfigError("Render job is missing its accepted source size")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", str(manifest.get("sourceSha256", ""))):
        raise AdapterConfigError("Render job is missing its accepted source SHA256")


def run_command(args: list[str], log_path: Path, timeout_seconds: int, env: dict[str, str] | None = None) -> None:
    """No shell and no process-name termination; only interrupt the command we own."""
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log_path.open("wb") as log_file:
        process = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT, env=env, **options)
        try:
            result = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            # CSDM aborts its queued video on Ctrl+C where a console is available.
            # Never kill an unrelated Steam/game process by name.
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=10)
            raise RenderError("CSDM command timed out; inspect local logs and close any remaining recording before retrying") from exc
    if result != 0:
        raise RenderError(f"CSDM command failed (exit {result}); inspect the local command log")


def assert_game_not_running() -> None:
    if os.name != "nt":
        return
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run(
            [str(tasklist), "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdapterConfigError("Cannot verify whether CS2 is already running") from exc
    if b'"cs2.exe"' in result.stdout.lower():
        raise AdapterConfigError("CS2 is already running; close it before starting the operator renderer")


def probe_video(ffprobe: Path, media: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
             "format=duration,format_name,start_time:stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,duration,start_time,nb_read_frames",
             "-of", "json", str(media)],
            capture_output=True, timeout=120, check=True,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        report = json.loads(result.stdout)
        videos = [item for item in report["streams"] if item.get("codec_type") == "video"]
        if len(videos) != 1:
            raise ValueError("video stream count")
        video = videos[0]
        duration = float(video["duration"])
        container_duration = float(report["format"]["duration"])
        start_time = float(video.get("start_time", 0))
        frame_count = int(video["nb_read_frames"])
        fps_numerator, fps_denominator = video["avg_frame_rate"].split("/")
        fps = float(fps_numerator) / float(fps_denominator)
        expected = (manifest["tickEnd"] - manifest["tickStart"]) / manifest["tickRate"]
        tolerance = max(2 / 30, 2 / manifest["tickRate"])
        if not all(math.isfinite(value) for value in (duration, container_duration, start_time, fps)):
            raise ValueError("non-finite timing")
        if (
            video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p"
            or (video.get("width"), video.get("height")) != (1280, 720)
            or "mp4" not in report["format"].get("format_name", "")
            or abs(fps - 30) > 0.01 or abs(start_time) > 1 / 30
            or abs(duration - expected) > tolerance
            or abs(frame_count / 30 - expected) > tolerance
            or not duration - tolerance <= container_duration <= expected + 0.5
        ):
            raise ValueError("media does not match requested tick range or encoding")
        return {"durationSeconds": container_duration, "videoDurationSeconds": duration,
                "frameCount": frame_count, "frameRate": fps, "timeOriginSeconds": 0,
                "calibrationBasis": "csdm_tick_start_stop", "ffprobe": report}
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise RenderError("Rendered MP4 failed duration, frame, or encoding verification; no video was published") from exc


class CSDMAdapter:
    def __init__(self, config: CSDMConfig, work_dir: Path):
        self.config = config
        self.work_dir = work_dir.resolve()

    def video_command(self, manifest: dict[str, Any], source: Path, output: Path) -> list[str]:
        # Custom output parameters replace CSDM's native image encoder defaults.
        # Explicit yuv420p is necessary for browser playback of the TGA fallback.
        ffmpeg_output = "-crf 23 -movflags +faststart"
        if self.config.recording_system == "CS":
            ffmpeg_output = "-pix_fmt yuv420p " + ffmpeg_output
        return self.config.command_prefix() + [
            "video", str(source), str(manifest["tickStart"]), str(manifest["tickEnd"]),
            "--focus-player", manifest["povSteamId"], "--recording-system", self.config.recording_system,
            "--encoder-software", "FFmpeg", "--recording-output", "video",
            "--ffmpeg-executable-path", str(self.config.ffmpeg_executable),
            "--ffmpeg-video-container", "mp4", "--ffmpeg-video-codec", "libx264",
            "--ffmpeg-audio-codec", "aac", "--ffmpeg-crf", "23",
            "--ffmpeg-input-parameters=", f"--ffmpeg-output-parameters={ffmpeg_output}",
            "--framerate", "30", "--width", "1280", "--height", "720",
            "--no-show-x-ray", "--no-true-view", "--no-show-only-death-notices", "--record-audio", "--no-player-voices",
            "--no-concatenate-sequences", "--close-game-after-recording", "--output", str(output),
        ]

    def build_plan(self, manifest: dict[str, Any], work_dir: Path) -> dict[str, Any]:
        validate_manifest(manifest)
        workspace = work_dir.resolve() / "jobs" / safe_job_id(manifest["jobId"])
        return {"adapter": "csdm", "recordingSystem": self.config.recording_system,
                "jobId": manifest["jobId"], "povSteamId": manifest["povSteamId"],
                "sourceDownloadPath": manifest["demoDownloadPath"], "workDir": str(workspace),
                "analyzeCommand": self.config.command_prefix() + ["analyze", str(workspace / "source.dem")],
                "videoCommand": self.video_command(manifest, workspace / "source.dem", workspace / "output"),
                "plannedAction": "Download verified demo, analyze in CSDM, render a tick-bounded player POV and verify MP4"}

    def process(self, manifest: dict[str, Any], client: CSDMClient, *, manifest_path: Path | None = None) -> AdapterResult:
        job_id = safe_job_id(manifest["jobId"])
        workspace = self.work_dir / "jobs" / job_id
        output = workspace / "output"
        created_workspace = False
        try:
            validate_manifest(manifest)
            self.config.validate()
            assert_game_not_running()
            # Fresh output avoids attaching an old clip after a failed recording,
            # and lets a retry of this same job id start over instead of failing
            # on its own leftovers.
            clear_previous_attempt(workspace)
            workspace.mkdir(parents=True, exist_ok=False)
            created_workspace = True
            output.mkdir()
            write_json(workspace / "manifest.json", manifest)
            source = workspace / "source.dem"
            write_json(workspace / "status.json", {"state": "downloading"})
            client.download_source(manifest, source)
            write_json(workspace / "status.json", {"state": "analyzing"})
            run_command(self.config.command_prefix() + ["analyze", str(source)], workspace / "analyze.log", self.config.timeout_seconds, self.config.command_env())
            # Recheck after analysis, immediately before CSDM can replace native scratch data.
            self.config.validate()
            assert_game_not_running()
            write_json(workspace / "status.json", {"state": "recording"})
            run_command(self.video_command(manifest, source, output), workspace / "record.log", self.config.timeout_seconds, self.config.command_env())
            filename = f"sequence-1-tick-{manifest['tickStart']}-to-{manifest['tickEnd']}.mp4"
            candidates = [path for path in output.rglob(filename) if path.is_file() and not path.is_symlink()]
            if len(candidates) != 1 or not candidates[0].resolve().is_relative_to(output.resolve()):
                raise RenderError("CSDM did not produce exactly one matching sequence MP4")
            media = candidates[0]
            verification = probe_video(self.config.ffprobe_executable, media, manifest)
            write_json(workspace / "media-verification.json", verification)
            write_json(workspace / "status.json", {"state": "uploading"})
            uploaded = client.upload_media(job_id, media)
        except (AdapterConfigError, RenderError, OSError, RuntimeError) as exc:
            message = str(exc) if isinstance(exc, (AdapterConfigError, RenderError)) else "Local rendering failed; inspect the operator workspace and service status"
            payload = failed_payload(manifest, message)
            if created_workspace:
                write_json(workspace / "status.json", {"state": "failed", "message": message})
            client.post_result(job_id, payload)
            return AdapterResult("failed", job_id, message, manifest_path, payload, workspace)
        payload = completed_payload(manifest, uploaded.video_url, uploaded.storage_key)
        payload["durationSeconds"] = verification["durationSeconds"]
        payload["timeOriginSeconds"] = verification["timeOriginSeconds"]
        # An uncertain completed callback must not be followed by a contradictory failed callback.
        client.post_result(job_id, payload)
        write_json(workspace / "status.json", {"state": "completed", "videoPath": str(media)})
        return AdapterResult("completed", job_id, "Verified CSDM player clip uploaded and attached", manifest_path, payload, workspace, media)
