import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.services.demo_service import DemoService


class PublicMediaProjectionTest(unittest.TestCase):
    def test_public_replay_replaces_internal_media_reference(self) -> None:
        demo = SimpleNamespace(id="demo-private-media", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            service.storage.write_bytes(
                f"local://videos/{demo.id}/clip.mp4",
                b"video-bytes",
            )
            service.write_replay_blob(
                demo.id,
                replay_contract(
                    demo.id,
                    storage_key=f"local://videos/{demo.id}/clip.mp4",
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            replay = service.public_replay(demo)

        self.assertIsNotNone(replay)
        video = replay["video"]
        self.assertEqual(video["url"], f"/demos/{demo.id}/media/video")
        self.assertNotIn("storageKey", video)
        self.assertNotIn("local://", str(replay))
        self.assertNotIn("/media/videos/", str(replay))
        self.assertEqual(replay["rounds"][0]["roundNumber"], 1)

    def test_public_replay_projects_only_explicit_contract_fields(self) -> None:
        demo = SimpleNamespace(id="demo-public-projection", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            storage_key = f"local://videos/{demo.id}/clip.mp4"
            service.storage.write_bytes(storage_key, b"video-bytes")
            replay = replay_contract(
                demo.id,
                storage_key=storage_key,
                url=f"/media/videos/{demo.id}/clip.mp4",
            )
            replay["replayStorageKey"] = f"local://replays/{demo.id}.json"
            replay["sourcePath"] = "/data/uploads/private.dem"
            replay["video"]["localMediaPath"] = "/data/videos/private.mp4"
            replay["players"] = [
                {
                    "id": "player-1",
                    "name": "Player One",
                    "side": "T",
                    "color": "#fff",
                    "storageKey": "local://uploads/private.dem",
                }
            ]
            replay["events"] = [
                {
                    "id": "event-1",
                    "type": "smoke",
                    "tick": 32,
                    "roundNumber": 1,
                    "source": "parser",
                    "playerIds": ["player-1"],
                    "label": "Smoke",
                    "metadata": {
                        "site": "A",
                        "localMediaPath": "/data/videos/private.mp4",
                    },
                    "sourcePath": "/data/uploads/private.dem",
                }
            ]
            service.write_replay_blob(demo.id, replay)

            projected = service.public_replay(demo)

        self.assertIsNotNone(projected)
        self.assertNotIn("replayStorageKey", projected)
        self.assertNotIn("sourcePath", projected)
        self.assertNotIn("localMediaPath", projected["video"])
        self.assertNotIn("storageKey", projected["players"][0])
        self.assertNotIn("sourcePath", projected["events"][0])
        self.assertEqual(projected["events"][0]["metadata"], {"site": "A"})
        self.assertNotIn("local://", str(projected))
        self.assertNotIn("/data/", str(projected))

    def test_replay_storage_reference_must_belong_to_the_demo(self) -> None:
        demo = SimpleNamespace(
            id="demo-owner-a",
            tick_rate=64,
            replay_storage_key="local://replays/demo-owner-b.json",
        )

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            service.write_replay_blob(
                "demo-owner-b",
                replay_contract(
                    "demo-owner-b",
                    storage_key="local://videos/demo-owner-b/clip.mp4",
                    url="/media/videos/demo-owner-b/clip.mp4",
                ),
            )

            replay = service.load_replay_blob(demo)

        self.assertIsNone(replay)

    def test_replay_payload_demo_id_must_match_the_database_demo(self) -> None:
        demo = SimpleNamespace(
            id="demo-owner-a",
            tick_rate=64,
            replay_storage_key="local://replays/demo-owner-a.json",
        )

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            service.write_replay_blob(
                demo.id,
                replay_contract(
                    "demo-owner-b",
                    storage_key="local://videos/demo-owner-b/clip.mp4",
                    url="/media/videos/demo-owner-b/clip.mp4",
                ),
            )

            replay = service.load_replay_blob(demo)

        self.assertIsNone(replay)

    def test_public_video_status_hides_internal_reference(self) -> None:
        demo = SimpleNamespace(id="demo-private-status", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            service.storage.write_bytes(
                f"local://videos/{demo.id}/clip.mp4",
                b"video-bytes",
            )
            service.write_replay_blob(
                demo.id,
                replay_contract(
                    demo.id,
                    storage_key=f"local://videos/{demo.id}/clip.mp4",
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            video = service.public_video_status(demo)

        self.assertEqual(video["url"], f"/demos/{demo.id}/media/video")
        self.assertNotIn("storageKey", video)

    def test_private_video_path_requires_demo_bound_local_video_key(self) -> None:
        demo = SimpleNamespace(id="demo-private-path", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            valid_key = f"local://videos/{demo.id}/clip.mp4"
            service.storage.write_bytes(valid_key, b"video-bytes")
            service.write_replay_blob(
                demo.id,
                replay_contract(
                    demo.id,
                    storage_key=valid_key,
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            path = service.get_private_video_path(demo)

        self.assertIsNotNone(path)
        self.assertEqual(path.name, "clip.mp4")

    def test_private_video_path_rejects_cross_demo_and_wrong_category(self) -> None:
        demo = SimpleNamespace(id="demo-private-invalid", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(db=None)
            for storage_key in (
                "local://videos/another-demo/clip.mp4",
                f"local://uploads/{demo.id}/clip.mp4",
                f"local://replays/{demo.id}/clip.mp4",
            ):
                service.write_replay_blob(
                    demo.id,
                    replay_contract(
                        demo.id,
                        storage_key=storage_key,
                        url=f"/media/videos/{demo.id}/clip.mp4",
                    ),
                )
                self.assertIsNone(service.get_private_video_path(demo))

    def test_private_video_path_rejects_symlink_escape(self) -> None:
        demo = SimpleNamespace(id="demo-private-symlink", tick_rate=64)

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            root = Path(directory)
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside-video")
            link = settings.video_storage_dir / demo.id / "clip.mp4"
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside)

            service = DemoService.for_internal(db=None)
            service.write_replay_blob(
                demo.id,
                replay_contract(
                    demo.id,
                    storage_key=f"local://videos/{demo.id}/clip.mp4",
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            self.assertIsNone(service.get_private_video_path(demo))


def replay_contract(demo_id: str, *, storage_key: str, url: str) -> dict:
    return {
        "demoId": demo_id,
        "mapName": "de_dust2",
        "tickRate": 64,
        "video": {
            "status": "ready",
            "url": url,
            "storageKey": storage_key,
            "durationSeconds": 10,
            "tickStart": 0,
            "tickEnd": 640,
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
                "endTick": 640,
            }
        ],
        "players": [],
        "frames": [],
        "events": [],
        "generatedAt": "2026-07-16T00:00:00Z",
    }


@contextmanager
def storage_dirs(root: Path):
    original = (
        settings.artifact_storage_root,
        settings.replay_storage_dir,
        settings.demo_upload_storage_dir,
        settings.video_storage_dir,
        settings.summary_storage_dir,
    )
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    object.__setattr__(settings, "summary_storage_dir", root / "summaries")
    try:
        yield
    finally:
        (
            artifact_root,
            replay_dir,
            upload_dir,
            video_dir,
            summary_dir,
        ) = original
        object.__setattr__(settings, "artifact_storage_root", artifact_root)
        object.__setattr__(settings, "replay_storage_dir", replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", upload_dir)
        object.__setattr__(settings, "video_storage_dir", video_dir)
        object.__setattr__(settings, "summary_storage_dir", summary_dir)


if __name__ == "__main__":
    unittest.main()
