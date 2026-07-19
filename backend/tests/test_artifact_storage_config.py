import base64
import unittest

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
    }


if __name__ == "__main__":
    unittest.main()
