import time
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import coaching, demo, job  # noqa: F401

    for attempt in range(1, 31):
        try:
            Base.metadata.create_all(bind=engine)
            ensure_schema_backfills()
            return
        except OperationalError:
            if attempt == 30:
                raise
            time.sleep(1)


def ensure_schema_backfills() -> None:
    inspector = inspect(engine)
    if "demo_jobs" not in inspector.get_table_names():
        return

    column_names = {column["name"] for column in inspector.get_columns("demo_jobs")}
    if "metadata_json" not in column_names:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE demo_jobs ADD COLUMN metadata_json TEXT DEFAULT '{}' NOT NULL")
            )
