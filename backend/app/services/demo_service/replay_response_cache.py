"""In-process cache of finished ``GET /demos/{id}/replay`` responses, with an ETag.

A replay response costs about a second of server work on every request (read
and parse the artifact, normalize it, project it, serialize, gzip) and a page
reload used to redo all of it. Production runs one uvicorn process, so one
in-process cache serves every request.

Why a replay key can be cached forever: every accepted replay write (parse
completion, the background replay upgrade, each video write) goes through
``ReplayBlob.write_replay_blob``, which stores the content under a fresh
``artifact://`` reference -- ``ArtifactStore.new_reference`` mints a uuid4
artifact id, and ``write_stream`` never overwrites an existing reference
(``ArtifactConflictError`` locally, ``IfNoneMatch: *`` on S3). The bytes behind
one reference never change. Legacy ``local://`` keys are rewritten in place
(``replays/{demo_id}.json``), so they keep the uncached path with no ETag.

What the response depends on, and so what the ETag hashes: the replay behind
the key; the public video status (that replay's video section plus whether
the private video object exists right now, a storage HEAD when a video is
ready, recomputed on every request); the normalizer and projection code
(``PUBLIC_REPLAY_CACHE_VERSION``, ``REPLAY_CONTRACT_VERSION``); and the map
config, which the normalizer reads to refresh map metadata and re-project
older replays. Same ETag, same body, in this process and after a restart.

Owner boundary: the route runs its owner, 404 and 409 checks before anything
here, so a foreign or deleted demo never reaches the cache, and a guessed
``If-None-Match`` from another owner still gets the 404.

Deletion: ``DeletionService.delete_demo`` and ``delete_account`` (the API
delete, account deletion and ``app.cli.delete_data`` all share them) evict the
demo's or the owner's entries after their commit, and a just-deleted demo id
is refused by ``put`` so a request that read the replay before the delete
cannot store it afterwards. The cache lives in one process: a delete run by
the CLI or by the worker's outbox drain cannot reach the API's memory, and
those entries stay unreachable (the route answers 404 first) until the LRU
drops them or the API restarts.

Warming: ``replay_warmer.py`` fills this same cache in the background (after a
parse, a replay upgrade or a video write, and at API startup) through
``warm_replay_response``, which runs the route's own miss path, so a warmed
entry is the bytes a request would have built, under the ETag it computes.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeGuard, cast

from app.core.config import settings
from app.parser.map_config import SUPPORTED_MAP_NAMES, get_map_config
from app.parser.replay_contract import REPLAY_CONTRACT_VERSION
from app.services.demo_service.projection import _public_replay_contract

if TYPE_CHECKING:
    from app.models.demo import Demo
    from app.services.demo_service import DemoService

# Bump whenever the public replay changes for the same stored replay: the
# projection in projection.py, the defaults normalize_replay_contract fills in,
# or the serialization below.
PUBLIC_REPLAY_CACHE_VERSION = "public_replay_cache_v1"
# Browsers never store a replay: _protect_browser_response (core/auth.py) sends private, no-store on
# every private path, so the ETag/304 serves non-browser clients and the server cache does the work.
CACHE_CONTROL = "private, no-store"
GZIP_LEVEL = 5
# Video sections are a few hundred bytes; this bounds the memo by count.
VIDEO_MEMO_MAX_ENTRIES = 4096
# Recently deleted demo ids whose late puts are refused (ids are uuid4, never reused).
EVICTED_DEMO_MEMORY = 4096
_ETAG_HEX_LENGTH = 32


def _map_config_fingerprint() -> str:
    configs = {name: get_map_config(name) for name in SUPPORTED_MAP_NAMES}
    encoded = json.dumps(configs, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


MAP_CONFIG_FINGERPRINT = _map_config_fingerprint()


def _configured_budget_bytes() -> int:
    # Read per call: tests and operators change the setting without a restart.
    return max(0, int(settings.replay_response_cache_mb)) * 1024 * 1024


def is_cacheable_replay_key(replay_key: object) -> TypeGuard[str]:
    return isinstance(replay_key, str) and replay_key.startswith("artifact://")


def replay_etag(replay_key: str, video_status: dict[str, Any]) -> str:
    """Strong, quoted ETag of the public replay for this key and video status."""
    fingerprint = json.dumps(
        {
            "cache": PUBLIC_REPLAY_CACHE_VERSION,
            "contract": REPLAY_CONTRACT_VERSION,
            "maps": MAP_CONFIG_FINGERPRINT,
            "replayKey": replay_key,
            "video": video_status,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:_ETAG_HEX_LENGTH]
    return f'"{digest}"'


def if_none_match_matches(header: str | None, etag: str) -> bool:
    """Weak comparison (RFC 9110 13.1.2): ``*``, comma lists and ``W/`` all match."""
    if not header:
        return False
    target = etag.strip().strip('"')
    for candidate in header.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if value[:2] in {"W/", "w/"}:
            value = value[2:]
        value = value.strip().strip('"')
        if value and value == target:
            return True
    return False


@dataclass(frozen=True)
class _Entry:
    demo_id: str
    owner_id: str
    body: bytes


@dataclass(frozen=True)
class _VideoMemo:
    demo_id: str
    owner_id: str
    video: dict[str, Any]


class ReplayResponseCache:
    """Thread-safe LRU of gzip response bodies keyed by ETag, bounded by total bytes.

    Beside it, a small LRU memo of each cached replay key's internal video
    section, so the ETag of a cached key costs no artifact read.
    """

    def __init__(
        self,
        budget_bytes: Callable[[], int] = _configured_budget_bytes,
        *,
        video_memo_max_entries: int = VIDEO_MEMO_MAX_ENTRIES,
    ) -> None:
        self._budget_bytes = budget_bytes
        self._video_memo_max_entries = video_memo_max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0
        self._videos: OrderedDict[str, _VideoMemo] = OrderedDict()
        self._evicted_demo_ids: OrderedDict[str, None] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._budget_bytes() > 0

    # -- responses --------------------------------------------------------------

    def get(self, etag: str) -> bytes | None:
        with self._lock:
            entry = self._entries.get(etag)
            if entry is None:
                return None
            self._entries.move_to_end(etag)
            return entry.body

    def contains(self, etag: str) -> bool:
        """Whether a body is stored for this ETag, without counting as a use."""
        with self._lock:
            return etag in self._entries

    def put(self, etag: str, *, demo_id: str, owner_id: str, body: bytes) -> bool:
        """Store one body; False when it was not kept (deleted demo, over budget)."""
        budget = self._budget_bytes()
        with self._lock:
            if demo_id in self._evicted_demo_ids:
                return False
            previous = self._entries.pop(etag, None)
            if previous is not None:
                self._bytes -= len(previous.body)
            stored = len(body) <= budget
            if stored:
                self._entries[etag] = _Entry(demo_id=demo_id, owner_id=owner_id, body=body)
                self._bytes += len(body)
            while self._bytes > budget and self._entries:
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= len(dropped.body)
            return stored

    # -- video memo -------------------------------------------------------------

    def video_for(self, replay_key: str) -> dict[str, Any] | None:
        with self._lock:
            memo = self._videos.get(replay_key)
            if memo is None:
                return None
            self._videos.move_to_end(replay_key)
            return memo.video

    def remember_video(self, replay_key: str, *, demo_id: str, owner_id: str, video: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(video)
        with self._lock:
            if demo_id in self._evicted_demo_ids:
                return
            self._videos[replay_key] = _VideoMemo(demo_id=demo_id, owner_id=owner_id, video=snapshot)
            self._videos.move_to_end(replay_key)
            while len(self._videos) > self._video_memo_max_entries:
                self._videos.popitem(last=False)

    # -- eviction ---------------------------------------------------------------

    def refuses(self, demo_id: str) -> bool:
        """Whether this demo was just deleted, so ``put`` would refuse its body."""
        with self._lock:
            return demo_id in self._evicted_demo_ids

    def evict_demo(self, demo_id: str) -> int:
        """Drop every response and memo of one demo and refuse its later puts."""
        with self._lock:
            removed = self._drop_where(lambda demo, _owner: demo == demo_id)
            self._evicted_demo_ids[demo_id] = None
            self._evicted_demo_ids.move_to_end(demo_id)
            while len(self._evicted_demo_ids) > EVICTED_DEMO_MEMORY:
                self._evicted_demo_ids.popitem(last=False)
            return removed

    def evict_owner(self, owner_id: str) -> int:
        """Drop every response and memo of one owner (account deletion)."""
        with self._lock:
            return self._drop_where(lambda _demo, owner: owner == owner_id)

    def _drop_where(self, matches: Callable[[str, str], bool]) -> int:
        doomed = [etag for etag, entry in self._entries.items() if matches(entry.demo_id, entry.owner_id)]
        for etag in doomed:
            self._bytes -= len(self._entries.pop(etag).body)
        doomed_videos = [key for key, memo in self._videos.items() if matches(memo.demo_id, memo.owner_id)]
        for key in doomed_videos:
            del self._videos[key]
        return len(doomed) + len(doomed_videos)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self._videos.clear()
            self._evicted_demo_ids.clear()

    # -- inspection -------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes, "videoMemos": len(self._videos)}

    def holds_demo(self, demo_id: str) -> bool:
        with self._lock:
            return any(entry.demo_id == demo_id for entry in self._entries.values()) or any(
                memo.demo_id == demo_id for memo in self._videos.values()
            )


replay_response_cache = ReplayResponseCache()


class ReplayArtifactMissingError(LookupError):
    """The demo's replay artifact could not be read; the route answers 404."""


@dataclass(frozen=True)
class CachedReplay:
    etag: str
    not_modified: bool
    # gzip of the compact JSON body; None for a 304.
    gzip_body: bytes | None


def cached_replay_response(
    service: DemoService,
    demo: Demo,
    *,
    if_none_match: str | None,
    cache: ReplayResponseCache | None = None,
) -> CachedReplay | None:
    """The cached replay response of an owner-checked, completed demo.

    None means "not cacheable, take the uncached path" (cache disabled or a
    legacy key). The artifact is read at most once per call, and not at all
    when the key's video section is memoized and the body is cached or the
    client's copy is current.
    """
    cache = replay_response_cache if cache is None else cache
    replay_key = getattr(demo, "replay_storage_key", None)
    if not cache.enabled or not is_cacheable_replay_key(replay_key):
        return None

    resolved = _resolve(service, demo, cache, replay_key)
    if if_none_match_matches(if_none_match, resolved.etag):
        return CachedReplay(etag=resolved.etag, not_modified=True, gzip_body=None)

    body = cache.get(resolved.etag)
    if body is None:
        body, _ = _render_and_store(service, demo, cache, resolved)
    return CachedReplay(etag=resolved.etag, not_modified=False, gzip_body=body)


WarmOutcome = Literal["warmed", "present", "disabled", "uncacheable", "refused"]


def warm_replay_response(
    service: DemoService,
    demo: Demo,
    *,
    cache: ReplayResponseCache | None = None,
) -> WarmOutcome:
    """Store the response the route would build for this completed demo, ahead of its first GET.

    Takes the route's own miss path, so the body and its ETag are the ones a
    request would have produced. Owner-agnostic: the caller decides which demo
    to warm (replay_warmer.py checks it exists and is completed), and the route
    still runs its owner, 404 and 409 checks before it ever reads the entry.
    "refused" means the demo was deleted before or during the warm (the cache's
    recently-deleted guard), or the body exceeds the whole budget.
    """
    cache = replay_response_cache if cache is None else cache
    replay_key = getattr(demo, "replay_storage_key", None)
    if not cache.enabled:
        return "disabled"
    if not is_cacheable_replay_key(replay_key):
        return "uncacheable"
    if cache.refuses(demo.id):
        return "refused"
    resolved = _resolve(service, demo, cache, replay_key)
    if cache.contains(resolved.etag):
        return "present"
    if cache.refuses(demo.id):
        return "refused"
    _, stored = _render_and_store(service, demo, cache, resolved)
    return "warmed" if stored else "refused"


@dataclass(frozen=True)
class _Resolved:
    etag: str
    video_status: dict[str, Any]
    # The parsed replay when resolving had to read it (no video memo), else None.
    replay: dict[str, Any] | None


def _resolve(service: DemoService, demo: Demo, cache: ReplayResponseCache, replay_key: str) -> _Resolved:
    """The ETag of a cacheable demo's response; reads the artifact only when the video memo misses."""
    replay: dict[str, Any] | None = None
    internal_video = cache.video_for(replay_key)
    if internal_video is None:
        replay = _load_replay(service, demo)
        internal_video = _video_section(replay)
        cache.remember_video(replay_key, demo_id=demo.id, owner_id=demo.owner_id, video=internal_video)
    video_status = service.public_video_status(demo, internal_video=internal_video)
    return _Resolved(etag=replay_etag(replay_key, video_status), video_status=video_status, replay=replay)


def _render_and_store(
    service: DemoService,
    demo: Demo,
    cache: ReplayResponseCache,
    resolved: _Resolved,
) -> tuple[bytes, bool]:
    """The miss path, shared by the route and the warmer: build the gzip body and offer it to the cache."""
    replay = resolved.replay if resolved.replay is not None else _load_replay(service, demo)
    # Same composition as ReplayBlob.public_replay, with the video status the ETag hashed.
    public = _public_replay_contract(replay, resolved.video_status)
    encoded = json.dumps(public, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    body = gzip.compress(encoded, compresslevel=GZIP_LEVEL, mtime=0)
    stored = cache.put(resolved.etag, demo_id=demo.id, owner_id=demo.owner_id, body=body)
    return body, stored


def _load_replay(service: DemoService, demo: Demo) -> dict[str, Any]:
    replay = service.load_replay_blob(demo)
    if replay is None:
        raise ReplayArtifactMissingError(demo.id)
    return cast(dict[str, Any], replay)


def _video_section(replay: dict[str, Any]) -> dict[str, Any]:
    video = replay.get("video")
    if isinstance(video, dict):
        return video
    raise ValueError("Invalid replay video contract")
