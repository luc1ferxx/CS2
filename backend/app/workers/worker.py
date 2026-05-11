import json
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.analysis.analyzer import analyze_replay
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.redis import get_redis_client
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.demo_parser import parse_demo_file
from app.parser.normalizer import normalize_parser_output
from app.services.demo_service import RENDER_CLIP_JOB_TYPE, DemoService
from app.services.mock_replay_service import build_mock_replay


RENDER_CLIP_NOT_CONNECTED_ERROR = (
    "GPU worker not connected for render_clip. "
    "A separate Windows/Linux GPU worker or manual operator must process this job."
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def process_job(db: Session, job_id: str, demo_id: str) -> None:
    demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
    job = db.query(DemoJob).filter(DemoJob.id == job_id).one_or_none()
    if demo is None or job is None:
        return

    if job.job_type == RENDER_CLIP_JOB_TYPE:
        process_render_clip_job(db, demo, job)
        return
    if job.job_type == "mock_render":
        process_mock_render_job(db, demo, job)
        return
    if job.job_type == "real_parse":
        process_real_parse_job(db, demo, job)
        return
    if job.job_type != "mock_parse":
        raise ValueError(f"Unsupported job type: {job.job_type}")

    process_mock_parse_job(db, demo, job)


def process_mock_parse_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService(db)
    service.claim_parse_job(demo, job)

    time.sleep(1.2)

    service.mark_parse_analyzing(demo, job)
    time.sleep(1.2)

    replay, events = build_mock_replay(demo.id)
    service.complete_parse_job(demo, job, replay, events)


def process_real_parse_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService(db)

    service.claim_parse_job(demo, job)

    parsed = parse_demo_file(service.source_demo_path(demo))

    service.mark_parse_analyzing(demo, job)

    replay = normalize_parser_output(demo.id, parsed)
    events = analyze_replay(replay)
    service.complete_parse_job(
        demo,
        job,
        replay,
        events,
        name=f"{replay['mapName']} parser spike {demo.id[:8]}",
    )


def process_mock_render_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService(db)

    job.status = "processing"
    job.attempts += 1
    job.started_at = utc_now()
    service.update_replay_video(
        demo,
        {
            **service.get_video_status(demo),
            "status": "rendering",
            "source": "rendered",
            "url": None,
            "errorMessage": None,
        },
    )
    db.commit()

    time.sleep(1.4)

    service.update_replay_video(
        demo,
        {
            **service.get_video_status(demo),
            "status": "ready",
            "source": "rendered",
            "url": None,
            "errorMessage": None,
        },
    )
    job.status = "completed"
    job.finished_at = utc_now()
    job.error_message = None
    db.commit()


def process_render_clip_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService(db)

    if job.status != "queued":
        return

    if service.load_replay_blob(demo) is None:
        raise ValueError("Replay blob is not ready")

    job.status = "rendering"
    job.attempts += 1
    job.started_at = utc_now()
    job.error_message = None
    service.update_render_clip_video_status(demo, "rendering", None)
    db.commit()

    service.update_render_clip_video_status(demo, "failed", RENDER_CLIP_NOT_CONNECTED_ERROR)
    job.status = "failed"
    job.error_message = RENDER_CLIP_NOT_CONNECTED_ERROR
    job.finished_at = utc_now()
    db.commit()


def fail_job(db: Session, job_id: str, demo_id: str, error: str) -> None:
    demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
    job = db.query(DemoJob).filter(DemoJob.id == job_id).one_or_none()
    if demo is not None and job is not None and job.job_type == "mock_render":
        try:
            service = DemoService(db)
            service.update_replay_video(
                demo,
                {
                    **service.get_video_status(demo),
                    "status": "failed",
                    "source": "rendered",
                    "url": None,
                    "errorMessage": error[:1000],
                },
            )
        except Exception:
            traceback.print_exc()
    elif demo is not None and job is not None and job.job_type == RENDER_CLIP_JOB_TYPE:
        try:
            service = DemoService(db)
            service.update_render_clip_video_status(demo, "failed", error[:1000])
        except Exception:
            traceback.print_exc()
    elif demo is not None and job is not None and job.job_type in {"real_parse", "mock_parse"}:
        DemoService(db).fail_parse_job(demo, job, error)
        return
    elif demo is not None:
        demo.status = "failed"
        demo.error_message = error[:1000]
    if job is not None:
        job.status = "failed"
        job.error_message = error[:1000]
        job.finished_at = utc_now()
    db.commit()


def run_worker() -> None:
    init_db()
    redis_client = get_redis_client()
    print(f"Worker listening on Redis queue: {settings.redis_queue_name}", flush=True)

    while True:
        item = redis_client.brpop(settings.redis_queue_name, timeout=5)
        if item is None:
            continue

        _, raw_payload = item
        payload = json.loads(raw_payload)
        job_id = str(payload["job_id"])
        demo_id = str(payload["demo_id"])

        with SessionLocal() as db:
            try:
                process_job(db, job_id, demo_id)
                print(f"Completed job {job_id} for demo {demo_id}", flush=True)
            except Exception as exc:
                traceback.print_exc()
                fail_job(db, job_id, demo_id, str(exc))


if __name__ == "__main__":
    run_worker()
