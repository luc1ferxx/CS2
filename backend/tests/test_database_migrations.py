import re
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.migrations.runner import MIGRATION_TABLE_NAME, MIGRATIONS, run_schema_migrations
from app.models import Account, ExternalIdentity, SteamConnection, SteamMatch, UploadSession


class DatabaseMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_fresh_and_repeated_upgrade_apply_all_tracked_migrations(self) -> None:
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
        with self.engine.begin() as connection:
            run_schema_migrations(connection)

        table_names = set(inspect(self.engine).get_table_names())
        self.assertTrue(
            {
                "accounts",
                "external_identities",
                "steam_connections",
                "steam_matches",
                MIGRATION_TABLE_NAME,
            }.issubset(table_names)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT version, name, checksum FROM {MIGRATION_TABLE_NAME}"
                    " ORDER BY version"
                )
            ).mappings().all()
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["version"], MIGRATIONS[0].version)
        self.assertEqual(rows[0]["checksum"], MIGRATIONS[0].checksum)
        self.assertEqual(
            MIGRATIONS[0].checksum,
            "51184be59b6bd615d8cd830dc8125d8e53ed85891c5c427583f1cf81d6cab9eb",
        )
        self.assertEqual(rows[1]["version"], "2026071902")
        self.assertEqual(rows[1]["checksum"], MIGRATIONS[1].checksum)
        self.assertEqual(rows[2]["version"], "2026071903")
        self.assertEqual(rows[2]["checksum"], MIGRATIONS[2].checksum)
        self.assertEqual(
            MIGRATIONS[2].checksum,
            "cdf48d440c6b366a8edf94250112c26a171c18bef1b3517a785ad161a7d54dbf",
        )
        self.assertEqual(rows[3]["version"], "2026092601")
        self.assertEqual(rows[3]["checksum"], MIGRATIONS[3].checksum)
        self.assertEqual(rows[4]["version"], "2026092701")
        self.assertEqual(rows[4]["checksum"], MIGRATIONS[4].checksum)
        self.assertEqual(rows[5]["version"], "2026100401")
        self.assertEqual(rows[5]["name"], "create_upload_sessions")
        self.assertEqual(rows[5]["checksum"], MIGRATIONS[5].checksum)
        self.assertTrue({"deletion_tasks", "upload_ledger", "upload_sessions"}.issubset(table_names))

        steam_match_columns = {
            column["name"] for column in inspect(self.engine).get_columns("steam_matches")
        }
        self.assertTrue(
            {
                "demo_id",
                "import_run_id",
                "import_lease_expires_at",
                "import_attempts",
                "provider_id",
                "last_import_error_code",
                "last_import_error_message",
                "import_started_at",
                "import_completed_at",
                "parser_dispatched_at",
                "parser_dispatched_job_id",
                "map_name",
                "duration_seconds",
                "ct_round_wins",
                "t_round_wins",
                "players_json",
            }.issubset(steam_match_columns)
        )
        unique_indexes = {
            index["name"]
            for index in inspect(self.engine).get_indexes("steam_matches")
            if index.get("unique")
        }
        self.assertIn("uq_steam_matches_demo_id", unique_indexes)

    def test_steam_sync_schema_enforces_owner_and_match_idempotency(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
        Session = sessionmaker(bind=self.engine)
        now = datetime.now(UTC)
        owner_id = "owner_v1_" + ("a" * 43)
        with Session() as db:
            db.add(Account(owner_id=owner_id, created_at=now, updated_at=now))
            db.commit()
            connection = SteamConnection(
                id="connection-a",
                owner_id=owner_id,
                steam_id64="76561202255233022",
                game_auth_code_ciphertext=b"ciphertext-and-tag",
                game_auth_code_nonce=b"1" * 12,
                known_code_ciphertext=b"ciphertext-and-tag",
                known_code_nonce=b"2" * 12,
                encryption_key_version="test-v1",
                status="connected",
                consecutive_failures=0,
                created_at=now,
                updated_at=now,
            )
            db.add(connection)
            db.commit()
            db.add_all(
                [
                    SteamMatch(
                        id="match-a",
                        connection_id=connection.id,
                        owner_id=owner_id,
                        share_code_hash="a" * 64,
                        share_code_ciphertext=b"ciphertext-and-tag",
                        share_code_nonce=b"3" * 12,
                        encryption_key_version="test-v1",
                        status="discovered",
                        discovered_at=now,
                        updated_at=now,
                    ),
                    SteamMatch(
                        id="match-b",
                        connection_id=connection.id,
                        owner_id=owner_id,
                        share_code_hash="a" * 64,
                        share_code_ciphertext=b"other-ciphertext-tag",
                        share_code_nonce=b"4" * 12,
                        encryption_key_version="test-v1",
                        status="discovered",
                        discovered_at=now,
                        updated_at=now,
                    ),
                ]
            )
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

            db.add(
                SteamConnection(
                    id="connection-b",
                    owner_id=owner_id,
                    steam_id64="76561202255233021",
                    game_auth_code_ciphertext=b"ciphertext-and-tag",
                    game_auth_code_nonce=b"5" * 12,
                    known_code_ciphertext=b"ciphertext-and-tag",
                    known_code_nonce=b"6" * 12,
                    encryption_key_version="test-v1",
                    status="connected",
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

            invalid_status = SteamMatch(
                id="match-invalid",
                connection_id=connection.id,
                owner_id=owner_id,
                share_code_hash="b" * 64,
                share_code_ciphertext=b"ciphertext-and-tag",
                share_code_nonce=b"7" * 12,
                encryption_key_version="test-v1",
                status="downloaded",
                discovered_at=now,
                updated_at=now,
            )
            db.add(invalid_status)
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

            owner_b = "owner_v1_" + ("b" * 43)
            db.add(Account(owner_id=owner_b, created_at=now, updated_at=now))
            db.commit()
            cross_owner_match = SteamMatch(
                id="match-cross-owner",
                connection_id=connection.id,
                owner_id=owner_b,
                share_code_hash="c" * 64,
                share_code_ciphertext=b"ciphertext-and-tag",
                share_code_nonce=b"8" * 12,
                encryption_key_version="test-v1",
                status="discovered",
                discovered_at=now,
                updated_at=now,
            )
            db.add(cross_owner_match)
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

            db.add(
                SteamMatch(
                    id="match-demo-a",
                    connection_id=connection.id,
                    owner_id=owner_id,
                    share_code_hash="d" * 64,
                    share_code_ciphertext=b"ciphertext-and-tag",
                    share_code_nonce=b"9" * 12,
                    encryption_key_version="test-v1",
                    status="parsing",
                    demo_id="one-demo-id",
                    discovered_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            db.add(
                SteamMatch(
                    id="match-demo-b",
                    connection_id=connection.id,
                    owner_id=owner_id,
                    share_code_hash="e" * 64,
                    share_code_ciphertext=b"ciphertext-and-tag",
                    share_code_nonce=b"0" * 12,
                    encryption_key_version="test-v1",
                    status="parsing",
                    demo_id="one-demo-id",
                    discovered_at=now,
                    updated_at=now,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_legacy_demo_owners_are_not_backfilled_into_accounts(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE demos (id VARCHAR(36) PRIMARY KEY, "
                    "owner_id VARCHAR(64) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO demos (id, owner_id) VALUES "
                    "('dev', 'dev-user'), ('test', 'owner-a')"
                )
            )
            run_schema_migrations(connection)

        with self.engine.connect() as connection:
            owners = connection.execute(
                text("SELECT owner_id FROM demos ORDER BY id")
            ).scalars().all()
            account_count = connection.execute(
                text("SELECT COUNT(*) FROM accounts")
            ).scalar_one()
        self.assertEqual(owners, ["dev-user", "owner-a"])
        self.assertEqual(account_count, 0)

    def test_untracked_account_schema_and_unknown_future_version_fail_closed(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE accounts (owner_id VARCHAR(64) PRIMARY KEY)")
            )
            with self.assertRaisesRegex(RuntimeError, "without its tracked"):
                run_schema_migrations(connection)

        self.engine.dispose()
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
            connection.execute(
                text(
                    f"INSERT INTO {MIGRATION_TABLE_NAME} "
                    "(version, name, checksum) VALUES "
                    "('9999999999', 'future', :checksum)"
                ),
                {"checksum": "f" * 64},
            )
        with self.engine.begin() as connection:
            with self.assertRaisesRegex(RuntimeError, "unknown to this application"):
                run_schema_migrations(connection)

    def test_checksum_drift_fails_closed(self) -> None:
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
            connection.execute(
                text(
                    f"UPDATE {MIGRATION_TABLE_NAME} SET checksum = :checksum "
                    "WHERE version = :version"
                ),
                {"checksum": "0" * 64, "version": MIGRATIONS[0].version},
            )
        with self.engine.begin() as connection:
            with self.assertRaisesRegex(RuntimeError, "checksum does not match"):
                run_schema_migrations(connection)

    def test_identity_constraints_are_enforced_by_migrated_schema(self) -> None:
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
        Session = sessionmaker(bind=self.engine)
        now = datetime.now(UTC)
        owner_id = "owner_v1_" + ("a" * 43)
        with Session() as db:
            db.add(Account(owner_id=owner_id, created_at=now, updated_at=now))
            db.add(
                ExternalIdentity(
                    id="identity-a",
                    provider="steam",
                    subject="76561197960278073",
                    owner_id=owner_id,
                    created_at=now,
                    last_login_at=now,
                )
            )
            db.commit()
            db.add(
                ExternalIdentity(
                    id="identity-b",
                    provider="steam",
                    subject="76561197960289184",
                    owner_id=owner_id,
                    created_at=now,
                    last_login_at=now,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()



def _normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _upload_session_schema(engine) -> dict[str, object]:
    inspector = inspect(engine)
    return {
        "columns": {
            column["name"]: (type(column["type"]).__name__, column["nullable"])
            for column in inspector.get_columns("upload_sessions")
        },
        "primary_key": inspector.get_pk_constraint("upload_sessions")["constrained_columns"],
        "indexes": {
            index["name"]: (tuple(index["column_names"]), bool(index.get("unique")))
            for index in inspector.get_indexes("upload_sessions")
        },
        "uniques": {
            unique["name"]: tuple(unique["column_names"])
            for unique in inspector.get_unique_constraints("upload_sessions")
        },
        "checks": {
            check["name"]: _normalized_sql(check["sqltext"])
            for check in inspector.get_check_constraints("upload_sessions")
        },
        "foreign_keys": inspector.get_foreign_keys("upload_sessions"),
    }


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class UploadSessionSchemaTest(unittest.TestCase):
    """The versioned migration and the ORM model must describe the same table.

    Tests that skip migrations build the table with create_all (the model);
    deployments build it with the migration. Drift between the two is invisible
    to every other test.
    """

    def setUp(self) -> None:
        self.migrated = create_engine("sqlite://")
        self.modeled = create_engine("sqlite://")
        for engine in (self.migrated, self.modeled):
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        with self.migrated.begin() as connection:
            run_schema_migrations(connection)
        UploadSession.__table__.create(self.modeled)

    def tearDown(self) -> None:
        self.migrated.dispose()
        self.modeled.dispose()

    def test_migration_matches_the_model(self) -> None:
        migrated = _upload_session_schema(self.migrated)
        self.assertEqual(migrated, _upload_session_schema(self.modeled))
        self.assertEqual(
            set(migrated["indexes"]),
            {"ix_upload_sessions_owner_id", "ix_upload_sessions_demo_id", "ix_upload_sessions_expires_at"},
        )
        self.assertEqual(
            migrated["uniques"],
            {"uq_upload_sessions_active_owner_id": ("active_owner_id",)},
        )
        self.assertEqual(
            set(migrated["checks"]),
            {
                "ck_upload_sessions_state",
                "ck_upload_sessions_active_owner",
                "ck_upload_sessions_completing",
                "ck_upload_sessions_completed",
                "ck_upload_sessions_failed",
                "ck_upload_sessions_sizes",
                "ck_upload_sessions_token",
            },
        )
        self.assertEqual(migrated["foreign_keys"], [])

    def test_untracked_upload_session_table_fails_closed(self) -> None:
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            for migration in MIGRATIONS[:-1]:
                migration.upgrade(connection)
            connection.execute(text("CREATE TABLE upload_sessions (id VARCHAR(32) PRIMARY KEY)"))
            with self.assertRaisesRegex(RuntimeError, "without its tracked"):
                MIGRATIONS[-1].upgrade(connection)

    def test_state_machine_constraints_hold_on_both_schemas(self) -> None:
        for label, engine in (("migrated", self.migrated), ("modeled", self.modeled)):
            with self.subTest(schema=label):
                self._assert_constraints(engine)

    def _assert_constraints(self, engine) -> None:
        Session = sessionmaker(bind=engine)
        now = datetime.now(UTC)
        counter = iter(range(1000))

        def row(**overrides: object) -> UploadSession:
            values: dict[str, object] = {
                "id": f"{next(counter):032x}",
                "owner_id": "owner-a",
                "active_owner_id": "owner-a",
                "state": "open",
                "display_filename": "match.dem",
                "content_type": "application/octet-stream",
                "file_size": 20 * 1024 * 1024,
                "part_size": 8 * 1024 * 1024,
                "part_count": 3,
                "token_sha256": "a" * 64,
                "created_at": now,
                "updated_at": now,
                "expires_at": now + timedelta(hours=24),
            }
            values.update(overrides)
            return UploadSession(**values)

        rejected = (
            # Active states must hold the owner's slot, and only their own.
            {"active_owner_id": None},
            {"active_owner_id": "owner-b"},
            {"state": "completing", "active_owner_id": None, "pending_demo_id": "d", "lease_until": now},
            # Terminal states must have released it.
            {"state": "completed", "demo_id": "demo-1"},
            {"state": "failed", "error_code": "INTAKE_CONTENT_MISMATCH"},
            # Per-state required columns.
            {"state": "completing", "pending_demo_id": None, "lease_until": now},
            {"state": "completing", "pending_demo_id": "demo-1", "lease_until": None},
            {"state": "completed", "active_owner_id": None, "demo_id": None},
            {"state": "failed", "active_owner_id": None, "error_code": None},
            {"state": "uploading"},
            {"file_size": 0},
            {"part_size": 0},
            {"part_count": 0},
            {"token_sha256": "a" * 63},
        )
        for overrides in rejected:
            with self.subTest(overrides=overrides), Session() as db:
                db.add(row(**overrides))
                with self.assertRaises(IntegrityError):
                    db.commit()

        with Session() as db:
            db.add(row())
            db.add(row(owner_id="owner-b", active_owner_id=None, state="completed", demo_id="demo-1"))
            db.add(row(owner_id="owner-b", active_owner_id=None, state="completed", demo_id="demo-2"))
            db.add(row(owner_id="owner-c", active_owner_id=None, state="failed", error_code="INTAKE_EMPTY"))
            db.add(
                row(
                    owner_id="owner-d",
                    active_owner_id="owner-d",
                    state="completing",
                    pending_demo_id="demo-3",
                    lease_until=now,
                )
            )
            db.commit()

        # One open/completing session per owner.
        with Session() as db:
            db.add(row())
            with self.assertRaises(IntegrityError):
                db.commit()


if __name__ == "__main__":
    unittest.main()
