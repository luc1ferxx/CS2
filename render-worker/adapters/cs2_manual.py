from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.base import (
    AdapterConfigError,
    AdapterResult,
    RenderWorkerClient,
    completed_payload,
    read_json,
    write_json,
)


class CS2ManualAdapter:
    def __init__(
        self,
        *,
        cs2_install_dir: Path | None,
        steam_user_data_dir: Path | None,
        work_dir: Path,
        output_filename_template: str = "{job_id}.mp4",
    ):
        self.cs2_install_dir = cs2_install_dir
        self.steam_user_data_dir = steam_user_data_dir
        self.work_dir = work_dir
        self.output_filename_template = output_filename_template

    def validate_config(self) -> None:
        missing: list[str] = []
        if self.cs2_install_dir is None:
            missing.append("CS2_INSTALL_DIR")
        if self.steam_user_data_dir is None:
            missing.append("STEAM_USER_DATA_DIR")
        if self.work_dir is None:
            missing.append("WORK_DIR")
        if missing:
            raise AdapterConfigError(f"Missing required manual adapter config: {', '.join(missing)}")

    def job_workspace(self, job_id: str) -> Path:
        return self.work_dir / "jobs" / job_id

    def prepare(self, manifest: dict[str, Any]) -> AdapterResult:
        self.validate_config()
        job_id = str(manifest["jobId"])
        workspace = self.job_workspace(job_id)
        output_path = workspace / "output" / self.output_filename_template.format(job_id=job_id)
        workspace.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        manifest_path = workspace / "manifest.json"
        expected_output_path = workspace / "expected_output.json"
        instructions_path = workspace / "instructions.md"

        write_json(manifest_path, manifest)
        write_json(expected_output_path, build_expected_output(manifest, output_path))
        instructions_path.write_text(
            build_instructions(
                manifest,
                output_path=output_path,
                cs2_install_dir=self.cs2_install_dir,
                steam_user_data_dir=self.steam_user_data_dir,
            ),
            encoding="utf-8",
        )

        return AdapterResult(
            action="prepared",
            job_id=job_id,
            message=f"Prepared manual CS2 render workspace at {workspace}",
            manifest_path=manifest_path,
            workspace_path=workspace,
            output_path=output_path,
        )

    def complete_prepared_job(
        self,
        job_id: str,
        client: RenderWorkerClient,
        *,
        video_path: Path | None = None,
    ) -> AdapterResult:
        workspace = self.job_workspace(job_id)
        expected_output_path = workspace / "expected_output.json"
        manifest_path = workspace / "manifest.json"
        if not expected_output_path.exists() or not manifest_path.exists():
            raise FileNotFoundError(f"Prepared job workspace is missing for {job_id}: {workspace}")

        expected_output = read_json(expected_output_path)
        manifest = read_json(manifest_path)
        output_path = video_path or Path(str(expected_output["outputPath"]))
        if not output_path.exists():
            return AdapterResult(
                action="waiting",
                job_id=job_id,
                message=f"Output mp4 not found at {output_path}. Complete manual recording first.",
                manifest_path=manifest_path,
                workspace_path=workspace,
                output_path=output_path,
            )

        video_url = client.upload_media(job_id, output_path)
        payload = completed_payload(manifest, video_url)
        client.post_result(job_id, payload)
        return AdapterResult(
            action="completed",
            job_id=job_id,
            message=f"Completed manual render callback with media {video_url}",
            manifest_path=manifest_path,
            callback_payload=payload,
            workspace_path=workspace,
            output_path=output_path,
        )


def build_expected_output(manifest: dict[str, Any], output_path: Path) -> dict[str, Any]:
    payload = completed_payload(manifest, video_url="")
    payload["videoUrl"] = None
    payload["localMediaPath"] = str(output_path)
    return {
        "adapter": "cs2-manual",
        "jobId": manifest["jobId"],
        "demoId": manifest["demoId"],
        "outputPath": str(output_path),
        "callbackPayload": payload,
    }


def build_instructions(
    manifest: dict[str, Any],
    *,
    output_path: Path,
    cs2_install_dir: Path | None,
    steam_user_data_dir: Path | None,
) -> str:
    pov = manifest.get("povSteamId") or manifest.get("playerId") or "operator-selected POV"
    round_number = manifest.get("roundNumber")
    return "\n".join(
        [
            "# CS2 Manual Render Instructions",
            "",
            "Do not automate Steam or CS2 from this skeleton.",
            "",
            f"- jobId: {manifest['jobId']}",
            f"- demoId: {manifest['demoId']}",
            f"- mapName: {manifest['mapName']}",
            f"- demoFilePath: {manifest['demoFilePath']}",
            f"- demoStorageKey: {manifest.get('demoStorageKey')}",
            f"- pov: {pov}",
            f"- playerId: {manifest.get('playerId')}",
            f"- povSteamId: {manifest.get('povSteamId')}",
            f"- tickStart: {manifest['tickStart']}",
            f"- tickEnd: {manifest['tickEnd']}",
            f"- tickRate: {manifest['tickRate']}",
            f"- roundNumber: {round_number}",
            f"- renderPreset: {manifest['renderPreset']}",
            f"- cs2InstallDir: {cs2_install_dir}",
            f"- steamUserDataDir: {steam_user_data_dir}",
            f"- recommendedOutputFile: {output_path}",
            "",
            "Manual steps:",
            "",
            "1. Open CS2 manually on the controlled render machine.",
            "2. Load the demo listed above using your normal operator workflow.",
            f"3. Seek to tickStart: {manifest['tickStart']}.",
            f"4. Record the clip through tickEnd: {manifest['tickEnd']}.",
            "5. Export an mp4 file.",
            f"6. Put the mp4 at: {output_path}.",
            "7. Run complete-prepared-job for this job id.",
            "",
        ]
    )
