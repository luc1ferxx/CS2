"""Demo rows with a parse job attached, ready for worker and queue tests.

A parse job is only half a fixture: anything that goes through the retry or
recovery paths also reads the stored source artifact, so `bind_source_artifact`
puts a real file behind the row. Wrap that call in the caller's own storage_dirs
contextmanager so the bytes land in a temporary directory.
"""

from __future__ import annotations

import io
import json

from app.core.config import settings
from app.models import Demo, DemoJob
from app.services.artifact_intake import ArtifactIntakeService
from app.services.demo_service import DemoService


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
