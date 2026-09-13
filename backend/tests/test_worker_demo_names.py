import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Demo, DemoJob
from app.services.demo_service import DemoService
from app.services.storage import LocalArtifactStore
from app.workers.worker import process_mock_parse_job, process_real_parse_job


class WorkerDemoNameTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = create_engine(f"sqlite:///{(root / 'names.db').as_posix()}")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.worker_service = patch(
            "app.workers.worker.DemoService.for_internal",
            side_effect=lambda db: DemoService(db, artifact_store=self.store, internal=True),
        )
        self.worker_service.start()
        self.redis = patch("app.services.demo_service.get_redis_client")
        self.redis.start()

    def tearDown(self):
        self.redis.stop()
        self.worker_service.stop()
        self.engine.dispose()
        self.scratch.cleanup()

    def create_upload(self, filename="xelex-nuke.dem"):
        with self.Session() as db:
            service = DemoService(db, owner_id="name-test-owner", artifact_store=self.store)
            created = service.create_real_demo(SimpleNamespace(
                filename=filename,
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + b"name-regression-payload" * 2),
            ))
            return created.id

    def test_default_uploaded_filename_survives_real_parse_completion(self):
        demo_id = self.create_upload()
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            self.assertEqual(demo.name, "xelex-nuke.dem")
            self.assertEqual(demo.original_filename, "xelex-nuke.dem")
            with patch("app.workers.worker.parse_demo_file", return_value=parsed_match()):
                process_real_parse_job(db, demo, job)
            db.refresh(demo)
            self.assertEqual(demo.status, "completed")
            self.assertEqual(demo.name, "xelex-nuke.dem")
            self.assertEqual(demo.map_name, "de_nuke")

    def test_user_rename_in_another_session_survives_parsing_and_analyzing(self):
        for rename_phase in ("parsing", "analyzing"):
            with self.subTest(phase=rename_phase):
                demo_id = self.create_upload()
                new_name = "验收 · xelex Nuke · 2026-09-12"

                def rename_if_phase(phase):
                    if phase == rename_phase:
                        with self.Session() as user_db:
                            service = DemoService(user_db, owner_id="name-test-owner", artifact_store=self.store)
                            self.assertEqual(service.get_demo(demo_id).status, phase)
                            service.update_demo(demo_id, name=new_name)

                def parse(_path):
                    rename_if_phase("parsing")
                    return parsed_match()

                def analyze(_replay):
                    rename_if_phase("analyzing")
                    return []

                with self.Session() as worker_db:
                    demo = worker_db.get(Demo, demo_id)
                    job = worker_db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
                    with patch("app.workers.worker.parse_demo_file", side_effect=parse), patch(
                        "app.workers.worker.analyze_replay", side_effect=analyze,
                    ):
                        process_real_parse_job(worker_db, demo, job)
                with self.Session() as read_db:
                    completed = read_db.get(Demo, demo_id)
                    self.assertEqual(completed.status, "completed")
                    self.assertEqual(completed.name, new_name)
                    self.assertEqual(completed.original_filename, "xelex-nuke.dem")
                    self.assertNotIn("parser spike", completed.name)

    def test_mock_name_is_unchanged_by_mock_parse_completion(self):
        with self.Session() as db:
            service = DemoService(db, owner_id="name-test-owner", artifact_store=self.store)
            created = service.create_mock_demo()
            demo = db.get(Demo, created.id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo.id).one()
            expected = f"Mock Match {demo.id[:8]}"
            self.assertEqual(demo.name, expected)
            with patch("app.workers.worker.time.sleep"):
                process_mock_parse_job(db, demo, job)
            db.refresh(demo)
            self.assertEqual(demo.status, "completed")
            self.assertEqual(demo.name, expected)


def parsed_match():
    return {
        "mapName": "de_nuke", "tickRate": 64,
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 128}],
        "players": [{"id": "xelex", "name": "xelex", "side": "T"}],
        "frames": [{"tick": 0, "players": [{"id": "xelex", "name": "xelex", "side": "T",
                                               "x": 100, "y": 200, "z": 0, "hp": 100, "alive": True}]}],
        "events": [], "kills": [], "deaths": [],
    }
