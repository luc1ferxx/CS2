import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from fixtures.filesystem import create_directory_link, requires_directory_links, requires_symlinks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.replay import get_replay
from app.core.config import settings
from app.core.database import Base
from app.models import Demo
from app.parser.map_config import map_metadata_for
from app.services.demo_service import DemoService
from app.services.storage import ArtifactReference


class PublicMediaProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()

    def test_public_replay_replaces_internal_media_reference(self) -> None:
        demo = add_demo(self.db, "demo-private-media")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            video_reference = write_accepted_video(service, demo, b"video-bytes")
            bind_replay(
                self.db,
                service,
                demo,
                replay_contract(
                    demo.id,
                    storage_key=video_reference,
                    url="https://objects.invalid/private/provider-key",
                ),
            )

            replay = service.public_replay(demo)

        self.assertIsNotNone(replay)
        video = replay["video"]
        self.assertEqual(video["url"], f"/demos/{demo.id}/media/video")
        self.assertNotIn("storageKey", video)
        self.assertNotIn("artifact://", str(replay))
        self.assertNotIn("objects.invalid", str(replay))
        self.assertNotIn("local://", str(replay))
        self.assertNotIn("/media/videos/", str(replay))
        self.assertEqual(replay["rounds"][0]["roundNumber"], 1)

    def test_public_replay_projects_only_explicit_contract_fields(self) -> None:
        demo = add_demo(self.db, "demo-public-projection")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            storage_key = write_accepted_video(service, demo, b"video-bytes")
            replay = replay_contract(
                demo.id,
                storage_key=storage_key,
                url="https://objects.invalid/private/provider-key",
            )
            replay["replayStorageKey"] = "artifact://private-replay-reference"
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
            bind_replay(self.db, service, demo, replay)

            projected = service.public_replay(demo)

        self.assertIsNotNone(projected)
        self.assertNotIn("replayStorageKey", projected)
        self.assertNotIn("sourcePath", projected)
        self.assertNotIn("localMediaPath", projected["video"])
        self.assertNotIn("storageKey", projected["players"][0])
        self.assertNotIn("sourcePath", projected["events"][0])
        self.assertEqual(projected["events"][0]["metadata"], {"site": "A"})
        self.assertNotIn("artifact://", str(projected))
        self.assertNotIn("objects.invalid", str(projected))
        self.assertNotIn("local://", str(projected))
        self.assertNotIn("/data/", str(projected))

    def test_replay_api_preserves_height_bomb_state_and_nuke_floor_metadata(self) -> None:
        demo = add_demo(self.db, "demo-nuke-height")
        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            replay = replay_contract(demo.id, storage_key="", url="")
            replay["mapName"] = "de_nuke"
            replay["mapMetadata"] = {**map_metadata_for("de_nuke"), "sourcePath": "/data/private-map.json"}
            replay["frames"] = [{
                "tick": 32, "roundNumber": 1,
                "players": [{"id": "player-1", "name": "xelex", "side": "T", "x": 30, "y": 40,
                             "z": -700, "alive": True, "hp": 80, "hasBomb": False,
                             "sourcePath": "/data/private-player.json"}],
                "bombState": {"status": "defused", "x": 30, "y": 40, "z": -700,
                              "sourcePath": "/data/private-bomb.json"},
            }]
            replay["events"] = [{"id": "plant", "type": "bomb_planted", "tick": 30,
                                 "x": 30, "y": 40, "z": -700, "playerId": "player-1",
                                 "sourcePath": "/data/private-event.json"}]
            bind_replay(self.db, service, demo, replay)

            response = get_replay(demo.id, db=self.db, owner_id=demo.owner_id)
            projected = json.loads(response.body)

        self.assertEqual(projected["frames"][0]["players"][0]["z"], -700)
        self.assertEqual(projected["frames"][0]["bombState"], {"status": "defused", "x": 30, "y": 40, "z": -700})
        self.assertEqual(projected["events"][0]["z"], -700)
        self.assertEqual(projected["mapMetadata"]["lowerLevelMaxZ"], -495)
        self.assertEqual(projected["mapMetadata"]["calibrationSource"], map_metadata_for("de_nuke")["calibrationSource"])
        self.assertNotIn("sourcePath", str(projected))
        self.assertNotIn("/data/", str(projected))

    def test_public_replay_rounds_height_and_drops_the_duplicate_kill_lists(self) -> None:
        demo = add_demo(self.db, "demo-public-height")
        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            replay = replay_contract(demo.id, storage_key="", url="")
            replay["frames"] = [{
                "tick": 32, "roundNumber": 1,
                "players": [{"id": "player-1", "name": "xelex", "side": "T", "x": 30.12, "y": 40.98,
                             "z": -167.96875000000003, "alive": True, "hp": 80},
                            {"id": "player-2", "name": "nozz", "side": "CT", "x": 31.0, "y": 41.0,
                             "alive": True, "hp": 100}],
                "bombState": {"status": "dropped", "x": 30, "y": 40, "z": 12.345678901234567},
            }]
            replay["events"] = [{"id": "kill-1", "type": "kill", "tick": 30, "roundNumber": 1,
                                 "x": 30, "y": 40, "z": -700.0400000001, "playerId": "player-1"}]
            kill = {"tick": 30, "roundNumber": 1, "attackerId": "player-1", "victimId": "player-2"}
            replay["kills"] = [kill]
            replay["deaths"] = [kill]
            bind_replay(self.db, service, demo, replay)

            projected = service.public_replay(demo)
            stored = service.load_replay_blob(demo)

        assert projected is not None and stored is not None
        players = projected["frames"][0]["players"]
        self.assertEqual(players[0]["z"], -168.0)
        self.assertNotIn("z", players[1])
        self.assertEqual(projected["frames"][0]["bombState"]["z"], 12.3)
        self.assertEqual(projected["events"][0]["z"], -700.0)
        # Only height: radar x/y keep their stored precision.
        self.assertEqual((players[0]["x"], players[0]["y"]), (30.12, 40.98))
        self.assertNotIn("kills", projected)
        self.assertNotIn("deaths", projected)
        # The stored replay (analyzer input) keeps the full values and both lists.
        self.assertEqual(stored["frames"][0]["players"][0]["z"], -167.96875000000003)
        self.assertEqual(stored["kills"], [kill])
        self.assertEqual(stored["deaths"], [kill])

    def test_replay_storage_reference_must_belong_to_the_demo(self) -> None:
        demo = add_demo(self.db, "demo-owner-a")
        foreign_demo = add_demo(self.db, "demo-owner-b")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            foreign_reference = bind_replay(
                self.db,
                service,
                foreign_demo,
                replay_contract(
                    "demo-owner-b",
                    storage_key="local://videos/demo-owner-b/clip.mp4",
                    url="/media/videos/demo-owner-b/clip.mp4",
                ),
            )
            demo.replay_storage_key = foreign_reference
            self.db.commit()

            replay = service.load_replay_blob(demo)

        self.assertIsNone(replay)

    def test_replay_payload_demo_id_must_match_the_database_demo(self) -> None:
        demo = add_demo(self.db, "demo-owner-a")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            bind_replay(
                self.db,
                service,
                demo,
                replay_contract(
                    "demo-owner-b",
                    storage_key="local://videos/demo-owner-b/clip.mp4",
                    url="/media/videos/demo-owner-b/clip.mp4",
                ),
            )

            replay = service.load_replay_blob(demo)

        self.assertIsNone(replay)

    def test_public_video_status_hides_internal_reference(self) -> None:
        demo = add_demo(self.db, "demo-private-status")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            video_reference = write_accepted_video(service, demo, b"video-bytes")
            bind_replay(
                self.db,
                service,
                demo,
                replay_contract(
                    demo.id,
                    storage_key=video_reference,
                    url="https://objects.invalid/private/provider-key",
                ),
            )

            video = service.public_video_status(demo)

        self.assertEqual(video["url"], f"/demos/{demo.id}/media/video")
        self.assertNotIn("storageKey", video)

    def test_private_video_path_requires_demo_bound_local_video_key(self) -> None:
        demo = add_demo(self.db, "demo-private-path")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            valid_key = f"local://videos/{demo.id}/clip.mp4"
            service.storage.write_bytes(valid_key, b"video-bytes")
            bind_replay(
                self.db,
                service,
                demo,
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
        demo = add_demo(self.db, "demo-private-invalid")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            service = DemoService.for_internal(self.db)
            service.storage.write_bytes(
                "local://videos/another-demo/clip.mp4",
                b"foreign-video",
            )
            service.storage.write_bytes(
                f"local://uploads/{demo.id}/clip.mp4",
                b"wrong-category",
            )
            for storage_key in (
                "local://videos/another-demo/clip.mp4",
                f"local://uploads/{demo.id}/clip.mp4",
                f"local://replays/{demo.id}/clip.mp4",
                f"local://videos/{demo.id}/../escape.mp4",
                f"local://videos/{demo.id}/nested/../../escape.mp4",
            ):
                bind_replay(
                    self.db,
                    service,
                    demo,
                    replay_contract(
                        demo.id,
                        storage_key=storage_key,
                        url=f"/media/videos/{demo.id}/clip.mp4",
                    ),
                )
                self.assertIsNone(service.get_private_video_path(demo))

    @requires_symlinks
    def test_private_video_path_rejects_symlink_escape(self) -> None:
        demo = add_demo(self.db, "demo-private-symlink")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            root = Path(directory)
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside-video")
            link = settings.video_storage_dir / demo.id / "clip.mp4"
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside)

            service = DemoService.for_internal(self.db)
            bind_replay(
                self.db,
                service,
                demo,
                replay_contract(
                    demo.id,
                    storage_key=f"local://videos/{demo.id}/clip.mp4",
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            self.assertIsNone(service.get_private_video_path(demo))

    @requires_directory_links
    def test_private_video_path_rejects_linked_demo_directory(self) -> None:
        demo = add_demo(self.db, "demo-private-linked-dir")

        with tempfile.TemporaryDirectory() as directory, storage_dirs(Path(directory)):
            root = Path(directory)
            outside_directory = root / "outside-videos"
            outside_directory.mkdir()
            (outside_directory / "clip.mp4").write_bytes(b"outside-video")
            demo_directory = settings.video_storage_dir / demo.id
            demo_directory.parent.mkdir(parents=True, exist_ok=True)
            # A junction is the unprivileged form of this escape on Windows, and
            # it is invisible to S_ISLNK, so the reparse flag has to catch it.
            create_directory_link(demo_directory, outside_directory)

            service = DemoService.for_internal(self.db)
            bind_replay(
                self.db,
                service,
                demo,
                replay_contract(
                    demo.id,
                    storage_key=f"local://videos/{demo.id}/clip.mp4",
                    url=f"/media/videos/{demo.id}/clip.mp4",
                ),
            )

            self.assertIsNone(service.get_private_video_path(demo))


def add_demo(db, demo_id: str) -> Demo:
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        source_storage_key=None,
        replay_storage_key=None,
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=0,
        status="completed",
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    return demo


def bind_replay(db, service: DemoService, demo: Demo, replay: dict) -> str:
    reference = service.write_replay_blob(demo.id, replay)
    parsed = ArtifactReference.parse(reference)
    if parsed.demo_id != demo.id or parsed.kind != "replay" or parsed.state != "accepted":
        raise AssertionError("Replay fixture did not produce a demo-bound accepted artifact")
    demo.replay_storage_key = reference
    db.commit()
    db.refresh(demo)
    return reference


def write_accepted_video(service: DemoService, demo: Demo, body: bytes) -> str:
    reference = service.artifact_store.new_reference(
        owner_id=demo.owner_id,
        demo_id=demo.id,
        kind="video",
        state="accepted",
    )
    metadata = service.artifact_store.write_stream(
        reference,
        io.BytesIO(body),
        max_bytes=settings.max_video_upload_bytes,
        chunk_size=settings.upload_chunk_bytes,
        expected_size=len(body),
        content_type="video/mp4",
        policy_version="artifact_store_v1",
    )
    if service.artifact_store.head(reference) != metadata:
        raise AssertionError("Accepted video fixture was not readable after write")
    return reference


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
        settings.artifact_storage_backend,
        settings.artifact_storage_root,
        settings.replay_storage_dir,
        settings.demo_upload_storage_dir,
        settings.video_storage_dir,
        settings.summary_storage_dir,
    )
    object.__setattr__(settings, "artifact_storage_backend", "local")
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    object.__setattr__(settings, "summary_storage_dir", root / "summaries")
    try:
        yield
    finally:
        (
            artifact_backend,
            artifact_root,
            replay_dir,
            upload_dir,
            video_dir,
            summary_dir,
        ) = original
        object.__setattr__(settings, "artifact_storage_backend", artifact_backend)
        object.__setattr__(settings, "artifact_storage_root", artifact_root)
        object.__setattr__(settings, "replay_storage_dir", replay_dir)
        object.__setattr__(settings, "demo_upload_storage_dir", upload_dir)
        object.__setattr__(settings, "video_storage_dir", video_dir)
        object.__setattr__(settings, "summary_storage_dir", summary_dir)


if __name__ == "__main__":
    unittest.main()
