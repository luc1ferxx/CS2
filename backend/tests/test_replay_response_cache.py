"""GET /demos/{id}/replay response cache: ETag/304, cache hits, invalidation, owner boundary, deletion."""

import gzip
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_auth_owner_boundary import (
    OWNER_A,
    OWNER_B,
    FakeRedis,
    add_demo,
    bind_replay,
    owner_headers,
    replay_contract,
)

from app.api import deletion, replay
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.json_compression import JsonGzipMiddleware
from app.models.account import Account
from app.models.demo import Demo
from app.services.deletion_service import DeletionService
from app.services.demo_service import DemoService
from app.services.demo_service import replay_blob as replay_blob_module
from app.services.demo_service import replay_response_cache as cache_module
from app.services.demo_service.replay_response_cache import (
    ReplayResponseCache,
    if_none_match_matches,
    replay_etag,
    replay_response_cache,
)
from app.services.demo_service.video_registry import VideoRegistry

DEMO_ID = "demo-cached"
OTHER_DEMO_ID = "demo-cached-other"
PATH = f"/demos/{DEMO_ID}/replay"
ETAG_PATTERN = re.compile(r'^"[0-9a-f]{32}"$')
SETTING_NAMES = (
    "auth_mode",
    "artifact_storage_root",
    "replay_storage_dir",
    "demo_upload_storage_dir",
    "video_storage_dir",
    "replay_response_cache_mb",
)


def _raw_body(client: TestClient, path: str, headers: dict[str, str]) -> tuple[dict[str, str], bytes]:
    # httpx decodes gzip on .content; iter_raw() shows what went over the wire.
    with client.stream("GET", path, headers=headers) as response:
        return dict(response.headers), b"".join(response.iter_raw())


def _contract(demo_id: str) -> dict[str, Any]:
    contract = replay_contract(demo_id)
    contract["players"] = [
        {"id": f"7656119800000000{index}", "name": f"Jürgen {index}", "side": "T" if index < 5 else "CT"}
        for index in range(10)
    ]
    contract["frames"] = [
        {"tick": tick, "players": [{"id": player["id"], "x": tick, "y": -tick, "alive": True}
                                   for player in contract["players"]]}
        for tick in range(0, 640, 16)
    ]
    return contract


class ReplayResponseCacheApiTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        # The deletion tests delete rows: enforce foreign keys like Postgres does.
        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_settings = {name: getattr(settings, name) for name in SETTING_NAMES}
        object.__setattr__(settings, "auth_mode", "test")
        object.__setattr__(settings, "artifact_storage_root", root)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
        object.__setattr__(settings, "video_storage_dir", root / "videos")
        object.__setattr__(settings, "replay_response_cache_mb", 64)
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=FakeRedis())
        self.redis_patch.start()
        replay_response_cache.clear()

        app = FastAPI()
        app.include_router(replay.router)
        app.include_router(deletion.router)
        app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
        app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(app)

        with self.Session() as db:
            bind_replay(db, add_demo(db, DEMO_ID, OWNER_A), _contract(DEMO_ID))
            bind_replay(db, add_demo(db, OTHER_DEMO_ID, OWNER_B), _contract(OTHER_DEMO_ID))

    def tearDown(self) -> None:
        replay_response_cache.clear()
        self.redis_patch.stop()
        for name, value in self.original_settings.items():
            object.__setattr__(settings, name, value)
        self.temp_dir.cleanup()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def get(self, path: str = PATH, owner: str = OWNER_A, **headers: str):
        return self.client.get(path, headers={**owner_headers(owner), **headers})

    def count_blob_reads(self):
        return patch.object(
            replay_blob_module,
            "read_accepted_replay_json",
            wraps=replay_blob_module.read_accepted_replay_json,
        )

    # -- ETag and 304 -------------------------------------------------------------

    def test_first_get_sends_an_etag_and_a_matching_revalidation_gets_304(self) -> None:
        first = self.get()

        self.assertEqual(first.status_code, 200)
        etag = first.headers["etag"]
        self.assertRegex(etag, ETAG_PATTERN)
        self.assertEqual(first.headers["cache-control"], "private, no-store")
        self.assertIn("Accept-Encoding", first.headers["vary"])
        self.assertEqual(first.json()["demoId"], DEMO_ID)

        for if_none_match in (etag, f"W/{etag}", f'"0123", {etag}', "*"):
            with self.subTest(if_none_match=if_none_match):
                revalidated = self.get(**{"If-None-Match": if_none_match})
                self.assertEqual(revalidated.status_code, 304)
                self.assertEqual(revalidated.content, b"")
                self.assertEqual(revalidated.headers["etag"], etag)
                self.assertEqual(revalidated.headers["cache-control"], "private, no-store")
                self.assertIn("Accept-Encoding", revalidated.headers["vary"])
                self.assertNotIn("content-encoding", revalidated.headers)

        stale = self.get(**{"If-None-Match": '"0123456789abcdef0123456789abcdef"'})
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.headers["etag"], etag)
        self.assertEqual(stale.json(), first.json())

    def test_a_cache_hit_and_a_revalidation_skip_the_blob_read(self) -> None:
        with self.count_blob_reads() as reads:
            first = self.get()
            self.assertEqual(reads.call_count, 1)
            second = self.get()
            revalidated = self.get(**{"If-None-Match": first.headers["etag"]})
            self.assertEqual(reads.call_count, 1)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first.content)
        self.assertEqual(second.headers["etag"], first.headers["etag"])
        self.assertEqual(revalidated.status_code, 304)
        self.assertEqual(replay_response_cache.stats()["entries"], 1)
        self.assertEqual(replay_response_cache.stats()["videoMemos"], 1)

    def test_the_etag_survives_a_restart_and_revalidates_without_projecting(self) -> None:
        etag = self.get().headers["etag"]
        replay_response_cache.clear()  # what a new process starts with

        with self.count_blob_reads() as reads, patch.object(
            cache_module, "_public_replay_contract", wraps=cache_module._public_replay_contract
        ) as project:
            revalidated = self.get(**{"If-None-Match": etag})

        self.assertEqual(revalidated.status_code, 304)
        self.assertEqual(revalidated.headers["etag"], etag)
        self.assertEqual(reads.call_count, 1)  # once, for the video section behind the ETag
        project.assert_not_called()
        self.assertEqual(replay_response_cache.stats()["entries"], 0)

    # -- invalidation -------------------------------------------------------------

    def test_a_video_status_change_issues_a_new_etag(self) -> None:
        first = self.get()
        self.assertIsNone(first.json()["video"]["url"])

        # Same replay key; the private video object appearing changes the public status.
        with patch.object(VideoRegistry, "private_video_available", return_value=True):
            changed = self.get(**{"If-None-Match": first.headers["etag"]})

        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.headers["etag"], first.headers["etag"])
        self.assertEqual(changed.json()["video"]["url"], f"/demos/{DEMO_ID}/media/video")

        back = self.get(**{"If-None-Match": changed.headers["etag"]})
        self.assertEqual(back.status_code, 200)
        self.assertEqual(back.headers["etag"], first.headers["etag"])

    def test_a_video_write_moves_the_replay_key_and_the_etag(self) -> None:
        first = self.get()
        with self.Session() as db:
            demo = db.get(Demo, DEMO_ID)
            assert demo is not None
            service = DemoService(db, owner_id=OWNER_A)
            previous_key = demo.replay_storage_key
            current = service.get_video_status(demo)
            service.update_replay_video(
                demo,
                {**current, "status": "failed", "source": "rendered", "url": None,
                 "errorMessage": "GPU worker not connected for render_clip"},
            )
            self.assertNotEqual(demo.replay_storage_key, previous_key)

        after = self.get(**{"If-None-Match": first.headers["etag"]})

        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after.headers["etag"], first.headers["etag"])
        self.assertEqual(after.json()["video"]["status"], "failed")
        self.assertEqual(self.get(**{"If-None-Match": after.headers["etag"]}).status_code, 304)

    # -- encodings ----------------------------------------------------------------

    def test_gzip_and_identity_clients_get_the_same_json_cold_and_warm(self) -> None:
        object.__setattr__(settings, "replay_response_cache_mb", 0)
        _, uncached_plain = _raw_body(self.client, PATH, {**owner_headers(OWNER_A), "Accept-Encoding": "identity"})
        object.__setattr__(settings, "replay_response_cache_mb", 64)

        for order in (("gzip", "identity"), ("identity", "gzip")):
            replay_response_cache.clear()
            seen: dict[str, tuple[dict[str, str], bytes]] = {}
            for encoding in order + order:  # cold, then warm
                seen[encoding] = _raw_body(self.client, PATH, {**owner_headers(OWNER_A), "Accept-Encoding": encoding})
                headers, raw = seen[encoding]
                with self.subTest(order=order, encoding=encoding):
                    self.assertEqual(int(headers["content-length"]), len(raw))
                    self.assertIn("Accept-Encoding", headers["vary"])
                    self.assertRegex(headers["etag"], ETAG_PATTERN)
            gzip_headers, gzip_raw = seen["gzip"]
            plain_headers, plain_raw = seen["identity"]
            self.assertEqual(gzip_headers["content-encoding"], "gzip")
            self.assertNotIn("content-encoding", plain_headers)
            self.assertEqual(gzip.decompress(gzip_raw), plain_raw)
            self.assertEqual(gzip_headers["etag"], plain_headers["etag"])
            # Byte for byte what the uncached path sends, compact and UTF-8.
            self.assertEqual(plain_raw, uncached_plain)
            self.assertIn("Jürgen 0".encode(), plain_raw)  # raw UTF-8, not \u escapes
            self.assertEqual(json.loads(plain_raw)["demoId"], DEMO_ID)

    # -- boundaries ---------------------------------------------------------------

    def test_another_owner_gets_404_never_cached_content(self) -> None:
        etag = self.get().headers["etag"]

        for headers in ({}, {"If-None-Match": etag}, {"If-None-Match": "*"}):
            with self.subTest(headers=headers):
                foreign = self.get(owner=OWNER_B, **headers)
                self.assertEqual(foreign.status_code, 404)
                self.assertEqual(foreign.json(), {"detail": "Demo not found"})
                self.assertNotIn("etag", foreign.headers)

    def test_unfinished_and_missing_replays_keep_their_errors(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-parsing", OWNER_A, status="parsing")
            demo = db.get(Demo, DEMO_ID)
            assert demo is not None
            DemoService(db, owner_id=OWNER_A).artifact_store.delete(demo.replay_storage_key)

        self.assertEqual(self.get("/demos/demo-parsing/replay").status_code, 409)
        missing = self.get()
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"detail": "Replay blob not found"})
        self.assertFalse(replay_response_cache.holds_demo(DEMO_ID))

    def test_zero_megabytes_turns_the_cache_and_its_etag_off(self) -> None:
        object.__setattr__(settings, "replay_response_cache_mb", 0)

        with self.count_blob_reads() as reads:
            first = self.get()
            second = self.get(**{"If-None-Match": "*"})

        for response in (first, second):
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("etag", response.headers)
            self.assertNotIn("cache-control", response.headers)
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(replay_response_cache.stats(), {"entries": 0, "bytes": 0, "videoMemos": 0})

    def test_legacy_local_replay_keys_keep_the_uncached_path(self) -> None:
        with self.Session() as db:
            demo = add_demo(db, "demo-legacy", OWNER_A)
            self.assertTrue(demo.replay_storage_key.startswith("local://"))
            DemoService(db, owner_id=OWNER_A).storage.write_json(demo.replay_storage_key, _contract("demo-legacy"))

        response = self.get("/demos/demo-legacy/replay")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["demoId"], "demo-legacy")
        self.assertNotIn("etag", response.headers)
        self.assertFalse(replay_response_cache.holds_demo("demo-legacy"))

    # -- deletion -----------------------------------------------------------------

    def test_deleting_a_demo_evicts_its_cached_responses(self) -> None:
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.get(f"/demos/{OTHER_DEMO_ID}/replay", owner=OWNER_B).status_code, 200)
        self.assertTrue(replay_response_cache.holds_demo(DEMO_ID))

        deleted = self.client.delete(f"/demos/{DEMO_ID}", headers=owner_headers(OWNER_A))

        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(replay_response_cache.holds_demo(DEMO_ID))
        self.assertTrue(replay_response_cache.holds_demo(OTHER_DEMO_ID))
        self.assertEqual(self.get().status_code, 404)

    def test_deleting_an_account_evicts_every_response_of_the_owner(self) -> None:
        with self.Session() as db:
            db.add(Account(owner_id=OWNER_A))
            bind_replay(db, add_demo(db, "demo-cached-second", OWNER_A), _contract("demo-cached-second"))
        self.get()
        self.get("/demos/demo-cached-second/replay")
        self.get(f"/demos/{OTHER_DEMO_ID}/replay", owner=OWNER_B)

        with self.Session() as db:
            self.assertTrue(DeletionService(db).delete_account(OWNER_A, revoke_sessions=lambda _owner: None))

        self.assertFalse(replay_response_cache.holds_demo(DEMO_ID))
        self.assertFalse(replay_response_cache.holds_demo("demo-cached-second"))
        self.assertTrue(replay_response_cache.holds_demo(OTHER_DEMO_ID))

    def test_a_refused_delete_evicts_nothing(self) -> None:
        self.get()

        refused = self.client.delete(f"/demos/{DEMO_ID}", headers=owner_headers(OWNER_B))

        self.assertEqual(refused.status_code, 404)
        self.assertTrue(replay_response_cache.holds_demo(DEMO_ID))


class ReplayResponseCacheUnitTest(unittest.TestCase):
    def test_byte_budget_evicts_the_least_recently_used(self) -> None:
        budget = {"bytes": 100}
        cache = ReplayResponseCache(lambda: budget["bytes"])

        self.assertTrue(cache.put('"a"', demo_id="d-a", owner_id="o", body=b"a" * 40))
        self.assertTrue(cache.put('"b"', demo_id="d-b", owner_id="o", body=b"b" * 40))
        self.assertEqual(cache.get('"a"'), b"a" * 40)  # a is now the most recent
        self.assertTrue(cache.put('"c"', demo_id="d-c", owner_id="o", body=b"c" * 40))

        self.assertIsNone(cache.get('"b"'))
        self.assertIsNotNone(cache.get('"a"'))
        self.assertIsNotNone(cache.get('"c"'))
        self.assertEqual(cache.stats()["bytes"], 80)

        # A body larger than the whole budget is served but never kept.
        self.assertFalse(cache.put('"big"', demo_id="d-big", owner_id="o", body=b"x" * 101))
        self.assertEqual(cache.stats()["entries"], 2)

        # A smaller budget applies on the next store.
        budget["bytes"] = 50
        self.assertTrue(cache.put('"d"', demo_id="d-d", owner_id="o", body=b"d" * 10))
        self.assertEqual(cache.stats(), {"entries": 2, "bytes": 50, "videoMemos": 0})
        self.assertIsNone(cache.get('"a"'))

    def test_replacing_an_entry_keeps_the_byte_count_exact(self) -> None:
        cache = ReplayResponseCache(lambda: 100)
        cache.put('"a"', demo_id="d", owner_id="o", body=b"a" * 30)
        cache.put('"a"', demo_id="d", owner_id="o", body=b"a" * 20)

        self.assertEqual(cache.stats()["bytes"], 20)

    def test_video_memo_is_bounded(self) -> None:
        cache = ReplayResponseCache(lambda: 100, video_memo_max_entries=2)
        for index in range(3):
            cache.remember_video(f"artifact://k{index}", demo_id=f"d{index}", owner_id="o", video={"status": "pending"})

        self.assertIsNone(cache.video_for("artifact://k0"))
        self.assertEqual(cache.video_for("artifact://k2"), {"status": "pending"})

    def test_a_store_racing_a_delete_is_refused(self) -> None:
        cache = ReplayResponseCache(lambda: 1024)
        cache.put('"a"', demo_id="d1", owner_id="o", body=b"x" * 10)
        cache.remember_video("artifact://k", demo_id="d1", owner_id="o", video={"status": "pending"})
        cache.put('"b"', demo_id="d2", owner_id="o", body=b"y" * 10)

        self.assertEqual(cache.evict_demo("d1"), 2)
        # A request that read the replay before the delete stores after it.
        self.assertFalse(cache.put('"a"', demo_id="d1", owner_id="o", body=b"x" * 10))
        cache.remember_video("artifact://k", demo_id="d1", owner_id="o", video={"status": "pending"})

        self.assertFalse(cache.holds_demo("d1"))
        self.assertTrue(cache.holds_demo("d2"))
        self.assertEqual(cache.stats(), {"entries": 1, "bytes": 10, "videoMemos": 0})

    def test_owner_eviction_leaves_other_owners_alone(self) -> None:
        cache = ReplayResponseCache(lambda: 1024)
        cache.put('"a"', demo_id="d1", owner_id="o1", body=b"x")
        cache.remember_video("artifact://k1", demo_id="d1", owner_id="o1", video={})
        cache.put('"b"', demo_id="d2", owner_id="o2", body=b"y")

        self.assertEqual(cache.evict_owner("o1"), 2)
        self.assertEqual(cache.stats(), {"entries": 1, "bytes": 1, "videoMemos": 0})
        self.assertTrue(cache.holds_demo("d2"))

    def test_if_none_match_uses_weak_comparison(self) -> None:
        etag = '"0123abcd"'
        for header in ('"0123abcd"', 'W/"0123abcd"', '"x", "0123abcd"', "*", ' "0123abcd" ', "0123abcd"):
            with self.subTest(header=header):
                self.assertTrue(if_none_match_matches(header, etag))
        for header in (None, "", '"0123abcde"', 'W/"0123"', '"x"', '""'):
            with self.subTest(header=header):
                self.assertFalse(if_none_match_matches(header, etag))

    def test_the_etag_is_deterministic_and_covers_every_input(self) -> None:
        video = {"status": "pending", "url": None, "tickRate": 64}
        etag = replay_etag("artifact://k1", video)

        self.assertRegex(etag, ETAG_PATTERN)
        self.assertEqual(etag, replay_etag("artifact://k1", dict(reversed(list(video.items())))))
        self.assertNotEqual(etag, replay_etag("artifact://k2", video))
        self.assertNotEqual(etag, replay_etag("artifact://k1", {**video, "status": "ready"}))
        for name in ("PUBLIC_REPLAY_CACHE_VERSION", "REPLAY_CONTRACT_VERSION", "MAP_CONFIG_FINGERPRINT"):
            with self.subTest(name=name), patch.object(cache_module, name, "changed"):
                self.assertNotEqual(etag, replay_etag("artifact://k1", video))


if __name__ == "__main__":
    unittest.main()
