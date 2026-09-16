from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

STEAM_DEMO_DOWNLOAD_ACQUIRE_SCRIPT = """
local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
local lease_ms = tonumber(ARGV[1])
local lease_until_ms = now_ms + lease_ms
local global_limit = tonumber(ARGV[2])
local owner_limit = tonumber(ARGV[3])
local token = ARGV[4]

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)
if redis.call('ZCARD', KEYS[1]) >= global_limit then
  return 0
end
if redis.call('ZCARD', KEYS[2]) >= owner_limit then
  return 0
end
if redis.call('ZADD', KEYS[1], 'NX', lease_until_ms, token) ~= 1 then
  return -1
end
if redis.call('ZADD', KEYS[2], 'NX', lease_until_ms, token) ~= 1 then
  redis.call('ZREM', KEYS[1], token)
  return -1
end
local ttl_seconds = math.max(math.ceil(lease_ms / 1000) + 1, 1)
redis.call('EXPIRE', KEYS[1], ttl_seconds)
redis.call('EXPIRE', KEYS[2], ttl_seconds)
return 1
"""


STEAM_DEMO_DOWNLOAD_RELEASE_SCRIPT = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""


class SteamDemoDownloadCapacityError(RuntimeError):
    pass


class SteamDemoDownloadLimiterUnavailableError(RuntimeError):
    pass


class SteamDemoDownloadLimiter:
    def __init__(
        self,
        redis_client: object,
        *,
        global_limit: int = 4,
        owner_limit: int = 1,
        lease_seconds: int = 120,
        token_factory: object | None = None,
    ):
        if not 1 <= global_limit <= 32:
            raise ValueError("Steam Demo global download limit is invalid")
        if not 1 <= owner_limit <= global_limit:
            raise ValueError("Steam Demo owner download limit is invalid")
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("Steam Demo download lease is invalid")
        self.redis = redis_client
        self.global_limit = global_limit
        self.owner_limit = owner_limit
        self.lease_seconds = lease_seconds
        self.token_factory = token_factory or (lambda: str(uuid.uuid4()))

    @contextmanager
    def lease(self, owner_id: str) -> Iterator[None]:
        owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        global_key = "steam:{demo-download}:global"
        owner_key = f"steam:{{demo-download}}:owner:{owner_digest}"
        token = str(self.token_factory())
        try:
            raw_result = self.redis.eval(
                STEAM_DEMO_DOWNLOAD_ACQUIRE_SCRIPT,
                2,
                global_key,
                owner_key,
                self.lease_seconds * 1000,
                self.global_limit,
                self.owner_limit,
                token,
            )
            result = int(raw_result)
        except Exception:
            raise SteamDemoDownloadLimiterUnavailableError(
                "Automatic Demo download capacity control is unavailable"
            ) from None
        if result == 0:
            raise SteamDemoDownloadCapacityError(
                "Automatic Demo download capacity is temporarily full"
            )
        if result != 1:
            raise SteamDemoDownloadLimiterUnavailableError(
                "Automatic Demo download capacity control is unavailable"
            )
        try:
            yield
        finally:
            try:
                self.redis.eval(
                    STEAM_DEMO_DOWNLOAD_RELEASE_SCRIPT,
                    2,
                    global_key,
                    owner_key,
                    token,
                )
            except Exception:
                # The short lease is the fail-safe cleanup if Redis disappears.
                pass


def steam_demo_download_limiter_from_settings(
    redis_client: object,
    runtime_settings: object,
) -> SteamDemoDownloadLimiter:
    return SteamDemoDownloadLimiter(
        redis_client,
        global_limit=int(
            runtime_settings.steam_demo_download_global_concurrency
        ),
        owner_limit=int(
            runtime_settings.steam_demo_download_owner_concurrency
        ),
        lease_seconds=int(
            runtime_settings.steam_demo_download_concurrency_lease_seconds
        ),
    )
