"""A match deleted while work on it is in flight.

Deletion is allowed in any state, so every path that loaded a demo or job row
before writing to it must tolerate the row vanishing: the parse worker keeps
running, nothing is written back for the deleted match (no replay blob, no
coaching rows), and the API answers 404 -- never a 500, never a 400/409 about
artifacts that went with the match.

Deletion is simulated by removing the rows directly, in the order the real
deletion uses, at the exact interleaving points. SQLite enforces foreign keys
here (PRAGMA foreign_keys=ON on every connection) so a late child-row insert
fails the way it does on PostgreSQL.
"""

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.analysis.analyzer import analyze_replay
from app.api import coaching, demos
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.redis import get_redis_client
from app.models import CoachingEvent, CoachingFeedback, Demo, DemoJob
from app.models.steam import SteamMatch
from app.schemas.demo import RenderClipRequest
from app.services.demo_service import DemoService
from app.services.demo_service.demo_ingest import DemoIngest
from app.services.demo_service.demo_library import DemoLibrary
from app.services.demo_service.render_lifecycle import RenderLifecycle
from app.services.demo_service.replay_blob import ReplayBlob
from app.services.storage import LocalArtifactStore, artifact_store_from_settings
from app.workers import worker

OWNER = "dev-user"
XELEX_ID = "76561198998266210"
OTHER_ID = "76561190000000001"
SOURCE_BYTES = b"PBDEMS2\0" + b"\0" * 64
PARSE_LOGGER = "app.services.demo_service.parse_lifecycle"


def fk_engine(path: Path):
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


def delete_demo_rows(session_factory, demo_id: str) -> None:
    """What a match deletion does to the rows, from another connection."""
    with session_factory() as db:
        db.execute(update(SteamMatch).where(SteamMatch.demo_id == demo_id).values(demo_id=None))
        db.execute(delete(CoachingFeedback).where(CoachingFeedback.demo_id == demo_id))
        db.execute(delete(CoachingEvent).where(CoachingEvent.demo_id == demo_id))
        db.execute(delete(DemoJob).where(DemoJob.demo_id == demo_id))
        db.execute(delete(Demo).where(Demo.id == demo_id))
        db.commit()


def parsed_match() -> dict:
    return {
        "mapName": "de_nuke", "tickRate": 64,
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 128}],
        "players": [{"id": "xelex", "name": "xelex", "side": "T"}],
        "frames": [{"tick": 0, "players": [{"id": "xelex", "name": "xelex", "side": "T",
                                               "x": 100, "y": 200, "z": 0, "hp": 100, "alive": True}]}],
        "events": [], "kills": [], "deaths": [],
    }


class _StopWorker(BaseException):
    """Ends run_worker's endless loop once the scripted messages are used up."""


class FakeQueue:
    def __init__(self, payloads: list[str]):
        self.payloads = list(payloads)
        self.released: list[str] = []
        self.consumer_id = "test-consumer"

    def register(self) -> None: ...

    def unregister(self) -> None: ...

    def renew_lease(self) -> None: ...

    def reap(self, *, on_orphan) -> int:
        return 0

    def reserve(self, timeout: int) -> str:
        if not self.payloads:
            raise _StopWorker
        return self.payloads.pop(0)

    def release(self, payload: str) -> None:
        self.released.append(payload)


class WorkerDeletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "worker.db")
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        self.replay_writes: list[tuple[str, str]] = []
        original_write = ReplayBlob.write_replay_blob
        test = self

        def record_write(component, demo_id, replay):
            reference = original_write(component, demo_id, replay)
            test.replay_writes.append((demo_id, reference))
            return reference

        for patcher in (
            patch(
                "app.workers.worker.DemoService.for_internal",
                side_effect=lambda db: DemoService(db, artifact_store=self.store, internal=True),
            ),
            patch("app.services.demo_service.get_redis_client"),
            patch("app.workers.worker.SessionLocal", self.Session),
            patch.object(ReplayBlob, "write_replay_blob", record_write),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.scratch.cleanup()

    def create_upload(self, filename: str) -> tuple[str, str]:
        with self.Session() as db:
            service = DemoService(db, owner_id="deletion-owner", artifact_store=self.store)
            created = service.create_real_demo(SimpleNamespace(
                filename=filename,
                content_type="application/octet-stream",
                file=io.BytesIO(b"HL2DEMO\x00" + filename.encode() * 4),
            ))
            job = db.query(DemoJob).filter(DemoJob.demo_id == created.id).one()
            return created.id, job.id

    def assert_nothing_left_for(self, demo_id: str) -> None:
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(DemoJob).filter(DemoJob.demo_id == demo_id).count(), 0)
            self.assertEqual(db.query(CoachingEvent).filter(CoachingEvent.demo_id == demo_id).count(), 0)
        for written_for, reference in self.replay_writes:
            if written_for == demo_id:
                self.assertIsNone(self.store.head(reference), "A staged replay blob outlived its match")

    def test_worker_survives_a_demo_deleted_mid_parse_and_takes_the_next_job(self) -> None:
        deleted_id, deleted_job = self.create_upload("deleted.dem")
        next_id, next_job = self.create_upload("next.dem")
        payloads = [
            json.dumps({"job_id": deleted_job, "demo_id": deleted_id}),
            json.dumps({"job_id": next_job, "demo_id": next_id}),
        ]
        queue = FakeQueue(payloads)
        parses: list[Path] = []

        def parse(source_path, **_kwargs):
            parses.append(source_path)
            if len(parses) == 1:
                # The user deletes the match while the parse child is running.
                delete_demo_rows(self.Session, deleted_id)
            return parsed_match()

        with patch.object(worker, "ParseQueue", return_value=queue), patch.object(
            worker, "init_db",
        ), patch.object(worker, "get_redis_client", return_value=MagicMock()), patch(
            "app.core.config.Settings.validate_worker_runtime_configuration",
        ), patch.object(worker, "run_parse_subprocess", side_effect=parse), self.assertLogs(
            PARSE_LOGGER, level="INFO",
        ) as logs:
            with self.assertRaises(_StopWorker):
                worker.run_worker()

        self.assertEqual(len(parses), 2, "The worker must still be running for the next job")
        self.assertEqual(queue.released, payloads)
        self.assertTrue(any("demo deleted during parse" in line for line in logs.output))
        self.assert_nothing_left_for(deleted_id)
        self.assertEqual([demo for demo, _ in self.replay_writes], [next_id])
        with self.Session() as db:
            self.assertEqual(db.get(Demo, next_id).status, "completed")
            self.assertEqual(db.get(DemoJob, next_job).status, "completed")

    def test_a_parse_is_stopped_as_soon_as_its_match_is_deleted(self) -> None:
        demo_id, job_id = self.create_upload("stopped.dem")
        renewals: list[int] = []

        def parse(source_path, *, on_tick=None, **_kwargs):
            on_tick()  # still there: the parse carries on
            delete_demo_rows(self.Session, demo_id)
            on_tick()  # gone: the tick raises, which kills the child
            raise AssertionError("the parse should have been stopped")

        with self.Session() as db:
            demo, job = db.get(Demo, demo_id), db.get(DemoJob, job_id)
            with patch.object(worker, "PARSE_DELETION_CHECK_SECONDS", 0), patch.object(
                worker, "run_parse_subprocess", side_effect=parse,
            ), patch.object(DemoService, "fail_parse_job") as fail_parse_job:
                worker.process_real_parse_job(db, demo, job, on_tick=lambda: renewals.append(1))

        self.assertEqual(len(renewals), 2, "The lease and heartbeat still renew on every tick")
        fail_parse_job.assert_not_called()
        self.assertEqual(self.replay_writes, [])
        self.assert_nothing_left_for(demo_id)

    def test_the_deletion_check_is_rate_limited_and_an_unknown_answer_is_not_a_deletion(self) -> None:
        now = [0.0]
        answers = iter([RuntimeError("database away"), True, False])
        checks: list[int] = []

        def job_exists() -> bool:
            checks.append(1)
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        tick = worker.deletion_aware_tick(job_exists, interval_seconds=15, clock=lambda: now[0])
        tick()  # too soon: no query
        now[0] = 15
        tick()  # the check fails: carry on
        now[0] = 20
        tick()  # rate-limited again
        now[0] = 30
        tick()  # still there
        now[0] = 45
        with self.assertRaises(worker.ParseJobDeletedError):
            tick()
        self.assertEqual(len(checks), 3)

    def test_deletion_while_analyzing_writes_no_replay_or_coaching(self) -> None:
        demo_id, job_id = self.create_upload("analyzing.dem")

        def analyze(replay):
            delete_demo_rows(self.Session, demo_id)
            return analyze_replay(replay)

        with self.Session() as db:
            demo, job = db.get(Demo, demo_id), db.get(DemoJob, job_id)
            with patch.object(worker, "run_parse_subprocess", return_value=parsed_match()), patch.object(
                worker, "analyze_replay", side_effect=analyze,
            ), self.assertLogs(PARSE_LOGGER, level="INFO"):
                worker.process_real_parse_job(db, demo, job)

        self.assertEqual(self.replay_writes, [])
        self.assert_nothing_left_for(demo_id)

    def test_deletion_between_replay_write_and_commit_removes_the_staged_blob(self) -> None:
        demo_id, job_id = self.create_upload("committing.dem")
        original_write = ReplayBlob.write_replay_blob
        staged: list[str] = []

        def write_then_delete(component, target_demo_id, replay):
            reference = original_write(component, target_demo_id, replay)
            staged.append(reference)
            self.replay_writes.append((target_demo_id, reference))
            # The deletion commits after the blob is stored, before the parse commits.
            delete_demo_rows(self.Session, target_demo_id)
            return reference

        with self.Session() as db:
            demo, job = db.get(Demo, demo_id), db.get(DemoJob, job_id)
            with patch.object(worker, "run_parse_subprocess", return_value=parsed_match()), patch.object(
                ReplayBlob, "write_replay_blob", write_then_delete,
            ), self.assertLogs(PARSE_LOGGER, level="INFO"):
                worker.process_real_parse_job(db, demo, job)

        self.assertEqual(len(staged), 1)
        self.assertIsNone(self.store.head(staged[0]))
        self.assert_nothing_left_for(demo_id)

    def test_mock_parse_deleted_between_phases_stops_quietly(self) -> None:
        with self.Session() as db:
            created = DemoService(db, owner_id="deletion-owner", artifact_store=self.store).create_mock_demo()
            demo_id = created.id
            demo = db.get(Demo, demo_id)
            job = db.query(DemoJob).filter(DemoJob.demo_id == demo_id).one()
            with patch.object(worker.time, "sleep", side_effect=lambda _s: delete_demo_rows(self.Session, demo_id)):
                worker.process_mock_parse_job(db, demo, job)
        self.assert_nothing_left_for(demo_id)

    def test_fail_job_recovers_a_session_whose_flush_hit_the_deleted_row(self) -> None:
        demo_id, job_id = self.create_upload("failing.dem")
        with self.Session() as db:
            demo = db.get(Demo, demo_id)
            delete_demo_rows(self.Session, demo_id)
            demo.status = "analyzing"
            with self.assertRaises(StaleDataError):
                db.commit()
            # Before hardening this raised PendingRollbackError from inside the
            # worker's except block and took the whole worker down.
            worker.fail_job(db, job_id, demo_id, RuntimeError("parse failed"))
        self.assert_nothing_left_for(demo_id)


class ApiDeletionRaceTest(unittest.TestCase):
    QUEUE_WAIT = 0

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "api.db")
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        changed = {
            "auth_mode": "development",
            "artifact_storage_backend": "local",
            "artifact_storage_root": root / "artifacts",
            "replay_storage_dir": root / "replays",
            "render_worker_mode": "external",
        }
        self.original_settings = {key: getattr(settings, key) for key in changed}
        for key, value in changed.items():
            object.__setattr__(settings, key, value)
        self.app = FastAPI()
        self.app.include_router(demos.router)
        self.app.include_router(coaching.router)
        self.app.dependency_overrides[get_db] = self.override_get_db
        self.app.dependency_overrides[get_redis_client] = lambda: FakeRedis()
        self.client = TestClient(self.app)
        self.worker_headers = {"X-Render-Worker-Token": settings.render_worker_token}

    def tearDown(self) -> None:
        self.client.close()
        for key, value in self.original_settings.items():
            object.__setattr__(settings, key, value)
        self.engine.dispose()
        self.scratch.cleanup()

    def override_get_db(self):
        with self.Session() as db:
            yield db

    def seed_demo(self, *, status: str = "completed") -> str:
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER)
            prepared = service.prepare_real_demo(
                stream=io.BytesIO(SOURCE_BYTES), filename="sample.dem",
                content_type="application/octet-stream",
            )
            service.commit_prepared_real_demo(prepared)
            demo = prepared.demo
            demo.status = prepared.job.status = status
            demo.map_name = "de_nuke"
            replay = {
                "demoId": demo.id, "mapName": "de_nuke", "tickRate": 64,
                "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 5760}],
                "players": [{"id": XELEX_ID, "name": "xelex", "side": "T"},
                            {"id": OTHER_ID, "name": "other", "side": "CT"}],
                "frames": [], "events": [],
                "video": {"status": "ready", "source": "mock", "url": None,
                          "tickStart": 0, "tickEnd": 5760, "tickRate": 64,
                          "durationSeconds": 90, "timeOriginSeconds": 0},
            }
            demo.replay_storage_key = service.write_replay_blob(demo.id, replay)
            db.add(CoachingEvent(
                id=f"event-{demo.id}", demo_id=demo.id, round_number=1, player_id=XELEX_ID,
                player_name="xelex", tick_start=640, tick_end=832, category="positioning",
                severity="medium", title="Review spacing", message="Sampled distance suggests a review.",
                structured_context_json={"ruleId": "poor_spacing"}, confidence=0.7,
            ))
            db.commit()
            return demo.id

    def create_job(self, demo_id: str, *, claim: bool = False) -> str:
        with self.Session() as db:
            service = DemoService(db, owner_id=OWNER)
            request = RenderClipRequest(playerId=XELEX_ID, tickStart=640, tickEnd=1920, tickRate=64)
            job_id = service.create_render_clip_job(service.get_demo(demo_id), request).id
        if claim:
            claimed = self.client.get(f"/render-worker/jobs/{job_id}/manifest", headers=self.worker_headers)
            self.assertEqual(claimed.status_code, 200, claimed.text)
        return job_id

    def delete_after(self, owner, name: str, demo_id: str):
        """Let `owner.name` load its row, then delete the match before the caller writes."""
        original = getattr(owner, name)

        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            delete_demo_rows(self.Session, demo_id)
            return result

        return patch.object(owner, name, wrapper)

    def assert_gone(self, demo_id: str) -> None:
        with self.Session() as db:
            self.assertIsNone(db.get(Demo, demo_id))
            self.assertEqual(db.query(DemoJob).filter(DemoJob.demo_id == demo_id).count(), 0)
            self.assertEqual(db.query(CoachingFeedback).filter(CoachingFeedback.demo_id == demo_id).count(), 0)

    def test_rename_and_archive_of_a_demo_deleted_mid_request_answer_404(self) -> None:
        for method, path, body in (
            ("PATCH", "/demos/{id}", {"name": "Renamed"}),
            ("PATCH", "/demos/{id}", {"archived": False}),
            ("POST", "/demos/{id}/archive", None),
        ):
            with self.subTest(method=method, path=path, body=body):
                demo_id = self.seed_demo()
                with self.delete_after(DemoLibrary, "get_demo", demo_id):
                    response = self.client.request(method, path.format(id=demo_id), json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {"detail": "Demo not found"})
                self.assert_gone(demo_id)

    def test_parse_retry_of_a_demo_deleted_mid_request_answers_404_and_queues_nothing(self) -> None:
        demo_id = self.seed_demo(status="failed")
        with self.delete_after(DemoIngest, "verify_source_artifact", demo_id):
            response = self.client.post(f"/demos/{demo_id}/parse/retry")
        self.assertEqual(response.status_code, 404, response.text)
        self.assert_gone(demo_id)

    def test_verdict_on_a_demo_deleted_mid_request_answers_404_and_leaves_no_row(self) -> None:
        for existing in (False, True):
            with self.subTest(existing_verdict=existing):
                demo_id = self.seed_demo()
                path = f"/demos/{demo_id}/coaching/event-{demo_id}/feedback"
                if existing:
                    self.assertEqual(self.client.put(path, json={"verdict": "helpful"}).status_code, 200)
                real_now = datetime.now(UTC)

                def delete_then_now():
                    delete_demo_rows(self.Session, demo_id)
                    return real_now

                with patch("app.services.demo_service.coaching_review.utc_now", side_effect=delete_then_now):
                    response = self.client.put(path, json={"verdict": "irrelevant"})
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {"detail": "Demo not found"})
                self.assert_gone(demo_id)

    def test_render_clip_request_for_a_demo_deleted_mid_request_is_404_not_400(self) -> None:
        demo_id = self.seed_demo()
        with self.delete_after(DemoLibrary, "get_demo", demo_id):
            response = self.client.post(f"/demos/{demo_id}/render/clip", json={
                "playerId": XELEX_ID, "tickStart": 640, "tickEnd": 1920, "tickRate": 64,
            })
        self.assertEqual(response.status_code, 404, response.text)
        self.assert_gone(demo_id)

    def test_dev_video_routes_for_a_demo_deleted_mid_request_answer_404(self) -> None:
        stored = []
        original_store = demos.store_video_artifact

        def keep(**kwargs):
            video = original_store(**kwargs)
            stored.append(video)
            return video

        cases = {
            "calibration": lambda demo_id: self.client.post(
                f"/demos/{demo_id}/video/calibration", json={"timeOriginSeconds": 1.5}),
            "upload": lambda demo_id: self.client.post(
                f"/demos/{demo_id}/video/upload", files={"file": ("clip.mp4", b"manual clip", "video/mp4")}),
            "mock-render": lambda demo_id: self.client.post(f"/demos/{demo_id}/render/mock"),
        }
        for name, call in cases.items():
            with self.subTest(route=name):
                demo_id = self.seed_demo()
                with self.delete_after(DemoLibrary, "get_demo", demo_id), patch.object(
                    demos, "store_video_artifact", side_effect=keep,
                ):
                    response = call(demo_id)
                self.assertEqual(response.status_code, 404, response.text)
                self.assert_gone(demo_id)
        self.assertEqual(len(stored), 1)
        self.assertIsNone(
            artifact_store_from_settings().head(stored[0].storage_key),
            "The manual MP4 of a deleted match must not stay behind",
        )

    def test_media_upload_for_a_job_deleted_mid_upload_is_404_and_the_mp4_is_removed(self) -> None:
        demo_id = self.seed_demo()
        job_id = self.create_job(demo_id, claim=True)
        stored = []
        original_store = demos.store_video_artifact

        def store_then_delete(**kwargs):
            video = original_store(**kwargs)
            stored.append(video)
            # The deletion lands while the uploaded body is being stored.
            delete_demo_rows(self.Session, demo_id)
            return video

        with patch.object(demos, "store_video_artifact", side_effect=store_then_delete):
            response = self.client.post(
                f"/render-worker/jobs/{job_id}/media", headers=self.worker_headers,
                files={"file": ("clip.mp4", b"rendered clip", "video/mp4")},
            )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json(), {"detail": "Render clip job not found"})
        self.assertEqual(len(stored), 1)
        self.assertIsNone(artifact_store_from_settings().head(stored[0].storage_key))
        self.assert_gone(demo_id)

    def test_render_worker_routes_answer_a_uniform_404_for_a_job_deleted_mid_request(self) -> None:
        cases = {
            "manifest-claim": lambda job_id: self.client.get(
                f"/render-worker/jobs/{job_id}/manifest", headers=self.worker_headers),
            "manifest-read": lambda job_id: self.client.get(
                f"/render-worker/jobs/{job_id}/manifest", params={"claim": False}, headers=self.worker_headers),
            "source": lambda job_id: self.client.get(
                f"/render-worker/jobs/{job_id}/source", headers=self.worker_headers),
            "result-failed": lambda job_id: self.client.post(
                f"/render-worker/jobs/{job_id}/result", headers=self.worker_headers,
                json={"status": "failed", "tickStart": 640, "tickEnd": 1920, "tickRate": 64,
                      "timeOriginSeconds": 0, "durationSeconds": 20,
                      "errorMessage": "GPU worker not connected"}),
            "result-completed": lambda job_id: self.client.post(
                f"/render-worker/jobs/{job_id}/result", headers=self.worker_headers,
                json={"status": "completed", "storageKey": "artifact://nothing", "tickStart": 640,
                      "tickEnd": 1920, "tickRate": 64, "timeOriginSeconds": 0, "durationSeconds": 20}),
        }
        for name, call in cases.items():
            with self.subTest(route=name):
                demo_id = self.seed_demo()
                job_id = self.create_job(demo_id, claim=name != "manifest-claim")
                with self.delete_after(RenderLifecycle, "get_render_clip_job", demo_id):
                    response = call(job_id)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), {"detail": "Render clip job not found"})
                self.assert_gone(demo_id)
                # Once gone, a fresh request is the ordinary not-found answer.
                self.assertEqual(call(job_id).status_code, 404)

    def test_next_hands_out_nothing_when_the_job_vanishes_before_the_claim(self) -> None:
        demo_id = self.seed_demo()
        self.create_job(demo_id)
        with self.delete_after(RenderLifecycle, "next_render_clip_job", demo_id):
            response = self.client.get("/render-worker/jobs/next", headers=self.worker_headers)
        self.assertEqual(response.status_code, 204, response.text)
        self.assert_gone(demo_id)

    def test_render_sweeps_skip_a_deleted_job_without_losing_the_batch(self) -> None:
        for sweep in ("unclaimed", "stale"):
            with self.subTest(sweep=sweep):
                deleted_demo, kept_demo = self.seed_demo(), self.seed_demo()
                claim = sweep == "stale"
                self.create_job(deleted_demo, claim=claim)
                kept_job = self.create_job(kept_demo, claim=claim)
                finder = "_unclaimed_render_clip_jobs" if sweep == "unclaimed" else "_stale_render_clip_jobs"
                with self.delete_after(RenderLifecycle, finder, deleted_demo), self.Session() as db:
                    service = DemoService.for_internal(db)
                    if sweep == "unclaimed":
                        handled = service.fail_unclaimed_render_clip_jobs(older_than_seconds=self.QUEUE_WAIT)
                    else:
                        handled = service.reclaim_stale_render_clip_jobs(older_than_seconds=self.QUEUE_WAIT)
                self.assertEqual(handled, [kept_job])
                self.assert_gone(deleted_demo)
                with self.Session() as db:
                    expected = "failed" if sweep == "unclaimed" else "queued"
                    self.assertEqual(db.get(DemoJob, kept_job).status, expected)
                    # Leave the next subtest a clean queue.
                    delete_demo_rows(self.Session, kept_demo)

    def test_status_and_render_job_reads_of_a_deleted_demo_are_404(self) -> None:
        demo_id = self.seed_demo()
        delete_demo_rows(self.Session, demo_id)
        for path in (f"/demos/{demo_id}/status", f"/demos/{demo_id}/video", f"/demos/{demo_id}/render/jobs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, _: int, value: str) -> None:
        self.values[key] = value


if __name__ == "__main__":
    unittest.main()
