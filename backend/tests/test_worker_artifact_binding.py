import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import Demo, DemoJob
from app.workers.worker import fail_job, process_job


class WorkerJobDemoBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_queue_payload_cannot_pair_a_job_with_another_demo(self) -> None:
        with self.Session() as db:
            demo_a = add_demo(db, "demo-a", "owner-a")
            demo_b = add_demo(db, "demo-b", "owner-b")
            job = DemoJob(
                id="job-b",
                demo_id=demo_b.id,
                job_type="real_parse",
                status="queued",
                attempts=0,
            )
            db.add(job)
            db.commit()

            with patch("app.workers.worker.process_real_parse_job") as process_parse:
                process_job(db, job.id, demo_a.id)

            process_parse.assert_not_called()
            db.refresh(job)
            db.refresh(demo_a)
            db.refresh(demo_b)
            self.assertEqual(job.status, "queued")
            self.assertEqual(demo_a.status, "queued")
            self.assertEqual(demo_b.status, "queued")

    def test_failure_handler_cannot_mark_an_unrelated_demo_failed(self) -> None:
        with self.Session() as db:
            demo_a = add_demo(db, "demo-a", "owner-a")
            demo_b = add_demo(db, "demo-b", "owner-b")
            job = DemoJob(
                id="job-b",
                demo_id=demo_b.id,
                job_type="real_parse",
                status="queued",
                attempts=0,
            )
            db.add(job)
            db.commit()

            fail_job(db, job.id, demo_a.id, RuntimeError("internal path /secret"))

            db.refresh(job)
            db.refresh(demo_a)
            db.refresh(demo_b)
            self.assertEqual(job.status, "queued")
            self.assertEqual(demo_a.status, "queued")
            self.assertEqual(demo_b.status, "queued")
            self.assertIsNone(job.error_message)
            self.assertIsNone(demo_a.error_message)


def add_demo(db, demo_id: str, owner_id: str) -> Demo:
    demo = Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=demo_id,
        original_filename=f"{demo_id}.dem",
        map_name="unknown",
        tick_rate=64,
        round_count=0,
        coaching_event_count=0,
        status="queued",
    )
    db.add(demo)
    db.commit()
    return demo


if __name__ == "__main__":
    unittest.main()
