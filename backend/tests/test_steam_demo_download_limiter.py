import unittest

from app.core.config import Settings
from app.services.steam_demo_download_limiter import (
    SteamDemoDownloadCapacityError,
    SteamDemoDownloadLimiter,
    SteamDemoDownloadLimiterUnavailableError,
    steam_demo_download_limiter_from_settings,
)


class FakeRedis:
    def __init__(self, acquire_result: object = 1) -> None:
        self.acquire_result = acquire_result
        self.calls: list[tuple[object, ...]] = []
        self.raise_error = False

    def eval(self, *args: object) -> object:
        self.calls.append(args)
        if self.raise_error:
            raise ConnectionError("redis unavailable")
        if len(self.calls) == 1:
            return self.acquire_result
        return 1


class SteamDemoDownloadLimiterTest(unittest.TestCase):
    def test_lease_acquires_both_limits_and_releases_same_opaque_token(self) -> None:
        redis = FakeRedis()
        limiter = SteamDemoDownloadLimiter(
            redis,
            global_limit=4,
            owner_limit=1,
            lease_seconds=120,
            token_factory=lambda: "opaque-run-token",
        )

        with limiter.lease("owner-a"):
            self.assertEqual(len(redis.calls), 1)

        self.assertEqual(len(redis.calls), 2)
        acquire = redis.calls[0]
        release = redis.calls[1]
        self.assertEqual(acquire[1], 2)
        self.assertEqual(acquire[2], "steam:{demo-download}:global")
        self.assertTrue(str(acquire[3]).startswith("steam:{demo-download}:owner:"))
        self.assertIn("redis.call('TIME')", str(acquire[0]))
        self.assertEqual(acquire[4:], (120_000, 4, 1, "opaque-run-token"))
        self.assertEqual(acquire[-1], "opaque-run-token")
        self.assertEqual(release[2:4], acquire[2:4])
        self.assertEqual(release[-1], "opaque-run-token")

    def test_capacity_is_fail_closed_without_running_body(self) -> None:
        redis = FakeRedis(acquire_result=0)
        limiter = SteamDemoDownloadLimiter(redis)
        entered = False

        with self.assertRaises(SteamDemoDownloadCapacityError):
            with limiter.lease("owner-a"):
                entered = True

        self.assertFalse(entered)
        self.assertEqual(len(redis.calls), 1)

    def test_redis_failure_is_fail_closed(self) -> None:
        redis = FakeRedis()
        redis.raise_error = True
        limiter = SteamDemoDownloadLimiter(redis)

        with self.assertRaises(SteamDemoDownloadLimiterUnavailableError):
            with limiter.lease("owner-a"):
                self.fail("unavailable limiter must not yield")

    def test_settings_factory_copies_validated_concurrency_limits(self) -> None:
        limiter = steam_demo_download_limiter_from_settings(
            FakeRedis(),
            Settings(
                auth_mode="test",
                steam_demo_download_global_concurrency=7,
                steam_demo_download_owner_concurrency=2,
                steam_demo_download_concurrency_lease_seconds=180,
            ),
        )
        self.assertEqual(limiter.global_limit, 7)
        self.assertEqual(limiter.owner_limit, 2)
        self.assertEqual(limiter.lease_seconds, 180)


if __name__ == "__main__":
    unittest.main()
