import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.migrations.runner import MIGRATION_TABLE_NAME, MIGRATIONS, run_schema_migrations
from app.models import Account, ExternalIdentity


class DatabaseMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_fresh_and_repeated_upgrade_apply_one_tracked_migration(self) -> None:
        with self.engine.begin() as connection:
            run_schema_migrations(connection)
        with self.engine.begin() as connection:
            run_schema_migrations(connection)

        table_names = set(inspect(self.engine).get_table_names())
        self.assertTrue(
            {"accounts", "external_identities", MIGRATION_TABLE_NAME}.issubset(
                table_names
            )
        )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT version, name, checksum FROM {MIGRATION_TABLE_NAME}"
                )
            ).mappings().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version"], MIGRATIONS[0].version)
        self.assertEqual(rows[0]["checksum"], MIGRATIONS[0].checksum)

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
        now = datetime.now(timezone.utc)
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


if __name__ == "__main__":
    unittest.main()
