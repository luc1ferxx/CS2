import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.analyzer import analyze_replay
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.redis import get_redis_client
from app.models.demo import Demo
from app.models.job import DemoJob
from app.parser.demo_parser import DemoParserError, parse_demo_file
from app.parser.normalizer import normalize_parser_output
from app.services.demo_service import (
    RENDER_CLIP_JOB_TYPE,
    RENDER_CLIP_NOT_CONNECTED_ERROR,
    RENDER_FAILED_ERROR_CODE,
    RENDER_FAILED_PUBLIC_MESSAGE,
    RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
    DemoService,
)
from app.services.diagnostics import write_worker_heartbeat
from app.services.storage import StorageKeyError
from app.services.mock_replay_service import build_mock_replay


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
    service = DemoService.for_internal(db)
    service.claim_parse_job(demo, job)

    time.sleep(1.2)

    service.mark_parse_analyzing(demo, job)
    time.sleep(1.2)

    replay, events = build_mock_replay(demo.id)
    service.complete_parse_job(demo, job, replay, events)


def process_real_parse_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)

    service.claim_parse_job(demo, job)

    try:
        parsed = parse_demo_file(service.source_demo_path(demo))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _log_job_failure(job.id, "parse", exc)
        _fail_classified_parse_job(service, demo, job, exc, phase="parse")
        return

    service.mark_parse_analyzing(demo, job)

    try:
        replay = normalize_parser_output(demo.id, parsed)
        events = analyze_replay(replay)
    except Exception as exc:
        _log_job_failure(job.id, "normalization", exc)
        _fail_classified_parse_job(service, demo, job, exc, phase="normalization")
        return

    service.complete_parse_job(
        demo,
        job,
        replay,
        events,
        name=f"{replay['mapName']} parser spike {demo.id[:8]}",
    )


def process_mock_render_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)

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
            "errorCode": None,
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
            "errorCode": None,
            "errorMessage": None,
        },
    )
    job.status = "completed"
    job.finished_at = utc_now()
    job.error_message = None
    db.commit()


def process_render_clip_job(db: Session, demo: Demo, job: DemoJob) -> None:
    service = DemoService.for_internal(db)

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

    service.update_render_clip_video_status(
        demo,
        "failed",
        RENDER_CLIP_NOT_CONNECTED_ERROR,
        error_code=RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
    )
    job.status = "failed"
    job.error_message = RENDER_CLIP_NOT_CONNECTED_ERROR
    job.finished_at = utc_now()
    db.commit()


def fail_job(db: Session, job_id: str, demo_id: str, error: Any) -> None:
    demo = db.query(Demo).filter(Demo.id == demo_id).one_or_none()
    job = db.query(DemoJob).filter(DemoJob.id == job_id).one_or_none()
    if demo is not None and job is not None and job.job_type == "mock_render":
        try:
            service = DemoService.for_internal(db)
            service.update_replay_video(
                demo,
                {
                    **service.get_video_status(demo),
                    "status": "failed",
                    "source": "rendered",
                    "url": None,
                    "errorCode": RENDER_FAILED_ERROR_CODE,
                    "errorMessage": RENDER_FAILED_PUBLIC_MESSAGE,
                },
            )
        except Exception:
            _log_job_failure(job.id, "mock-render-failure-update", error)
    elif demo is not None and job is not None and job.job_type == RENDER_CLIP_JOB_TYPE:
        try:
            service = DemoService.for_internal(db)
            service.update_render_clip_video_status(
                demo,
                "failed",
                RENDER_FAILED_PUBLIC_MESSAGE,
                error_code=RENDER_FAILED_ERROR_CODE,
            )
        except Exception:
            _log_job_failure(job.id, "render-clip-failure-update", error)
    elif demo is not None and job is not None and job.job_type in {"real_parse", "mock_parse"}:
        failure = _parse_failure_for_exception(error, phase="parse")
        DemoService.for_internal(db).fail_parse_job(
            demo,
            job,
            failure["message"],
            error_code=failure["errorCode"],
        )
        return
    elif demo is not None:
        demo.status = "failed"
        demo.error_message = "Background job failed. Retry the operation."
    if job is not None:
        job.status = "failed"
        job.error_message = (
            RENDER_FAILED_PUBLIC_MESSAGE
            if job.job_type in {"mock_render", RENDER_CLIP_JOB_TYPE}
            else "Background job failed. Retry the operation."
        )
        job.finished_at = utc_now()
    db.commit()


def run_worker() -> None:
    settings.validate_worker_runtime_configuration()
    init_db()
    redis_client = get_redis_client()
    print(f"Worker listening on Redis queue: {settings.redis_queue_name}", flush=True)

    while True:
        write_worker_heartbeat(redis_client)
        item = redis_client.brpop(settings.redis_queue_name, timeout=5)
        write_worker_heartbeat(redis_client)
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
                _log_job_failure(job_id, "process", exc)
                fail_job(db, job_id, demo_id, exc)
            finally:
                write_worker_heartbeat(redis_client)


def _fail_classified_parse_job(
    service: DemoService,
    demo: Demo,
    job: DemoJob,
    exc: BaseException,
    *,
    phase: str,
) -> None:
    failure = _parse_failure_for_exception(exc, phase=phase)
    service.fail_parse_job(
        demo,
        job,
        failure["message"],
        error_code=failure["errorCode"],
    )


def _parse_failure_for_exception(error: Any, *, phase: str) -> dict[str, str]:
    if isinstance(error, DemoParserError):
        return {
            "errorCode": error.error_code,
            "message": error.user_message,
        }

    if isinstance(error, (OSError, StorageKeyError)):
        return {
            "errorCode": "STORAGE_READ_FAILED",
            "message": "Uploaded demo artifact could not be read from storage.",
        }

    if phase == "normalization":
        return {
            "errorCode": "NORMALIZATION_FAILED",
            "message": "Parser output could not be normalized for replay review.",
        }

    return {
        "errorCode": "PARSER_UNEXPECTED",
        "message": "Unexpected parser error. Retry or upload a different demo.",
    }


def _log_job_failure(job_id: str, phase: str, error: BaseException) -> None:
    print(
        f"Worker job {job_id} failed during {phase}: {type(error).__name__}",
        flush=True,
    )


if __name__ == "__main__":
    run_worker()
