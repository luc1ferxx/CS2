import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ARTIFACT_STORAGE_ROOT = Path(os.getenv("ARTIFACT_STORAGE_ROOT", "/data"))


def _base_url_from_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_queue_name: str = os.getenv("REDIS_QUEUE_NAME", "cs2-demo-jobs")
    backend_public_url: str = _base_url_from_env("BACKEND_PUBLIC_URL", "http://localhost:8000")
    media_url_base: str = _base_url_from_env("MEDIA_URL_BASE")
    artifact_storage_root: Path = DEFAULT_ARTIFACT_STORAGE_ROOT
    replay_storage_dir: Path = Path(
        os.getenv("REPLAY_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "replays"))
    )
    demo_upload_storage_dir: Path = Path(
        os.getenv("DEMO_UPLOAD_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "uploads"))
    )
    video_storage_dir: Path = Path(
        os.getenv("VIDEO_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "videos"))
    )
    summary_storage_dir: Path = Path(
        os.getenv("SUMMARY_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "summaries"))
    )
    dev_user_id: str = os.getenv("DEV_USER_ID", "dev-user")
    max_render_clip_seconds: int = int(os.getenv("MAX_RENDER_CLIP_SECONDS", "60"))
    render_worker_token: str = os.getenv("RENDER_WORKER_TOKEN", "dev-render-worker-token")
    cors_origins_raw: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins_raw.split(",")
            if origin.strip()
        ]


settings = Settings()
