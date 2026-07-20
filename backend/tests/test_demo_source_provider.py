import unittest

from app.core.config import Settings
from app.services.demo_source_provider import (
    DemoSourceUnavailableError,
    DisabledDemoSourceProvider,
    demo_source_provider_from_settings,
)


class DemoSourceProviderTest(unittest.TestCase):
    def test_default_provider_fails_closed_without_network_or_fake_source(self) -> None:
        provider = DisabledDemoSourceProvider()

        with self.assertRaises(DemoSourceUnavailableError) as raised:
            provider.resolve(share_code="CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")

        self.assertEqual(provider.provider_id, "disabled")
        self.assertFalse(provider.available)
        self.assertEqual(raised.exception.code, "provider_not_configured")
        self.assertTrue(raised.exception.manual_upload_supported)
        self.assertNotIn("CSGO-", str(raised.exception))

    def test_factory_is_disabled_by_default_and_rejects_unregistered_provider(self) -> None:
        provider = demo_source_provider_from_settings(Settings(auth_mode="test"))
        self.assertIsInstance(provider, DisabledDemoSourceProvider)

        with self.assertRaises(RuntimeError):
            demo_source_provider_from_settings(
                Settings(auth_mode="test", steam_demo_provider="unregistered")
            )


if __name__ == "__main__":
    unittest.main()
