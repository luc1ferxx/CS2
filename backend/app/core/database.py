import time
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

SCHEMA_UPGRADE_LOCK_ID = 7_302_202_607_190_001


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
    from app.migrations import run_schema_migrations
    from app.models import account, coaching, demo, job, steam  # noqa: F401

    for attempt in range(1, 31):
        try:
            with schema_upgrade_lock():
                with engine.begin() as connection:
                    run_schema_migrations(connection)
                Base.metadata.create_all(bind=engine)
                ensure_schema_backfills()
            return
        except OperationalError:
            if attempt == 30:
                raise
            time.sleep(1)


@contextmanager
def schema_upgrade_lock() -> Generator[None, None, None]:
    if engine.dialect.name != "postgresql":
        yield
        return

    connection = engine.connect()
    try:
        connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": SCHEMA_UPGRADE_LOCK_ID},
        )
        yield
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": SCHEMA_UPGRADE_LOCK_ID},
            )
        finally:
            connection.close()


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
                            "SET owner_id = NULLIF(user_id, '') "
                            "WHERE owner_id IS NULL OR owner_id = ''"
                        )
                    )
                if settings.auth_mode == "production":
                    missing_owner_count = connection.execute(
                        text(
                            "SELECT COUNT(*) FROM demos "
                            "WHERE owner_id IS NULL OR owner_id = ''"
                        )
                    ).scalar_one()
                    if missing_owner_count:
                        raise RuntimeError(
                            "Production startup cannot backfill demo owner without a legacy owner"
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
                if "user_id" in demo_column_names:
                    connection.execute(
                        text(
                            "UPDATE demos "
                            "SET owner_id = NULLIF(user_id, '') "
                            "WHERE (owner_id IS NULL OR owner_id = '') "
                            "AND user_id IS NOT NULL AND user_id != ''"
                        )
                    )
                if settings.auth_mode == "production":
                    missing_owner_count = connection.execute(
                        text(
                            "SELECT COUNT(*) FROM demos "
                            "WHERE owner_id IS NULL OR owner_id = ''"
                        )
                    ).scalar_one()
                    if missing_owner_count:
                        raise RuntimeError(
                            "Production startup cannot backfill demo owner without a legacy owner"
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
    if "queued_at" not in job_column_names:
        # demo_jobs is created by Base.metadata.create_all, which runs after the
        # versioned migrations in init_db() and would not see an ALTER from one.
        # This is the mechanism for adding a column to a create_all-managed
        # table, as the demos backfills above already do.
        timestamp_type = (
            "TIMESTAMP WITH TIME ZONE"
            if engine.dialect.name == "postgresql"
            else "TIMESTAMP"
        )
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE demo_jobs ADD COLUMN queued_at {timestamp_type}")
            )
            # Nothing has requeued these rows since the column did not exist, so
            # created_at is exactly when each of them entered the queue.
            connection.execute(
                text("UPDATE demo_jobs SET queued_at = created_at WHERE queued_at IS NULL")
            )
