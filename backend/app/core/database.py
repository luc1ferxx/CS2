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
    table_names = set(inspector.get_table_names())

    if "demos" in table_names:
        demo_column_names = {column["name"] for column in inspector.get_columns("demos")}
        if "archived" not in demo_column_names:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE demos ADD COLUMN archived BOOLEAN DEFAULT FALSE NOT NULL")
                )
            demo_column_names.add("archived")
        if "owner_id" not in demo_column_names:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE demos ADD COLUMN owner_id VARCHAR(64)"))
                if "user_id" in demo_column_names:
                    connection.execute(
                        text(
                            "UPDATE demos "
                            "SET owner_id = COALESCE(NULLIF(user_id, ''), :default_owner) "
                            "WHERE owner_id IS NULL OR owner_id = ''"
                        ),
                        {"default_owner": settings.dev_user_id},
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE demos "
                            "SET owner_id = :default_owner "
                            "WHERE owner_id IS NULL OR owner_id = ''"
                        ),
                        {"default_owner": settings.dev_user_id},
                    )
                if engine.dialect.name == "postgresql":
                    connection.execute(text("ALTER TABLE demos ALTER COLUMN owner_id SET NOT NULL"))
            demo_column_names.add("owner_id")
        else:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE demos "
                        "SET owner_id = :default_owner "
                        "WHERE owner_id IS NULL OR owner_id = ''"
                    ),
                    {"default_owner": settings.dev_user_id},
                )

        if "user_id" not in demo_column_names:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE demos ADD COLUMN user_id VARCHAR(64)"))
                connection.execute(
                    text(
                        "UPDATE demos "
                        "SET user_id = COALESCE(NULLIF(owner_id, ''), :default_owner) "
                        "WHERE user_id IS NULL OR user_id = ''"
                    ),
                    {"default_owner": settings.dev_user_id},
                )
            demo_column_names.add("user_id")
        else:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE demos "
                        "SET user_id = COALESCE(NULLIF(owner_id, ''), :default_owner) "
                        "WHERE user_id IS NULL OR user_id = ''"
                    ),
                    {"default_owner": settings.dev_user_id},
                )

        if "source_storage_key" not in demo_column_names:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE demos ADD COLUMN source_storage_key VARCHAR(512)"))
                if {"id", "original_filename"}.issubset(demo_column_names):
                    connection.execute(
                        text(
                            "UPDATE demos "
                            "SET source_storage_key = 'local://uploads/' || id || '/' || original_filename "
                            "WHERE source_storage_key IS NULL OR source_storage_key = ''"
                        )
                    )
            demo_column_names.add("source_storage_key")
        elif {"id", "original_filename"}.issubset(demo_column_names):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE demos "
                        "SET source_storage_key = 'local://uploads/' || id || '/' || original_filename "
                        "WHERE source_storage_key IS NULL OR source_storage_key = ''"
                    )
                )

        if "replay_storage_key" not in demo_column_names:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE demos ADD COLUMN replay_storage_key VARCHAR(512)"))
                if {"id", "status"}.issubset(demo_column_names):
                    connection.execute(
                        text(
                            "UPDATE demos "
                            "SET replay_storage_key = 'local://replays/' || id || '.json' "
                            "WHERE status = 'completed' "
                            "AND (replay_storage_key IS NULL OR replay_storage_key = '')"
                        )
                    )
            demo_column_names.add("replay_storage_key")
        elif {"id", "status"}.issubset(demo_column_names):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE demos "
                        "SET replay_storage_key = 'local://replays/' || id || '.json' "
                        "WHERE status = 'completed' "
                        "AND (replay_storage_key IS NULL OR replay_storage_key = '')"
                    )
                )

        with engine.begin() as connection:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_demos_owner_id ON demos (owner_id)"))

    if "demo_jobs" not in table_names:
        return

    job_column_names = {column["name"] for column in inspector.get_columns("demo_jobs")}
    if "metadata_json" not in job_column_names:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE demo_jobs ADD COLUMN metadata_json TEXT DEFAULT '{}' NOT NULL")
            )
