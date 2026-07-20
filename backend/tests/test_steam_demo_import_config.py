import base64
import unittest

from app.core.config import Settings


class SteamDemoImportConfigurationTest(unittest.TestCase):
    def test_shipped_runtime_defaults_to_the_disabled_provider(self) -> None:
        settings = Settings(auth_mode="development")

        self.assertEqual(settings.steam_demo_provider, "disabled")
        self.assertFalse(settings.steam_demo_experimental_replay_cdn_enabled)
        self.assertEqual(settings.steam_demo_download_allowed_hosts, frozenset())
        settings.validate_runtime_configuration()

    def test_shipped_runtime_rejects_unregistered_or_blank_providers(self) -> None:
        for provider in ("", "partner", "experimental_replay_cdn"):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(RuntimeError, "STEAM_DEMO_PROVIDER"):
                    Settings(
                        auth_mode="development",
                        steam_demo_provider=provider,
                    ).validate_runtime_configuration()

    def test_experimental_replay_cdn_switch_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED",
        ):
            Settings(
                auth_mode="development",
                steam_demo_experimental_replay_cdn_enabled=True,
            ).validate_runtime_configuration()

        for raw_value in ("", "enabled", "2"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED",
                ):
                    Settings(
                        auth_mode="development",
                        steam_demo_experimental_replay_cdn_enabled=False,
                        steam_demo_experimental_replay_cdn_raw=raw_value,
                    ).validate_runtime_configuration()

    def test_production_applies_the_same_fail_closed_provider_gate(self) -> None:
        for override, expected in (
            ({"steam_demo_provider": "partner"}, "STEAM_DEMO_PROVIDER"),
            (
                {"steam_demo_experimental_replay_cdn_enabled": True},
                "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED",
            ),
        ):
            with self.subTest(override=override):
                values = production_values()
                values.update(override)
                with self.assertRaisesRegex(RuntimeError, expected):
                    Settings(**values).validate_runtime_configuration()

    def test_parser_worker_does_not_receive_or_validate_source_configuration(self) -> None:
        Settings(
            auth_mode="production",
            render_worker_token="production-worker-secret",
            artifact_storage_backend="s3",
            object_storage_bucket="private-cs2-artifacts",
            object_storage_prefix="cs2-artifacts-v1",
            steam_demo_provider="not-present-in-worker-env",
            steam_demo_experimental_replay_cdn_enabled=True,
        ).validate_worker_runtime_configuration()

    def test_exact_download_host_allowlist_is_canonical_and_bounded(self) -> None:
        settings = Settings(
            auth_mode="development",
            steam_demo_download_allowed_hosts_raw=(
                "demos.partner.example,archive.partner.example"
            ),
        )

        settings.validate_runtime_configuration()
        self.assertEqual(
            settings.steam_demo_download_allowed_hosts,
            frozenset(
                {"demos.partner.example", "archive.partner.example"}
            ),
        )

        for allowlist in (
            "*.partner.example",
            ".partner.example",
            "PARTNER.EXAMPLE",
            "partner.example:443",
            "127.0.0.1",
            "localhost",
            "partner.example,partner.example",
            "partner..example",
        ):
            with self.subTest(allowlist=allowlist):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS",
                ):
                    Settings(
                        auth_mode="development",
                        steam_demo_download_allowed_hosts_raw=allowlist,
                    ).validate_runtime_configuration()

    def test_download_limits_and_concurrency_are_strictly_bounded(self) -> None:
        invalid = (
            ({"steam_demo_download_max_bytes": 15}, "MAX_BYTES"),
            (
                {"steam_demo_download_max_bytes": 1024 * 1024 * 1024 + 1},
                "MAX_BYTES",
            ),
            ({"steam_demo_download_max_redirects": -1}, "MAX_REDIRECTS"),
            ({"steam_demo_download_max_redirects": 4}, "MAX_REDIRECTS"),
            ({"steam_demo_download_connect_timeout_seconds": 0}, "CONNECT_TIMEOUT"),
            ({"steam_demo_download_read_timeout_seconds": 31}, "READ_TIMEOUT"),
            (
                {
                    "steam_demo_download_read_timeout_seconds": 10,
                    "steam_demo_download_total_timeout_seconds": 9,
                },
                "TOTAL_TIMEOUT",
            ),
            ({"steam_demo_download_total_timeout_seconds": 121}, "TOTAL_TIMEOUT"),
            ({"steam_demo_download_global_concurrency": 0}, "GLOBAL_CONCURRENCY"),
            ({"steam_demo_download_global_concurrency": 33}, "GLOBAL_CONCURRENCY"),
            ({"steam_demo_download_owner_concurrency": 0}, "OWNER_CONCURRENCY"),
            (
                {
                    "steam_demo_download_global_concurrency": 2,
                    "steam_demo_download_owner_concurrency": 3,
                },
                "OWNER_CONCURRENCY",
            ),
            (
                {
                    "steam_demo_download_total_timeout_seconds": 120,
                    "steam_demo_download_concurrency_lease_seconds": 119,
                },
                "CONCURRENCY_LEASE",
            ),
            (
                {"steam_demo_download_concurrency_lease_seconds": 3601},
                "CONCURRENCY_LEASE",
            ),
        )
        for override, expected in invalid:
            with self.subTest(override=override):
                values = {"auth_mode": "development", **override}
                with self.assertRaisesRegex(RuntimeError, expected):
                    Settings(**values).validate_runtime_configuration()


def production_values() -> dict[str, object]:
    return {
        "auth_mode": "production",
        "auth_provider": "steam",
        "backend_public_url": "https://coach.example.test",
        "frontend_public_url": "https://coach.example.test",
        "auth_cookie_secure": True,
        "cors_origins_raw": "https://coach.example.test",
        "render_worker_token": "production-worker-secret",
        "steam_web_api_key": "a" * 32,
        "steam_credential_encryption_key": base64.urlsafe_b64encode(
            b"p" * 32
        ).decode("ascii"),
        "steam_credential_encryption_key_version": "production-v1",
        "artifact_storage_backend": "s3",
        "object_storage_bucket": "private-cs2-artifacts",
        "object_storage_prefix": "cs2-artifacts-v1",
    }


if __name__ == "__main__":
    unittest.main()
