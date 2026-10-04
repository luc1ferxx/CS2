"""Process-wide upload capacity: the single artifact intake slot and the part pool.

Both rely on the single uvicorn process the Compose stack runs (see
docker-compose.prod.yml): a second process would get its own counters.

* `INTAKE_SLOT` admits one whole-file intake at a time across the legacy
  `/uploads/demo`, the manual video and worker media uploads (taken by
  MultipartRequestLimitMiddleware while their body streams) and the chunked
  upload's `complete` (taken by the route, non-blocking). Only one 1 GiB
  intake runs at once, as docs/object_storage_safe_artifact_intake_v1.md says.
* A `PartPool` bounds the chunked-upload part PUTs being read at once
  (each holds at most one part in memory). The middleware owns its pool.

Acquisition never blocks: a full slot or pool answers 503 `INTAKE_BUSY`
with a `Retry-After`, and the client tries again.
"""

from __future__ import annotations

import re
import threading

# The one chunked-upload route authenticated by its upload token instead of
# the session cookie. Always matched with `fullmatch` (a `$` would accept a
# trailing newline); SessionCsrfMiddleware exempts exactly PUT on this path.
UPLOAD_PART_PATH = re.compile(r"/uploads/sessions/[0-9a-f]{32}/parts/[0-9]{1,5}")


class _CountingSlot:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._in_use = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_use >= self.capacity:
                return False
            self._in_use += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_use <= 0:
                raise RuntimeError("released more times than acquired")
            self._in_use -= 1

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._in_use


class IntakeSlot(_CountingSlot):
    """The whole-file intake slot; capacity 1 outside tests."""

    def __init__(self, capacity: int = 1) -> None:
        super().__init__(capacity)


class PartPool(_CountingSlot):
    """At most `capacity` chunked-upload part bodies read at once."""


INTAKE_SLOT = IntakeSlot(1)


def is_upload_part_request(method: str, path: str) -> bool:
    return method.upper() == "PUT" and UPLOAD_PART_PATH.fullmatch(path) is not None


def get_intake_slot() -> IntakeSlot:
    """FastAPI dependency for the shared intake slot (tests override it)."""
    return INTAKE_SLOT
