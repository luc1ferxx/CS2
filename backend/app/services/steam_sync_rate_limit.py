from __future__ import annotations

import hashlib
import time


STEAM_SYNC_RATE_WINDOW_SECONDS = 60
STEAM_SYNC_OWNER_REQUEST_LIMIT = 3
STEAM_SYNC_GLOBAL_REQUEST_LIMIT = 30
STEAM_SYNC_PUBLISHER_BLOCK_KEY = "steam:sync:publisher:blocked"

STEAM_SYNC_RATE_LIMIT_SCRIPT = """
local blocked_ttl = redis.call('TTL', KEYS[3])
if blocked_ttl == -1 then
  return {-2, 0}
end
if blocked_ttl > 0 then
  return {-1, blocked_ttl}
end

local owner_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local global_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if owner_count >= tonumber(ARGV[2]) then
  return {0, math.max(redis.call('TTL', KEYS[1]), 1)}
end
if global_count >= tonumber(ARGV[3]) then
  return {0, math.max(redis.call('TTL', KEYS[2]), 1)}
end

owner_count = redis.call('INCR', KEYS[1])
if owner_count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
global_count = redis.call('INCR', KEYS[2])
if global_count == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[1])
end
return {1, 0}
"""

STEAM_SYNC_PUBLISHER_BLOCK_SCRIPT = """
local current_ttl = redis.call('TTL', KEYS[1])
local requested_ttl = tonumber(ARGV[1])
if current_ttl < requested_ttl then
  redis.call('SET', KEYS[1], '1', 'EX', requested_ttl)
end
return 1
"""


class SteamSyncRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("Steam match-history sync rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class SteamSyncRateLimitUnavailableError(RuntimeError):
    pass


class SteamSyncRateLimiter:
    def __init__(self, redis_client: object, *, clock: object = time.time):
        self.redis = redis_client
        self.clock = clock

    def enforce(self, owner_id: str) -> None:
        now = int(self.clock())
        window = now // STEAM_SYNC_RATE_WINDOW_SECONDS
        window_ttl = (
            STEAM_SYNC_RATE_WINDOW_SECONDS
            - (now % STEAM_SYNC_RATE_WINDOW_SECONDS)
            + 1
        )
        owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        owner_key = f"steam:sync:rate:owner:{window}:{owner_digest}"
        global_key = f"steam:sync:rate:global:{window}"
        try:
            result = self.redis.eval(
                STEAM_SYNC_RATE_LIMIT_SCRIPT,
                3,
                owner_key,
                global_key,
                STEAM_SYNC_PUBLISHER_BLOCK_KEY,
                window_ttl,
                STEAM_SYNC_OWNER_REQUEST_LIMIT,
                STEAM_SYNC_GLOBAL_REQUEST_LIMIT,
            )
            allowed, retry_after = _rate_limit_result(result)
        except SteamSyncRateLimitUnavailableError:
            raise
        except Exception:
            raise SteamSyncRateLimitUnavailableError(
                "Steam sync rate limiter is unavailable"
            ) from None
        if allowed != 1:
            raise SteamSyncRateLimitError(retry_after)

    def block_publisher(self, retry_after_seconds: int) -> None:
        bounded_retry_after = max(
            1,
            min(int(retry_after_seconds), 86_400),
        )
        try:
            self.redis.eval(
                STEAM_SYNC_PUBLISHER_BLOCK_SCRIPT,
                1,
                STEAM_SYNC_PUBLISHER_BLOCK_KEY,
                bounded_retry_after,
            )
        except Exception:
            # The current request already persists its owner-scoped backoff.
            # A best-effort global breaker must not hide that safe result.
            return


def _rate_limit_result(result: object) -> tuple[int, int]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise SteamSyncRateLimitUnavailableError(
            "Steam sync rate limiter returned an invalid result"
        )
    try:
        allowed = int(result[0])
        raw_retry_after = int(result[1])
    except (TypeError, ValueError, OverflowError):
        raise SteamSyncRateLimitUnavailableError(
            "Steam sync rate limiter returned an invalid result"
        ) from None
    if allowed == 1:
        return allowed, 0
    if allowed == 0:
        retry_after = max(
            1,
            min(raw_retry_after, STEAM_SYNC_RATE_WINDOW_SECONDS + 1),
        )
        return allowed, retry_after
    if allowed == -1:
        retry_after = max(1, min(raw_retry_after, 86_400))
        return allowed, retry_after
    raise SteamSyncRateLimitUnavailableError(
        "Steam sync rate limiter returned an invalid result"
    )
