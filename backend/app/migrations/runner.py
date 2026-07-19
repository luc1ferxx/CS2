from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
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


def _create_steam_sync_v1(connection: Connection) -> None:
    existing_tables = set(inspect(connection).get_table_names())
    owned_tables = {"steam_connections", "steam_matches"}
    if existing_tables.intersection(owned_tables):
        raise RuntimeError(
            "Steam sync schema exists without its tracked schema migration"
        )
    metadata = MetaData()
    Table("accounts", metadata, autoload_with=connection)
    steam_connections = Table(
        "steam_connections",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "owner_id",
            String(64),
            ForeignKey("accounts.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("steam_id64", String(17), nullable=False),
        Column("game_auth_code_ciphertext", LargeBinary(), nullable=False),
        Column("game_auth_code_nonce", LargeBinary(12), nullable=False),
        Column("known_code_ciphertext", LargeBinary(), nullable=False),
        Column("known_code_nonce", LargeBinary(12), nullable=False),
        Column("encryption_key_version", String(64), nullable=False),
        Column("status", String(32), nullable=False),
        Column("sync_run_id", String(36), nullable=True),
        Column("consecutive_failures", Integer, nullable=False),
        Column("last_sync_started_at", DateTime(timezone=True), nullable=True),
        Column("last_sync_completed_at", DateTime(timezone=True), nullable=True),
        Column("next_retry_at", DateTime(timezone=True), nullable=True),
        Column("last_error_code", String(64), nullable=True),
        Column("last_error_message", String(255), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("owner_id", name="uq_steam_connections_owner_id"),
        UniqueConstraint("steam_id64", name="uq_steam_connections_steam_id64"),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_steam_connections_id_owner_id",
        ),
        CheckConstraint(
            "status IN ('connected', 'syncing', 'caught_up', 'retry_wait', "
            "'authorization_required', 'error')",
            name="ck_steam_connections_status",
        ),
        CheckConstraint(
            "length(game_auth_code_nonce) = 12",
            name="ck_steam_connections_game_auth_nonce_length",
        ),
        CheckConstraint(
            "length(game_auth_code_ciphertext) >= 16",
            name="ck_steam_connections_game_auth_ciphertext_length",
        ),
        CheckConstraint(
            "length(known_code_nonce) = 12",
            name="ck_steam_connections_known_code_nonce_length",
        ),
        CheckConstraint(
            "length(known_code_ciphertext) >= 16",
            name="ck_steam_connections_known_code_ciphertext_length",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_steam_connections_consecutive_failures",
        ),
    )
    steam_matches = Table(
        "steam_matches",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "connection_id",
            String(36),
            nullable=False,
        ),
        Column(
            "owner_id",
            String(64),
            ForeignKey("accounts.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("share_code_hash", String(64), nullable=False),
        Column("share_code_ciphertext", LargeBinary(), nullable=False),
        Column("share_code_nonce", LargeBinary(12), nullable=False),
        Column("encryption_key_version", String(64), nullable=False),
        Column("status", String(32), nullable=False),
        Column("discovered_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        ForeignKeyConstraint(
            ("connection_id", "owner_id"),
            ("steam_connections.id", "steam_connections.owner_id"),
            ondelete="CASCADE",
            name="fk_steam_matches_connection_owner",
        ),
        UniqueConstraint(
            "owner_id",
            "share_code_hash",
            name="uq_steam_matches_owner_share_code_hash",
        ),
        CheckConstraint(
            "status IN ('discovered', 'demo_pending', 'downloading', 'parsing', "
            "'ready', 'unavailable')",
            name="ck_steam_matches_status",
        ),
        CheckConstraint(
            "length(share_code_hash) = 64",
            name="ck_steam_matches_share_code_hash_length",
        ),
        CheckConstraint(
            "length(share_code_nonce) = 12",
            name="ck_steam_matches_share_code_nonce_length",
        ),
        CheckConstraint(
            "length(share_code_ciphertext) >= 16",
            name="ck_steam_matches_share_code_ciphertext_length",
        ),
    )
    Index("ix_steam_matches_connection_id", steam_matches.c.connection_id)
    Index(
        "ix_steam_matches_owner_discovered_at",
        steam_matches.c.owner_id,
        steam_matches.c.discovered_at,
    )
    steam_connections.create(connection)
    steam_matches.create(connection)


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
    SchemaMigration(
        version="2026071902",
        name="create_steam_connections_and_matches",
        checksum=_checksum(
            "steam-connections-v1:owner,steam-id64,validated-encrypted-credentials,key-version,"
            "sync-state,retry-state,timestamps;steam-matches-v1:connection,owner,"
            "connection-owner-foreign-key,encrypted-share-code,share-code-hash,status,timestamps"
        ),
        upgrade=_create_steam_sync_v1,
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
