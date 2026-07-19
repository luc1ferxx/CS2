import unittest

from app.services.steam_sync_rate_limit import (
    STEAM_SYNC_GLOBAL_REQUEST_LIMIT,
    STEAM_SYNC_OWNER_REQUEST_LIMIT,
    STEAM_SYNC_PUBLISHER_BLOCK_SCRIPT,
    STEAM_SYNC_RATE_LIMIT_SCRIPT,
    SteamSyncRateLimitError,
    SteamSyncRateLimiter,
    SteamSyncRateLimitUnavailableError,
)


class FakeRateLimitRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.publisher_ttl = 0
        self.keys_seen: list[str] = []

    def eval(self, script: str, key_count: int, *values: object):
        if script == STEAM_SYNC_RATE_LIMIT_SCRIPT:
            self.assert_key_count(key_count, 3)
            owner_key, global_key, _blocked_key = map(str, values[:3])
            window_ttl, owner_limit, global_limit = map(int, values[3:])
            self.keys_seen.extend((owner_key, global_key))
            if self.publisher_ttl > 0:
                return [-1, self.publisher_ttl]
            owner_count = self.counts.get(owner_key, 0)
            global_count = self.counts.get(global_key, 0)
            if owner_count >= owner_limit:
                return [0, self.ttls.get(owner_key, 1)]
            if global_count >= global_limit:
                return [0, self.ttls.get(global_key, 1)]
            self.counts[owner_key] = owner_count + 1
            self.counts[global_key] = global_count + 1
            self.ttls.setdefault(owner_key, window_ttl)
            self.ttls.setdefault(global_key, window_ttl)
            return [1, 0]
        if script == STEAM_SYNC_PUBLISHER_BLOCK_SCRIPT:
            self.assert_key_count(key_count, 1)
            requested_ttl = int(values[1])
            self.publisher_ttl = max(self.publisher_ttl, requested_ttl)
            return 1
        raise AssertionError("Unexpected Redis script")

    @staticmethod
    def assert_key_count(actual: int, expected: int) -> None:
        if actual != expected:
            raise AssertionError(f"Expected {expected} Redis keys, got {actual}")


class SteamSyncRateLimiterTest(unittest.TestCase):
    def test_owner_limit_is_atomic_and_owner_key_is_hashed(self) -> None:
        redis = FakeRateLimitRedis()
        limiter = SteamSyncRateLimiter(redis, clock=lambda: 1_000.0)
        owner_id = "owner_v1_private_account"

        for _ in range(STEAM_SYNC_OWNER_REQUEST_LIMIT):
            limiter.enforce(owner_id)
        with self.assertRaises(SteamSyncRateLimitError) as raised:
            limiter.enforce(owner_id)

        self.assertGreaterEqual(raised.exception.retry_after_seconds, 1)
        self.assertTrue(all(owner_id not in key for key in redis.keys_seen))

    def test_distinct_owners_share_the_global_limit(self) -> None:
        redis = FakeRateLimitRedis()
        limiter = SteamSyncRateLimiter(redis, clock=lambda: 2_000.0)

        for index in range(STEAM_SYNC_GLOBAL_REQUEST_LIMIT):
            limiter.enforce(f"owner_v1_{index:04d}")
        with self.assertRaises(SteamSyncRateLimitError):
            limiter.enforce("owner_v1_global_limit")

    def test_publisher_breaker_preserves_long_retry_after(self) -> None:
        redis = FakeRateLimitRedis()
        limiter = SteamSyncRateLimiter(redis, clock=lambda: 3_000.0)
        limiter.block_publisher(3_600)

        with self.assertRaises(SteamSyncRateLimitError) as raised:
            limiter.enforce("owner_v1_other")

        self.assertEqual(raised.exception.retry_after_seconds, 3_600)

    def test_redis_failure_fails_closed(self) -> None:
        class BrokenRedis:
            def eval(self, *_args: object):
                raise ConnectionError("redis unavailable")

        limiter = SteamSyncRateLimiter(BrokenRedis(), clock=lambda: 4_000.0)
        with self.assertRaises(SteamSyncRateLimitUnavailableError):
            limiter.enforce("owner_v1_test")


if __name__ == "__main__":
    unittest.main()
