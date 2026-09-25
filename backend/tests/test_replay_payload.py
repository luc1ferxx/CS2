import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from test_auth_owner_boundary import (
    OWNER_A,
    OWNER_B,
    FakeRedis,
    add_demo,
    add_job,
    bind_replay,
    owner_headers,
    render_job_metadata,
    replay_contract,
)

from app.api import demos, replay
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.json_compression import JsonGzipMiddleware, accepts_gzip
from app.services.demo_service.replay_blob import ReplayBlob

LARGE_JSON = {"items": [{"index": index, "label": f"row-{index}"} for index in range(400)]}


def _raw_body(client: TestClient, path: str, headers: dict[str, str]) -> tuple[dict[str, str], bytes]:
    # httpx decodes gzip on .content; iter_raw() shows what went over the wire.
    with client.stream("GET", path, headers=headers) as response:
        return dict(response.headers), b"".join(response.iter_raw())


class JsonGzipMiddlewareTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()

        @app.get("/large-json")
        def large_json() -> JSONResponse:
            return JSONResponse(LARGE_JSON)

        @app.get("/small-json")
        def small_json() -> JSONResponse:
            return JSONResponse({"ok": True})

        @app.get("/video")
        def video() -> Response:
            return Response(b"\x00" * 4096, media_type="video/mp4")

        @app.get("/streamed-json")
        def streamed_json() -> StreamingResponse:
            chunks = (json.dumps(LARGE_JSON)[index:index + 1024].encode() for index in range(0, 8192, 1024))
            return StreamingResponse(chunks, media_type="application/json")

        app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
        self.client = TestClient(app)

    def test_compresses_large_json_for_gzip_clients(self) -> None:
        headers, raw = _raw_body(self.client, "/large-json", {"Accept-Encoding": "gzip"})

        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertIn("Accept-Encoding", headers["vary"])
        self.assertEqual(int(headers["content-length"]), len(raw))
        self.assertEqual(json.loads(gzip.decompress(raw)), LARGE_JSON)
        self.assertLess(len(raw), len(json.dumps(LARGE_JSON)))

    def test_leaves_small_json_uncompressed(self) -> None:
        headers, raw = _raw_body(self.client, "/small-json", {"Accept-Encoding": "gzip"})

        self.assertNotIn("content-encoding", headers)
        self.assertEqual(json.loads(raw), {"ok": True})

    def test_respects_clients_that_do_not_accept_gzip(self) -> None:
        for accept_encoding in ("identity", "gzip;q=0", "br"):
            with self.subTest(accept_encoding=accept_encoding):
                headers, raw = _raw_body(self.client, "/large-json", {"Accept-Encoding": accept_encoding})
                self.assertNotIn("content-encoding", headers)
                self.assertEqual(json.loads(raw), LARGE_JSON)

    def test_never_touches_media_range_or_streamed_responses(self) -> None:
        video_headers, video_raw = _raw_body(self.client, "/video", {"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", video_headers)
        self.assertEqual(video_raw, b"\x00" * 4096)

        range_headers, _ = _raw_body(self.client, "/large-json", {"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
        self.assertNotIn("content-encoding", range_headers)

        streamed_headers, streamed_raw = _raw_body(self.client, "/streamed-json", {"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", streamed_headers)
        self.assertEqual(streamed_raw, json.dumps(LARGE_JSON)[:8192].encode())

    def test_still_compresses_beneath_a_base_http_middleware(self) -> None:
        # BaseHTTPMiddleware re-streams bodies in chunks, so gzip must sit inside it.
        class PassThrough(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
                return await call_next(request)

        app = FastAPI()

        @app.get("/large-json")
        def large_json() -> JSONResponse:
            return JSONResponse(LARGE_JSON)

        app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
        app.add_middleware(PassThrough)
        headers, raw = _raw_body(TestClient(app), "/large-json", {"Accept-Encoding": "gzip"})

        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(raw)), LARGE_JSON)

    def test_application_registers_gzip_innermost(self) -> None:
        from app.main import app as main_app

        # add_middleware prepends, so the innermost middleware is the last entry.
        self.assertIs(main_app.user_middleware[-1].cls, JsonGzipMiddleware)

    def test_accept_encoding_parsing(self) -> None:
        self.assertTrue(accepts_gzip("gzip, deflate, br"))
        self.assertTrue(accepts_gzip("br;q=1.0, GZIP;q=0.5"))
        self.assertFalse(accepts_gzip("gzip;q=0"))
        self.assertFalse(accepts_gzip("gzip;q=nonsense"))
        self.assertFalse(accepts_gzip("deflate, br"))
        self.assertFalse(accepts_gzip(""))


class ReplayPayloadApiTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_settings = {
            name: getattr(settings, name)
            for name in ("auth_mode", "artifact_storage_root", "replay_storage_dir", "demo_upload_storage_dir",
                         "video_storage_dir")
        }
        object.__setattr__(settings, "auth_mode", "test")
        object.__setattr__(settings, "artifact_storage_root", root)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
        object.__setattr__(settings, "video_storage_dir", root / "videos")
        self.redis_patch = patch("app.services.demo_service.get_redis_client", return_value=FakeRedis())
        self.redis_patch.start()

        app = FastAPI()
        app.include_router(demos.router)
        app.include_router(replay.router)
        app.add_middleware(JsonGzipMiddleware, minimum_size=1024, compresslevel=5)
        app.dependency_overrides[get_db] = self.override_get_db
        self.client = TestClient(app)

        with self.Session() as db:
            demo = add_demo(db, "demo-payload", OWNER_A)
            contract = replay_contract(demo.id)
            contract["players"] = [
                {"id": f"7656119800000000{index}", "name": f"Player {index}", "side": "T" if index < 5 else "CT"}
                for index in range(10)
            ]
            contract["frames"] = [
                {"tick": tick, "players": [{"id": player["id"], "x": tick, "y": -tick, "alive": True}
                                           for player in contract["players"]]}
                for tick in range(0, 640, 16)
            ]
            bind_replay(db, demo, contract)
            add_job(db, "job-render-payload", demo.id, "render_clip", status="queued", attempts=0,
                    metadata=render_job_metadata(0, 320))

    def tearDown(self) -> None:
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

    def test_replay_is_compact_json_and_gzipped_for_browsers(self) -> None:
        headers, raw = _raw_body(
            self.client, "/demos/demo-payload/replay", {**owner_headers(OWNER_A), "Accept-Encoding": "gzip, deflate"}
        )

        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertTrue(headers["content-type"].startswith("application/json"))
        body = gzip.decompress(raw)
        replay_payload = json.loads(body)
        self.assertEqual(replay_payload["demoId"], "demo-payload")
        self.assertEqual(len(replay_payload["frames"]), 40)
        self.assertEqual(body, json.dumps(replay_payload, ensure_ascii=False, separators=(",", ":")).encode())

        plain = self.client.get("/demos/demo-payload/replay", headers={**owner_headers(OWNER_A),
                                                                        "Accept-Encoding": "identity"})
        self.assertNotIn("content-encoding", plain.headers)
        self.assertEqual(plain.json(), replay_payload)

    def test_replay_keeps_owner_scoping_and_readiness_errors(self) -> None:
        with self.Session() as db:
            add_demo(db, "demo-parsing", OWNER_A, status="parsing")

        foreign = self.client.get("/demos/demo-payload/replay", headers=owner_headers(OWNER_B))
        parsing = self.client.get("/demos/demo-parsing/replay", headers=owner_headers(OWNER_A))

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(parsing.status_code, 409)

    def test_replay_video_and_render_polls_read_the_replay_blob_once(self) -> None:
        for path in ("/demos/demo-payload/replay", "/demos/demo-payload/video", "/demos/demo-payload/render/jobs"):
            with self.subTest(path=path), patch.object(
                ReplayBlob, "load_replay_blob", autospec=True, side_effect=ReplayBlob.load_replay_blob
            ) as load:
                response = self.client.get(path, headers=owner_headers(OWNER_A))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
