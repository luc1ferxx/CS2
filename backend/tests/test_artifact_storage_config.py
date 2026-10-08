import base64
import os
import subprocess
import sys
import unittest
from pathlib import Path

from app.core.config import Settings


class ArtifactStorageConfigurationTest(unittest.TestCase):
    def test_development_can_use_the_explicit_local_adapter(self) -> None:
        Settings(
            auth_mode="development",
            artifact_storage_backend="local",
        ).validate_runtime_configuration()

    def test_production_rejects_local_artifact_storage(self) -> None:
        values = production_values()
        values["artifact_storage_backend"] = "local"

        with self.assertRaisesRegex(RuntimeError, "ARTIFACT_STORAGE_BACKEND"):
            Settings(**values).validate_runtime_configuration()

    def test_production_object_storage_requires_private_namespace_configuration(self) -> None:
        for override, expected in (
            ({"object_storage_bucket": ""}, "OBJECT_STORAGE_BUCKET"),
            ({"object_storage_bucket": "bad/bucket"}, "OBJECT_STORAGE_BUCKET"),
            ({"object_storage_prefix": ""}, "OBJECT_STORAGE_PREFIX"),
            ({"object_storage_prefix": "../escape"}, "OBJECT_STORAGE_PREFIX"),
            ({"object_storage_endpoint_url": "http://objects.example.test"}, "OBJECT_STORAGE_ENDPOINT_URL"),
        ):
            with self.subTest(override=override):
                values = production_values()
                values.update(override)
                with self.assertRaisesRegex(RuntimeError, expected):
                    Settings(**values).validate_runtime_configuration()

    def test_object_storage_credentials_are_optional_but_must_be_a_pair(self) -> None:
        values = production_values()
        values["object_storage_access_key_id"] = "access-only"

        with self.assertRaisesRegex(RuntimeError, "credentials"):
            Settings(**values).validate_runtime_configuration()

        Settings(**production_values()).validate_runtime_configuration()

    def test_production_rejects_unbounded_or_invalid_artifact_limits(self) -> None:
        invalid = (
            ({"artifact_quarantine_ttl_seconds": 0}, "ARTIFACT_QUARANTINE_TTL_SECONDS"),
            ({"max_demo_upload_bytes": 15}, "MAX_DEMO_UPLOAD_BYTES"),
            ({"max_demo_upload_bytes": 1024 * 1024 * 1024 + 1}, "MAX_DEMO_UPLOAD_BYTES"),
            ({"max_video_upload_bytes": 0}, "MAX_VIDEO_UPLOAD_BYTES"),
            ({"max_video_upload_bytes": 2 * 1024 * 1024 * 1024 + 1}, "MAX_VIDEO_UPLOAD_BYTES"),
            ({"max_replay_artifact_bytes": 0}, "MAX_REPLAY_ARTIFACT_BYTES"),
            ({"max_replay_artifact_bytes": 128 * 1024 * 1024 + 1}, "MAX_REPLAY_ARTIFACT_BYTES"),
            ({"upload_chunk_bytes": 0}, "UPLOAD_CHUNK_BYTES"),
            ({"upload_chunk_bytes": 2 * 1024 * 1024}, "UPLOAD_CHUNK_BYTES"),
            ({"upload_chunk_bytes": 8 * 1024 * 1024 + 1}, "UPLOAD_CHUNK_BYTES"),
        )
        for override, expected in invalid:
            with self.subTest(override=override):
                values = production_values()
                values.update(override)
                with self.assertRaisesRegex(RuntimeError, expected):
                    Settings(**values).validate_runtime_configuration()

    def test_production_worker_requires_the_same_object_storage_boundary(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ARTIFACT_STORAGE_BACKEND"):
            Settings(
                auth_mode="production",
                render_worker_token="production-worker-secret",
                artifact_storage_backend="local",
            ).validate_worker_runtime_configuration()

        Settings(
            auth_mode="production",
            render_worker_token="production-worker-secret",
            artifact_storage_backend="s3",
            object_storage_bucket="private-cs2-artifacts",
            object_storage_prefix="cs2-artifacts-v1",
            redis_url="redis://:test-redis-password@redis:6379/0",
        ).validate_worker_runtime_configuration()


def production_values() -> dict[str, object]:
    return {
        "auth_mode": "production",
        "auth_provider": "oidc",
        "oidc_issuer": "https://issuer.example.test",
        "oidc_client_id": "cs2-coach",
        "oidc_authorization_endpoint": "https://issuer.example.test/authorize",
        "oidc_token_endpoint": "https://issuer.example.test/token",
        "oidc_jwks_url": "https://issuer.example.test/jwks",
        "oidc_redirect_uri": "https://coach.example.test/auth/oidc/callback",
        "backend_public_url": "https://coach.example.test",
        "frontend_public_url": "https://coach.example.test",
        "auth_cookie_secure": True,
        "cors_origins_raw": "https://coach.example.test",
        "render_worker_token": "production-worker-secret",
        "steam_web_api_key": "a" * 32,
        "steam_credential_encryption_key": base64.urlsafe_b64encode(
            b"p" * 32
        ).decode("ascii"),
        "steam_credential_encryption_key_version": "test-v1",
        "artifact_storage_backend": "s3",
        "object_storage_bucket": "private-cs2-artifacts",
        "object_storage_prefix": "cs2-artifacts-v1",
        # Production Redis requires a password (config._validate_production_redis_url).
        "redis_url": "redis://:test-redis-password@redis:6379/0",
    }


MIB = 1024 * 1024
UPLOAD_SESSION_ENV = (
    "UPLOAD_STAGING_ROOT",
    "UPLOAD_PART_BYTES",
    "UPLOAD_MAX_PARALLEL_PARTS",
    "UPLOAD_PART_POOL",
    "UPLOAD_SESSION_TTL_SECONDS",
    "UPLOAD_SESSION_GLOBAL_LIMIT",
    "UPLOAD_STAGING_MIN_FREE_BYTES",
)


class UploadSessionConfigurationTest(unittest.TestCase):
    """Chunked upload settings (S22): ranges checked in every mode and by the worker."""

    def bases(self) -> tuple[dict[str, object], ...]:
        return (
            {"auth_mode": "development", "artifact_storage_backend": "local"},
            {"auth_mode": "test", "artifact_storage_backend": "local"},
            production_values(),
        )

    def test_env_defaults_and_overrides(self) -> None:
        # Settings reads the environment once at import, so read it fresh.
        script = (
            "from app.core.config import settings as s\n"
            "print(s.upload_staging_root.as_posix(), s.upload_part_bytes,"
            " s.upload_max_parallel_parts, s.upload_part_pool, s.upload_session_ttl_seconds,"
            " s.upload_session_global_limit, s.upload_staging_min_free_bytes)\n"
        )
        base_env = {
            name: value
            for name, value in os.environ.items()
            if name not in {*UPLOAD_SESSION_ENV, "ARTIFACT_STORAGE_ROOT"}
        }
        for overrides, expected in (
            ({}, f"/data/upload-staging {8 * MIB} 4 8 86400 6 {5 * 1024 * MIB}"),
            (
                {
                    "UPLOAD_STAGING_ROOT": "/srv/staging",
                    "UPLOAD_PART_BYTES": str(16 * MIB),
                    "UPLOAD_MAX_PARALLEL_PARTS": "2",
                    "UPLOAD_PART_POOL": "4",
                    "UPLOAD_SESSION_TTL_SECONDS": "7200",
                    "UPLOAD_SESSION_GLOBAL_LIMIT": "3",
                    "UPLOAD_STAGING_MIN_FREE_BYTES": "0",
                },
                f"/srv/staging {16 * MIB} 2 4 7200 3 0",
            ),
            # The staging root follows ARTIFACT_STORAGE_ROOT unless set.
            ({"ARTIFACT_STORAGE_ROOT": "/mnt/artifacts"}, f"/mnt/artifacts/upload-staging {8 * MIB} 4 8 86400 6 {5 * 1024 * MIB}"),
        ):
            with self.subTest(overrides=overrides):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    env={**base_env, **overrides, "PYTHONPATH": str(BACKEND_DIR)},
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], expected)

    def test_defaults_validate_in_every_mode_and_for_the_worker(self) -> None:
        for base in self.bases():
            with self.subTest(mode=base["auth_mode"]):
                Settings(**base).validate_runtime_configuration()
        Settings(
            auth_mode="production",
            render_worker_token="production-worker-secret",
            artifact_storage_backend="s3",
            object_storage_bucket="private-cs2-artifacts",
            object_storage_prefix="cs2-artifacts-v1",
            redis_url="redis://:test-redis-password@redis:6379/0",
        ).validate_worker_runtime_configuration()
        Settings(auth_mode="development", artifact_storage_backend="local").validate_worker_runtime_configuration()

    def test_boundaries_are_accepted(self) -> None:
        accepted = (
            {"upload_part_bytes": 4 * MIB},
            {"upload_part_bytes": 32 * MIB},
            {"upload_max_parallel_parts": 1, "upload_part_pool": 1},
            {"upload_max_parallel_parts": 6, "upload_part_pool": 6},
            {"upload_part_pool": 32},
            {"upload_part_bytes": 32 * MIB, "upload_part_pool": 8},
            {"upload_session_ttl_seconds": 3_600},
            {"upload_session_ttl_seconds": 7 * 86_400},
            {"upload_session_global_limit": 1},
            {"upload_session_global_limit": 64},
            {"upload_staging_min_free_bytes": 0},
            {"upload_staging_min_free_bytes": 1024 * 1024 * MIB},
        )
        for base in self.bases():
            for override in accepted:
                with self.subTest(mode=base["auth_mode"], override=override):
                    Settings(**{**base, **override}).validate_runtime_configuration()

    def test_out_of_range_values_are_rejected_in_every_mode(self) -> None:
        rejected = (
            ({"upload_part_bytes": 4 * MIB - 1}, "UPLOAD_PART_BYTES"),
            ({"upload_part_bytes": 32 * MIB + MIB}, "UPLOAD_PART_BYTES"),
            ({"upload_part_bytes": 8 * MIB + 1}, "UPLOAD_PART_BYTES"),
            ({"upload_part_bytes": 0}, "UPLOAD_PART_BYTES"),
            ({"upload_max_parallel_parts": 0}, "UPLOAD_MAX_PARALLEL_PARTS"),
            ({"upload_max_parallel_parts": 7, "upload_part_pool": 8}, "UPLOAD_MAX_PARALLEL_PARTS"),
            ({"upload_part_pool": 3}, "UPLOAD_PART_POOL"),
            ({"upload_part_pool": 33}, "UPLOAD_PART_POOL"),
            # Bodies are buffered in memory: pool x part may not exceed 256 MiB.
            ({"upload_part_bytes": 32 * MIB, "upload_part_pool": 9}, "UPLOAD_PART_POOL x UPLOAD_PART_BYTES"),
            ({"upload_session_ttl_seconds": 3_599}, "UPLOAD_SESSION_TTL_SECONDS"),
            ({"upload_session_ttl_seconds": 7 * 86_400 + 1}, "UPLOAD_SESSION_TTL_SECONDS"),
            ({"upload_session_global_limit": 0}, "UPLOAD_SESSION_GLOBAL_LIMIT"),
            ({"upload_session_global_limit": 65}, "UPLOAD_SESSION_GLOBAL_LIMIT"),
            ({"upload_staging_min_free_bytes": -1}, "UPLOAD_STAGING_MIN_FREE_BYTES"),
            ({"upload_staging_min_free_bytes": 1024 * 1024 * MIB + 1}, "UPLOAD_STAGING_MIN_FREE_BYTES"),
            ({"upload_staging_root": Path("")}, "UPLOAD_STAGING_ROOT"),
        )
        for base in self.bases():
            for override, expected in rejected:
                with (
                    self.subTest(mode=base["auth_mode"], override=override),
                    self.assertRaisesRegex(RuntimeError, expected),
                ):
                    Settings(**{**base, **override}).validate_runtime_configuration()
        worker = Settings(auth_mode="development", artifact_storage_backend="local", upload_part_pool=0)
        with self.assertRaisesRegex(RuntimeError, "UPLOAD_PART_POOL"):
            worker.validate_worker_runtime_configuration()

    def test_production_requires_a_rooted_staging_path(self) -> None:
        # "/data/upload-staging" is rooted but not absolute on Windows.
        Settings(**{**production_values(), "upload_staging_root": Path("/data/upload-staging")}).validate_runtime_configuration()
        with self.assertRaisesRegex(RuntimeError, "UPLOAD_STAGING_ROOT must be an absolute path"):
            Settings(**{**production_values(), "upload_staging_root": Path("relative/staging")}).validate_runtime_configuration()
        # Development keeps relative roots usable for local runs.
        Settings(
            auth_mode="development",
            artifact_storage_backend="local",
            upload_staging_root=Path("relative/staging"),
        ).validate_runtime_configuration()


BACKEND_DIR = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    unittest.main()
