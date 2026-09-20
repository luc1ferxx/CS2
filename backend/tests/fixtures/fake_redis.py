"""An in-memory stand-in for the subset of Redis this project actually uses.

Every double here behaves like redis-py configured with decode_responses=True:
lists and sets hold `str`, never `bytes`. Lists are stored head-first, matching
Redis itself -- LPUSH prepends, RPOP takes from the tail, and LINDEX -1 is the
oldest entry, which is the one the queue reaps first.

Keys do not expire on their own. A test that needs a lease to lapse deletes the
key outright; that is the same state the reaper sees, without making the tests
depend on wall-clock timing.
"""

from __future__ import annotations

from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    # -- connection -----------------------------------------------------
    def ping(self) -> bool:
        return True

    # -- strings --------------------------------------------------------
    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: Any) -> bool:
        self.values[key] = str(value)
        return True

    def setex(self, key: str, ttl_seconds: int, value: Any) -> bool:
        self.values[key] = str(value)
        self.ttls[key] = ttl_seconds
        return True

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    def exists(self, *keys: str) -> int:
        return sum(
            1
            for key in keys
            if key in self.values or self.lists.get(key) or self.sets.get(key)
        )

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            present = key in self.values or key in self.lists or key in self.sets
            self.values.pop(key, None)
            self.lists.pop(key, None)
            self.sets.pop(key, None)
            self.ttls.pop(key, None)
            removed += 1 if present else 0
        return removed

    # -- lists ----------------------------------------------------------
    def lpush(self, key: str, *values: Any) -> int:
        entries = self.lists.setdefault(key, [])
        for value in values:
            entries.insert(0, str(value))
        return len(entries)

    def rpush(self, key: str, *values: Any) -> int:
        entries = self.lists.setdefault(key, [])
        entries.extend(str(value) for value in values)
        return len(entries)

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        entries = self.lists.get(key, [])
        if end == -1:
            return entries[start:]
        return entries[start : end + 1]

    def lindex(self, key: str, index: int) -> str | None:
        entries = self.lists.get(key, [])
        try:
            return entries[index]
        except IndexError:
            return None

    def rpop(self, key: str) -> str | None:
        entries = self.lists.get(key)
        if not entries:
            return None
        return entries.pop()

    def lrem(self, key: str, count: int, value: Any) -> int:
        entries = self.lists.get(key)
        if not entries:
            return 0
        needle = str(value)
        order = entries if count >= 0 else list(reversed(entries))
        limit = abs(count) or len(order)
        keep: list[str] = []
        removed = 0
        for entry in order:
            if entry == needle and removed < limit:
                removed += 1
                continue
            keep.append(entry)
        self.lists[key] = keep if count >= 0 else list(reversed(keep))
        return removed

    def rpoplpush(self, source: str, destination: str) -> str | None:
        value = self.rpop(source)
        if value is None:
            return None
        self.lpush(destination, value)
        return value

    def brpoplpush(self, source: str, destination: str, timeout: int = 0) -> str | None:
        # Nothing blocks in a fake: an empty queue reports the same thing a real
        # BRPOPLPUSH reports once its timeout elapses.
        return self.rpoplpush(source, destination)

    def brpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
        value = self.rpop(key)
        if value is None:
            return None
        return (key, value)

    # -- sets -----------------------------------------------------------
    def sadd(self, key: str, *members: Any) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(str(member) for member in members)
        return len(bucket) - before

    def srem(self, key: str, *members: Any) -> int:
        bucket = self.sets.get(key)
        if not bucket:
            return 0
        before = len(bucket)
        bucket.difference_update(str(member) for member in members)
        return before - len(bucket)

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))
