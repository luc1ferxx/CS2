import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import get_redis_client
from app.models.coaching import CoachingEvent
from app.models.demo import Demo
from app.models.job import DemoJob
from app.schemas.coaching import CoachingEventOut
from app.schemas.demo import DemoListItem
from app.services.upload_service import StoredVideoUpload, demo_upload_path, store_demo_upload


class DemoService:
    def __init__(self, db: Session):
        self.db = db

    def list_demos(self) -> list[DemoListItem]:
        demos = (
            self.db.query(Demo)
            .filter(Demo.user_id == settings.dev_user_id)
            .order_by(desc(Demo.created_at))
            .all()
        )
        return [DemoListItem.model_validate(demo) for demo in demos]

    def get_demo(self, demo_id: str) -> Demo | None:
        return (
            self.db.query(Demo)
            .filter(Demo.id == demo_id, Demo.user_id == settings.dev_user_id)
            .one_or_none()
        )

    def create_mock_demo(self) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        demo = Demo(
            id=demo_id,
            user_id=settings.dev_user_id,
            name=f"Mock Match {demo_id[:8]}",
            original_filename=f"mock_demo_{demo_id[:8]}.dem",
            map_name="de_inferno",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
        )
        job = DemoJob(
            id=job_id,
            demo_id=demo_id,
            job_type="mock_parse",
            status="queued",
            attempts=0,
        )

        self.db.add(demo)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo_id}),
        )

        return DemoListItem.model_validate(demo)

    async def create_real_demo(self, upload: Any) -> DemoListItem:
        demo_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        stored_upload = await store_demo_upload(demo_id, upload)

        demo = Demo(
            id=demo_id,
            user_id=settings.dev_user_id,
            name=f"Uploaded Demo {demo_id[:8]}",
            original_filename=stored_upload.original_filename,
            map_name="unknown",
            tick_rate=64,
            round_count=0,
            coaching_event_count=0,
            status="queued",
        )
        job = DemoJob(
            id=job_id,
            demo_id=demo_id,
            job_type="real_parse",
            status="queued",
            attempts=0,
        )

        self.db.add(demo)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(demo)

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo_id}),
        )

        return DemoListItem.model_validate(demo)

    def source_demo_path(self, demo: Demo) -> Path:
        return demo_upload_path(demo.id, demo.original_filename)

    def create_mock_render_job(self, demo: Demo) -> DemoJob:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")

        job_id = str(uuid.uuid4())
        job = DemoJob(
            id=job_id,
            demo_id=demo.id,
            job_type="mock_render",
            status="queued",
            attempts=0,
        )
        self.db.add(job)
        self.update_replay_video(
            demo,
            {
                **self.get_video_status(demo),
                "status": "queued",
                "source": "rendered",
                "url": None,
                "errorMessage": None,
            },
        )
        self.db.commit()

        get_redis_client().lpush(
            settings.redis_queue_name,
            json.dumps({"job_id": job_id, "demo_id": demo.id}),
        )
        return job

    def replay_blob_path(self, demo_id: str) -> Path:
        return settings.replay_storage_dir / f"{demo_id}.json"

    def write_replay_blob(self, demo_id: str, replay: dict[str, Any]) -> str:
        settings.replay_storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.replay_blob_path(demo_id)
        path.write_text(json.dumps(replay, separators=(",", ":")), encoding="utf-8")
        return f"local://replays/{demo_id}.json"

    def load_replay_blob(self, demo: Demo) -> dict[str, object] | None:
        path = self.replay_blob_path(demo.id)
        if not path.exists():
            return None
        replay = json.loads(path.read_text(encoding="utf-8"))
        return self._with_replay_contract_defaults(replay)

    def get_video_status(self, demo: Demo) -> dict[str, Any]:
        replay = self.load_replay_blob(demo)
        if replay is None:
            return {
                "status": "pending",
                "url": None,
                "durationSeconds": 0,
                "tickStart": 0,
                "tickEnd": 0,
                "tickRate": demo.tick_rate,
                "source": "mock",
                "errorMessage": None,
                "timeOriginSeconds": 0,
            }
        video = replay["video"]
        if isinstance(video, dict):
            return video
        raise ValueError("Invalid replay video contract")

    def attach_manual_video(self, demo: Demo, stored_video: StoredVideoUpload) -> dict[str, Any]:
        current_video = self.get_video_status(demo)
        return self.update_replay_video(
            demo,
            {
                **current_video,
                "status": "ready",
                "url": stored_video.url,
                "source": "manual_upload",
                "errorMessage": None,
            },
        )

    def update_video_calibration(
        self,
        demo: Demo,
        *,
        duration_seconds: float | None = None,
        tick_start: int | None = None,
        tick_end: int | None = None,
        tick_rate: int | None = None,
        time_origin_seconds: float | None = None,
    ) -> dict[str, Any]:
        current_video = self.get_video_status(demo)
        next_video = {**current_video}

        if duration_seconds is not None:
            if duration_seconds < 0:
                raise ValueError("durationSeconds must be zero or greater")
            next_video["durationSeconds"] = duration_seconds
        if tick_start is not None:
            next_video["tickStart"] = tick_start
        if tick_end is not None:
            next_video["tickEnd"] = tick_end
        if tick_rate is not None:
            if tick_rate <= 0:
                raise ValueError("tickRate must be greater than zero")
            next_video["tickRate"] = tick_rate
        if time_origin_seconds is not None:
            if time_origin_seconds < 0:
                raise ValueError("timeOriginSeconds must be zero or greater")
            next_video["timeOriginSeconds"] = time_origin_seconds

        if int(next_video["tickEnd"]) < int(next_video["tickStart"]):
            raise ValueError("tickEnd must be greater than or equal to tickStart")
        return self.update_replay_video(demo, next_video)

    def update_replay_video(self, demo: Demo, video: dict[str, Any]) -> dict[str, Any]:
        replay = self.load_replay_blob(demo)
        if replay is None:
            raise ValueError("Replay blob is not ready")
        replay["video"] = self._with_video_contract_defaults(video, replay)
        self.write_replay_blob(demo.id, replay)
        return replay["video"]

    def list_coaching_events(self, demo_id: str) -> list[CoachingEventOut]:
        events = (
            self.db.query(CoachingEvent)
            .filter(CoachingEvent.demo_id == demo_id)
            .order_by(CoachingEvent.tick_start.asc())
            .all()
        )
        return [CoachingEventOut.model_validate(event) for event in events]

    def _with_replay_contract_defaults(self, replay: dict[str, Any]) -> dict[str, Any]:
        if "video" in replay:
            replay["video"] = self._with_video_contract_defaults(replay["video"], replay)
            return replay

        rounds = replay.get("rounds", [])
        tick_rate = int(replay.get("tickRate", 64))
        tick_start = int(rounds[0]["startTick"]) if rounds else 0
        tick_end = int(rounds[-1]["endTick"]) if rounds else tick_start
        replay["video"] = self._with_video_contract_defaults(
            {
                "status": "ready",
                "url": None,
                "source": "mock",
            },
            replay,
        )
        return replay

    def _with_video_contract_defaults(
        self,
        video: dict[str, Any],
        replay: dict[str, Any],
    ) -> dict[str, Any]:
        rounds = replay.get("rounds", [])
        tick_rate = _positive_int_or_default(video.get("tickRate"), int(replay.get("tickRate", 64)))
        tick_start = _int_or_default(video.get("tickStart"), int(rounds[0]["startTick"]) if rounds else 0)
        tick_end = _int_or_default(video.get("tickEnd"), int(rounds[-1]["endTick"]) if rounds else tick_start)
        duration_seconds = video.get("durationSeconds")
        if duration_seconds is None:
            duration_seconds = round((tick_end - tick_start) / tick_rate, 2)
        time_origin_seconds = float(video.get("timeOriginSeconds", 0) or 0)

        return {
            **video,
            "status": video.get("status", "pending"),
            "url": video.get("url"),
            "durationSeconds": max(0, float(duration_seconds)),
            "tickStart": tick_start,
            "tickEnd": tick_end,
            "tickRate": tick_rate,
            "source": video.get("source", "mock"),
            "errorMessage": video.get("errorMessage"),
            "timeOriginSeconds": max(0, time_origin_seconds),
        }


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _positive_int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    return parsed if parsed > 0 else default
