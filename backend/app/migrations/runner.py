from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    inspect,
    insert,
    select,
)
from sqlalchemy.engine import Connection


MIGRATION_TABLE_NAME = "app_schema_migrations"


@dataclass(frozen=True)
class SchemaMigration:
    version: str
    name: str
    checksum: str
    upgrade: Callable[[Connection], None]


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_accounts_v1(connection: Connection) -> None:
    existing_tables = set(inspect(connection).get_table_names())
    owned_tables = {"accounts", "external_identities"}
    if existing_tables.intersection(owned_tables):
        raise RuntimeError(
            "Account schema exists without its tracked schema migration"
        )
    metadata = MetaData()
    accounts = Table(
        "accounts",
        metadata,
        Column("owner_id", String(64), primary_key=True),
        Column("display_name", String(128), nullable=True),
        Column("avatar_url", String(512), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    external_identities = Table(
        "external_identities",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("provider", String(96), nullable=False),
        Column("subject", String(255), nullable=False),
        Column(
            "owner_id",
            String(64),
            ForeignKey("accounts.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("last_login_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint(
            "provider",
            "subject",
            name="uq_external_identities_provider_subject",
        ),
        UniqueConstraint(
            "owner_id",
            "provider",
            name="uq_external_identities_owner_provider",
        ),
    )
    Index("ix_external_identities_owner_id", external_identities.c.owner_id)
    accounts.create(connection)
    external_identities.create(connection)


MIGRATIONS = (
    SchemaMigration(
        version="2026071901",
        name="create_accounts_and_external_identities",
        checksum=_checksum(
            "accounts-v1:owner-id,profile,timestamps;"
            "external-identities-v1:provider-subject,owner-provider,owner-fk,timestamps"
        ),
        upgrade=_create_accounts_v1,
    ),
)


def _migration_table(metadata: MetaData) -> Table:
    return Table(
        MIGRATION_TABLE_NAME,
        metadata,
        Column("version", String(32), primary_key=True),
        Column("name", String(128), nullable=False),
        Column("checksum", String(64), nullable=False),
        Column(
            "applied_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )


def run_schema_migrations(connection: Connection) -> None:
    metadata = MetaData()
    migration_table = _migration_table(metadata)
    migration_table.create(connection, checkfirst=True)

    applied = {
        row.version: row.checksum
        for row in connection.execute(
            select(migration_table.c.version, migration_table.c.checksum)
        )
    }
    known_versions = {migration.version for migration in MIGRATIONS}
    unknown_versions = sorted(set(applied) - known_versions)
    if unknown_versions:
        raise RuntimeError(
            "Database contains schema migrations unknown to this application: "
            + ", ".join(unknown_versions)
        )

    for migration in MIGRATIONS:
        previous_checksum = applied.get(migration.version)
        if previous_checksum is not None:
            if previous_checksum != migration.checksum:
                raise RuntimeError(
                    f"Schema migration {migration.version} checksum does not match"
                )
            continue
        migration.upgrade(connection)
        connection.execute(
            insert(migration_table).values(
                version=migration.version,
                name=migration.name,
                checksum=migration.checksum,
            )
        )
