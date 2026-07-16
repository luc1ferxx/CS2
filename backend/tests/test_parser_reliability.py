import io
import json
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Demo, DemoJob
from app.parser.demo_parser import DemoParserError, parse_demo_file
from app.parser.normalizer import normalize_parser_output
from app.services.artifact_intake import ArtifactIntakeService
from app.services.demo_service import DemoService
from app.workers.worker import fail_job, process_mock_parse_job, process_real_parse_job


class ParserErrorClassificationTest(unittest.TestCase):
    def test_unsupported_parser_input_is_classified(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            parse_demo_file(Path("match.rar"))

        self.assertEqual(raised.exception.error_code, "UNSUPPORTED_PARSER_FORMAT")
        self.assertEqual(raised.exception.user_message, "Unsupported demo parser input format.")

    def test_invalid_zip_upload_is_classified_without_leaking_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "not-a-real-demo.zip"
            source_path.write_bytes(b"not a zip archive")

            with self.assertRaises(DemoParserError) as raised:
                parse_demo_file(source_path)

        self.assertEqual(raised.exception.error_code, "INVALID_DEMO")
        self.assertIn("Invalid or unreadable demo", raised.exception.user_message)
        self.assertNotIn(str(source_path), raised.exception.user_message)

    def test_tiny_direct_demo_is_rejected_before_parser_panic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "tiny.dem"
            source_path.write_bytes(b"not a demo")

            with self.assertRaises(DemoParserError) as raised:
                parse_demo_file(source_path)

        self.assertEqual(raised.exception.error_code, "INVALID_DEMO")
        self.assertEqual(raised.exception.user_message, "Invalid or unreadable demo file.")

    def test_missing_essential_match_metadata_is_classified(self) -> None:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {}

            def parse_player_info(self) -> list[dict[str, object]]:
                return []

            def parse_event(self, *_: object, **__: object) -> list[dict[str, object]]:
                return []

            def parse_ticks(self, *_: object, **__: object) -> list[dict[str, object]]:
                raise AssertionError("parse_ticks should not run without sampleable match metadata")

        fake_module = types.SimpleNamespace(DemoParser=FakeDemoParser)
        with patch.dict("sys.modules", {"demoparser2": fake_module}), patch(
            "app.parser.demo_parser._validate_demo_file",
            return_value=None,
        ):
            with self.assertRaises(DemoParserError) as raised:
                parse_demo_file(Path("match.dem"))

        self.assertEqual(raised.exception.error_code, "MISSING_MATCH_METADATA")
        self.assertIn("essential match metadata", raised.exception.user_message)

    def test_missing_sampled_ticks_is_classified(self) -> None:
        class FakeDemoParser:
            def __init__(self, _: str):
                pass

            def parse_header(self) -> dict[str, object]:
                return {"map_name": "de_dust2", "tick_rate": 64, "playback_ticks": 128}

            def parse_player_info(self) -> list[dict[str, object]]:
                return []

            def parse_event(self, *_: object, **__: object) -> list[dict[str, object]]:
                return []

            def parse_ticks(self, *_: object, **__: object) -> list[dict[str, object]]:
                return []

        fake_module = types.SimpleNamespace(DemoParser=FakeDemoParser)
        with patch.dict("sys.modules", {"demoparser2": fake_module}), patch(
            "app.parser.demo_parser._validate_demo_file",
            return_value=None,
        ):
            with self.assertRaises(DemoParserError) as raised:
                parse_demo_file(Path("match.dem"))

        self.assertEqual(raised.exception.error_code, "MISSING_FRAMES")
        self.assertIn("player position ticks", raised.exception.user_message)


class ReplayContractSanitizationTest(unittest.TestCase):
    def test_normalizer_repairs_malformed_contract_fields_before_storage(self) -> None:
        replay = normalize_parser_output(
            "demo-sanitize",
            {
                "mapName": "de_cache",
                "tickRate": "not-a-number",
                "rounds": [
                    {"roundNumber": "bad", "startTick": 300, "freezeEndTick": 260, "endTick": 250},
                    {"roundNumber": 1, "startTick": 100, "freezeEndTick": 120, "endTick": 220},
                ],
                "players": [
                    {"id": "t-1", "name": "T One", "side": "T"},
                    {"name": "CT Fallback", "side": "CT"},
                ],
                "frames": [
                    {
                        "tick": 220,
                        "players": [
                            {
                                "id": "bad-coords",
                                "name": "Bad Coords",
                                "side": "T",
                                "x": float("nan"),
                                "y": 10,
                            }
                        ],
                    },
                    {
                        "tick": 100,
                        "roundNumber": "bad",
                        "timeSeconds": "bad",
                        "players": [
                            {
                                "id": "t-1",
                                "name": "T One",
                                "side": "T",
                                "x": -100.0,
                                "y": 40.0,
                                "hp": 100,
                            },
                            {
                                "name": "CT Fallback",
                                "side": "CT",
                                "x": 100.0,
                                "y": -40.0,
                                "hp": 100,
                            },
                        ],
                    },
                    {
                        "tick": "bad",
                        "players": [
                            {"id": "ignored", "name": "Ignored", "side": "T", "x": 0, "y": 0}
                        ],
                    },
                    {
                        "tick": 120,
                        "players": "not-a-list",
                    },
                ],
                "events": [
                    {"type": "smoke", "tick": 110, "x": float("nan"), "y": 12},
                    {"type": "bomb_planted", "tick": "bad"},
                    "not-an-event",
                ],
            },
        )

        self.assertEqual(replay["tickRate"], 64)
        self.assertEqual(replay["mapMetadata"]["confidence"], "fallback")
        self.assertEqual(
            [(round_info["startTick"], round_info["endTick"]) for round_info in replay["rounds"]],
            [(100, 220), (300, 300)],
        )
        self.assertEqual([frame["tick"] for frame in replay["frames"]], [100])
        self.assertEqual(replay["frames"][0]["timeSeconds"], 0.0)
        self.assertEqual(replay["frames"][0]["roundNumber"], 1)
        self.assertEqual([player["id"] for player in replay["frames"][0]["players"]], ["t-1", "CT Fallback"])
        self.assertEqual([event["type"] for event in replay["events"]], ["smoke"])
        self.assertNotIn("x", replay["events"][0])


class ParserWorkerReliabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_real_parse_failure_is_classified_and_next_job_can_complete(self) -> None:
        class ParserPanic(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                failed_demo, failed_job = add_demo_with_job(
                    db,
                    "demo-bad-parse",
                    "real_parse",
                )
                bind_source_artifact(db, failed_demo, failed_job)

                with patch(
                    "app.workers.worker.parse_demo_file",
                    side_effect=ParserPanic(
                        f"parser panicked while reading {directory}/private/bad.dem\ntraceback..."
                    ),
                ), patch("app.workers.worker._log_job_failure") as log_failure:
                    process_real_parse_job(db, failed_demo, failed_job)

                log_failure.assert_called_once()
                db.refresh(failed_demo)
                db.refresh(failed_job)
                failure = json.loads(failed_job.metadata_json)["failure"]
                self.assertEqual(failed_demo.status, "failed")
                self.assertEqual(failed_job.status, "failed")
                self.assertEqual(failure["errorCode"], "PARSER_UNEXPECTED")
                self.assertIn("unexpected parser error", failure["message"].lower())
                self.assertNotIn(directory, failure["message"])
                self.assertNotIn("traceback", failure["message"].lower())

                next_demo, next_job = add_demo_with_job(db, "demo-next-mock", "mock_parse")
                with patch("app.workers.worker.time.sleep", return_value=None):
                    process_mock_parse_job(db, next_demo, next_job)

                db.refresh(next_demo)
                db.refresh(next_job)
                self.assertEqual(next_demo.status, "completed")
                self.assertEqual(next_job.status, "completed")

    def test_normalization_failure_is_classified_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with storage_dirs(Path(directory)):
                db = self.Session()
                demo, job = add_demo_with_job(
                    db,
                    "demo-bad-normalize",
                    "real_parse",
                )
                bind_source_artifact(db, demo, job)

                with patch(
                    "app.workers.worker.parse_demo_file",
                    return_value={
                        "mapName": "de_dust2",
                        "tickRate": 64,
                        "rounds": [{"roundNumber": 1, "startTick": 0, "endTick": 128}],
                        "frames": [{"tick": 0, "players": [{"id": "bad", "x": float("nan"), "y": 0}]}],
                        "players": [],
                        "kills": [],
                        "deaths": [],
                        "events": [],
                    },
                ), patch("app.workers.worker._log_job_failure") as log_failure:
                    process_real_parse_job(db, demo, job)

                log_failure.assert_called_once()
                db.refresh(demo)
                db.refresh(job)
                failure = json.loads(job.metadata_json)["failure"]
                self.assertEqual(demo.status, "failed")
                self.assertEqual(job.status, "failed")
                self.assertEqual(failure["errorCode"], "NORMALIZATION_FAILED")
                self.assertEqual(
                    failure["message"],
                    "Parser output could not be normalized for replay review.",
                )

    def test_storage_read_failure_is_classified_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self.Session()
            demo, job = add_demo_with_job(db, "demo-storage-fail", "real_parse")

            fail_job(
                db,
                job.id,
                demo.id,
                OSError(f"could not open {directory}/private/source.dem"),
            )

            db.refresh(demo)
            db.refresh(job)
            failure = json.loads(job.metadata_json)["failure"]
            self.assertEqual(demo.status, "failed")
            self.assertEqual(job.status, "failed")
            self.assertEqual(failure["errorCode"], "STORAGE_READ_FAILED")
            self.assertEqual(
                failure["message"],
                "Uploaded demo artifact could not be read from storage.",
            )
            self.assertNotIn(directory, failure["message"])


@contextmanager
def storage_dirs(root: Path):
    original_artifact_root = settings.artifact_storage_root
    original_upload_dir = settings.demo_upload_storage_dir
    original_replay_dir = settings.replay_storage_dir
    original_video_dir = settings.video_storage_dir
    object.__setattr__(settings, "artifact_storage_root", root)
    object.__setattr__(settings, "demo_upload_storage_dir", root / "uploads")
    object.__setattr__(settings, "replay_storage_dir", root / "replays")
    object.__setattr__(settings, "video_storage_dir", root / "videos")
    try:
        yield
    finally:
        object.__setattr__(settings, "artifact_storage_root", original_artifact_root)
        object.__setattr__(settings, "demo_upload_storage_dir", original_upload_dir)
        object.__setattr__(settings, "replay_storage_dir", original_replay_dir)
        object.__setattr__(settings, "video_storage_dir", original_video_dir)


def add_demo_with_job(
    db,
    demo_id: str,
    job_type: str,
    *,
    source_storage_key: str | None = None,
) -> tuple[Demo, DemoJob]:
    demo = Demo(
        id=demo_id,
        owner_id=settings.dev_user_id,
        legacy_user_id=settings.dev_user_id,
        name=f"Demo {demo_id}",
        original_filename=f"{demo_id}.dem",
        source_storage_key=source_storage_key,
        map_name="unknown",
        tick_rate=64,
        round_count=0,
        coaching_event_count=0,
        status="queued",
    )
    job = DemoJob(
        id=f"job-{demo_id}",
        demo_id=demo_id,
        job_type=job_type,
        status="queued",
        attempts=0,
    )
    db.add(demo)
    db.add(job)
    db.commit()
    db.refresh(demo)
    db.refresh(job)
    return demo, job


def bind_source_artifact(db, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)
    accepted = ArtifactIntakeService(service.artifact_store).intake_demo(
        owner_id=demo.owner_id,
        demo_id=demo.id,
        filename=demo.original_filename,
        content_type="application/octet-stream",
        stream=io.BytesIO(b"HL2DEMO\x00parser-worker-fixture"),
    )
    demo.source_storage_key = accepted.reference
    job.metadata_json = json.dumps(
        {"phase": "uploaded", "sourceArtifact": accepted.as_snapshot()},
        separators=(",", ":"),
    )
    db.commit()
    db.refresh(demo)
    db.refresh(job)


if __name__ == "__main__":
    unittest.main()
