import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fixtures.filesystem import (
    CAN_UNLINK_OPEN_FILE,
    advance_mtime,
    create_directory_link,
    requires_directory_links,
    requires_symlinks,
    try_replace_with_link,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import private_media
from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import Demo
from app.services.demo_service import DemoService
from app.services.storage import ArtifactReference, LocalStorageService
from app.services.upload_service import store_video_artifact

OWNER_A = "owner-a"
VIDEO_BYTES = b"0123456789abcdef"


class PrivateMediaApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_artifact_storage_backend = settings.artifact_storage_backend
        self.original_artifact_storage_root = settings.artifact_storage_root
        self.original_replay_dir = settings.replay_storage_dir
        self.original_video_dir = settings.video_storage_dir
        root = Path(self.temp_dir.name)
        self.artifact_root = root / "artifacts"
        object.__setattr__(settings, "artifact_storage_backend", "local")
        object.__setattr__(settings, "artifact_storage_root", self.artifact_root)
        object.__setattr__(settings, "replay_storage_dir", root / "replays")
        object.__setattr__(settings, "video_storage_dir", root / "videos")

        self.app = FastAPI()
        self.app.include_router(private_media.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.app.dependency_overrides[get_current_owner_id] = lambda: OWNER_A
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()
        object.__setattr__(
            settings,
            "artifact_storage_backend",
            self.original_artifact_storage_backend,
        )
        object.__setattr__(
            settings,
            "artifact_storage_root",
            self.original_artifact_storage_root,
        )
        object.__setattr__(settings, "replay_storage_dir", self.original_replay_dir)
        object.__setattr__(settings, "video_storage_dir", self.original_video_dir)
        self.temp_dir.cleanup()
        self.engine.dispose()

    def override_get_db(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def test_owner_can_stream_private_video(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, VIDEO_BYTES)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["accept-ranges"], "bytes")

    def test_private_video_stream_does_not_use_unbounded_read_bytes(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, VIDEO_BYTES)

    def test_owner_can_seek_private_video_with_a_byte_range(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.get(
            "/demos/demo-owner-a/media/video",
            headers={"Range": "bytes=2-5"},
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, VIDEO_BYTES[2:6])
        self.assertEqual(response.headers["content-range"], "bytes 2-5/16")
        self.assertEqual(response.headers["content-length"], "4")

    def test_unsatisfiable_private_video_range_is_rejected(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.get(
            "/demos/demo-owner-a/media/video",
            headers={"Range": "bytes=99-120"},
        )

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["content-range"], "*/16")

    def test_owner_can_probe_private_video_with_head(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.head("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["content-length"], str(len(VIDEO_BYTES)))
        self.assertEqual(response.headers["accept-ranges"], "bytes")

    def test_private_video_endpoint_is_read_only(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.post("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 405)

    def test_private_video_disables_shared_or_persistent_caching(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(response.headers["vary"], "Cookie, Origin")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("content-disposition", response.headers)

    def test_production_private_video_rejects_non_same_origin_browser_fetches(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)
        original_auth_mode = settings.auth_mode
        object.__setattr__(settings, "auth_mode", "production")
        try:
            response = self.client.get(
                "/demos/demo-owner-a/media/video",
                headers={"Sec-Fetch-Site": "same-site"},
            )
        finally:
            object.__setattr__(settings, "auth_mode", original_auth_mode)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Media not found"})

    def test_copied_private_video_url_does_not_cross_owner_boundary(self) -> None:
        video_reference = self.add_ready_video("demo-owner-b", "owner-b")

        response = self.client.get("/demos/demo-owner-b/media/video")

        self.assert_private_not_found_without_artifact_details(
            response,
            video_reference,
        )

    def test_archived_demo_video_remains_available_to_its_owner(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A, archived=True)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, VIDEO_BYTES)

    def test_missing_private_video_is_not_found_without_path_details(self) -> None:
        video_reference = self.add_ready_video("demo-owner-a", OWNER_A)
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER_A)
            service.artifact_store.delete(video_reference)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assert_private_not_found_without_artifact_details(
            response,
            video_reference,
        )

    def test_private_video_rejects_another_demos_artifact_reference(self) -> None:
        self.add_ready_video("demo-owner-a", OWNER_A)
        other_reference = self.add_ready_video("demo-owner-b", OWNER_A)
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER_A)
            demo = service.get_demo("demo-owner-a")
            replay = service.load_replay_blob(demo)
            replay["video"]["storageKey"] = other_reference
            persist_replay(service, demo, replay)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assert_private_not_found_without_artifact_details(
            response,
            other_reference,
        )

    @requires_symlinks
    def test_private_video_rejects_symlinked_artifact(self) -> None:
        # Legacy local media remains supported only as an explicit compatibility boundary.
        self.add_ready_video("demo-owner-a", OWNER_A, legacy=True)
        video_path = settings.video_storage_dir / "demo-owner-a" / "clip.mp4"
        outside_path = Path(self.temp_dir.name) / "outside.mp4"
        outside_path.write_bytes(b"outside-video")
        video_path.unlink()
        video_path.symlink_to(outside_path)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Media not found"})
        self.assertNotIn(str(outside_path), response.text)

    @requires_directory_links
    def test_private_video_rejects_symlinked_demo_directory(self) -> None:
        # Legacy local media remains supported only as an explicit compatibility boundary.
        self.add_ready_video("demo-owner-a", OWNER_A, legacy=True)
        self.add_ready_video("demo-owner-b", "owner-b", legacy=True)
        owner_a_directory = settings.video_storage_dir / "demo-owner-a"
        owner_b_directory = settings.video_storage_dir / "demo-owner-b"
        (owner_a_directory / "clip.mp4").unlink()
        owner_a_directory.rmdir()
        # A junction is the unprivileged form of this attack on Windows, and it
        # is invisible to S_ISLNK, so it must be rejected by its reparse flag.
        create_directory_link(owner_a_directory, owner_b_directory)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Media not found"})
        self.assertNotIn("demo-owner-b", response.text)

    def test_private_video_stream_remains_bound_to_the_validated_file(self) -> None:
        # This test pins the already-open file descriptor in the legacy local adapter.
        # POSIX proves it by swapping the path for a link once the descriptor is
        # open. Windows refuses that swap outright while a handle is open, which
        # enforces the same guarantee earlier, so the swap is attempted and the
        # binding is asserted from the streamed bytes either way.
        self.add_ready_video("demo-owner-a", OWNER_A, legacy=True)
        video_path = settings.video_storage_dir / "demo-owner-a" / "clip.mp4"
        outside_path = Path(self.temp_dir.name) / "outside.mp4"
        outside_path.write_bytes(b"outside-video")
        original_open = LocalStorageService.open_video_for_demo
        replaced: list[bool] = []

        def open_then_replace(storage, demo_id, storage_key):
            opened = original_open(storage, demo_id, storage_key)
            replaced.append(try_replace_with_link(video_path, outside_path))
            return opened

        with patch.object(
            LocalStorageService,
            "open_video_for_demo",
            new=open_then_replace,
        ):
            response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, VIDEO_BYTES)
        self.assertNotEqual(response.content, outside_path.read_bytes())
        self.assertEqual(len(replaced), 1)
        if not replaced[0]:
            # The swap was refused, which only happens where the open handle
            # itself blocks the unlink. Assert that is why, and that the
            # validated file is still intact rather than silently gone.
            self.assertFalse(CAN_UNLINK_OPEN_FILE)
            self.assertEqual(video_path.read_bytes(), VIDEO_BYTES)

    def test_private_video_rejects_traversal_in_artifact_metadata(self) -> None:
        # Traversal syntax only exists in the legacy local storage-key format.
        self.add_ready_video("demo-owner-a", OWNER_A, legacy=True)
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER_A)
            demo = service.get_demo("demo-owner-a")
            replay = service.load_replay_blob(demo)
            replay["video"]["storageKey"] = (
                "local://videos/demo-owner-a/../../outside.mp4"
            )
            persist_replay(service, demo, replay)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Media not found"})
        self.assertNotIn("outside.mp4", response.text)

    def test_private_video_rejects_missing_artifact_generation_metadata(self) -> None:
        video_reference = self.add_ready_video("demo-owner-a", OWNER_A)
        _, metadata_path = self.accepted_artifact_paths(video_reference)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("generation")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assert_private_not_found_without_artifact_details(
            response,
            video_reference,
        )

    def test_private_video_rejects_accepted_content_drift(self) -> None:
        video_reference = self.add_ready_video("demo-owner-a", OWNER_A)
        data_path, _ = self.accepted_artifact_paths(video_reference)
        data_path.write_bytes(b"fedcba9876543210")
        advance_mtime(data_path)

        response = self.client.get("/demos/demo-owner-a/media/video")

        self.assert_private_not_found_without_artifact_details(
            response,
            video_reference,
        )

    def test_main_app_does_not_mount_public_video_storage(self) -> None:
        from app.main import app

        public_media_mounts = [
            route
            for route in app.routes
            if getattr(route, "path", None) == "/media/videos"
        ]

        self.assertEqual(public_media_mounts, [])

    def add_ready_video(
        self,
        demo_id: str,
        owner_id: str,
        *,
        archived: bool = False,
        legacy: bool = False,
    ) -> str:
        timestamp = datetime(2026, 7, 16, tzinfo=UTC)
        with self.Session() as db:
            demo = Demo(
                id=demo_id,
                owner_id=owner_id,
                legacy_user_id=owner_id,
                name=f"Demo {demo_id}",
                original_filename=f"{demo_id}.dem",
                map_name="de_dust2",
                tick_rate=64,
                round_count=1,
                coaching_event_count=0,
                status="completed",
                archived=archived,
                created_at=timestamp,
                updated_at=timestamp,
            )
            db.add(demo)
            db.commit()
            db.refresh(demo)

            service = DemoService(db, owner_id=owner_id)
            if legacy:
                video_key = service.storage.video_key(demo_id, "clip.mp4")
                service.storage.write_bytes(video_key, VIDEO_BYTES)
                video_url = f"/media/videos/{demo_id}/clip.mp4"
            else:
                stored_video = store_video_artifact(
                    owner_id=owner_id,
                    demo_id=demo_id,
                    upload=FakeUpload("clip.mp4", VIDEO_BYTES, "video/mp4"),
                    store=service.artifact_store,
                )
                video_key = stored_video.storage_key
                video_url = stored_video.url
            replay_reference = service.write_replay_blob(
                demo_id,
                {
                    "demoId": demo_id,
                    "mapName": "de_dust2",
                    "tickRate": 64,
                    "video": {
                        "status": "ready",
                        "url": video_url,
                        "storageKey": video_key,
                        "durationSeconds": 1,
                        "tickStart": 0,
                        "tickEnd": 64,
                        "tickRate": 64,
                        "source": "manual_upload",
                        "errorMessage": None,
                        "timeOriginSeconds": 0,
                    },
                    "rounds": [
                        {
                            "roundNumber": 1,
                            "startTick": 0,
                            "freezeEndTick": 0,
                            "endTick": 64,
                        }
                    ],
                    "players": [],
                    "frames": [],
                    "events": [],
                    "generatedAt": "2026-07-16T00:00:00Z",
                },
            )
            demo.replay_storage_key = replay_reference
            db.commit()
            return video_key

    def accepted_artifact_paths(self, reference: str) -> tuple[Path, Path]:
        artifact_id = ArtifactReference.parse(reference).artifact_id
        data_paths = list(self.artifact_root.rglob(f"{artifact_id}.blob"))
        metadata_paths = list(
            self.artifact_root.rglob(f"{artifact_id}.metadata.json")
        )
        self.assertEqual(len(data_paths), 1)
        self.assertEqual(len(metadata_paths), 1)
        return data_paths[0], metadata_paths[0]

    def assert_private_not_found_without_artifact_details(
        self,
        response,
        reference: str,
    ) -> None:
        parsed = ArtifactReference.parse(reference)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Media not found"})
        self.assertNotIn(reference, response.text)
        self.assertNotIn(parsed.artifact_id, response.text)
        self.assertNotIn("artifact://", response.text)
        self.assertNotIn(".blob", response.text)
        self.assertNotIn("storageKey", response.text)
        self.assertNotIn("bucket", response.text.lower())
        self.assertNotIn(str(self.artifact_root), response.text)


class FakeUpload:
    def __init__(self, filename: str, payload: bytes, content_type: str):
        self.filename = filename
        self.file = io.BytesIO(payload)
        self.content_type = content_type


def persist_replay(service: DemoService, demo: Demo, replay: dict) -> str:
    reference = service.write_replay_blob(demo.id, replay)
    demo.replay_storage_key = reference
    service.db.commit()
    return reference


if __name__ == "__main__":
    unittest.main()
