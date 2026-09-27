"""Delete matches or accounts on a user's behalf, through the site's own deletion protocol.

For the operator, when the owner cannot do it themselves: a removal request
from another player in an uploaded .dem, a user who was taken off the invite
list (and so can no longer sign in), or the deletions to redo after restoring
an older database backup (docs/vps_deploy_v1.md, step 7). It runs exactly what
DELETE /demos/{id} and DELETE /auth/account run -- rows in one transaction,
the storage purge through the durable outbox, sessions revoked -- so never
delete these rows by hand in psql instead.

    python -m app.cli.delete_data demo <demo_id>... [--yes]
    python -m app.cli.delete_data account <owner_id>... [--yes]
    python -m app.cli.delete_data account --steam-id <SteamID64>... [--yes]

Without --yes it only shows what would be deleted. Account deletion exists
only for real accounts (AUTH_MODE=production), as on the website.
Exit status: 0 when everything named was deleted (or, without --yes, found),
1 when something was not found or not confirmed, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.account import Account, ExternalIdentity
from app.models.demo import Demo
from app.services.deletion_service import DeletionService
from app.services.storage import ArtifactStore

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.delete_data",
        description="Delete matches or accounts on a user's behalf (irreversible).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="Delete matches by demo id.")
    demo.add_argument("ids", nargs="+", metavar="DEMO_ID")
    demo.add_argument("--yes", action="store_true", help="Actually delete; without it, only show.")

    account = commands.add_parser("account", help="Delete accounts and everything they own.")
    account.add_argument("ids", nargs="+", metavar="OWNER_ID")
    account.add_argument(
        "--steam-id",
        action="store_true",
        help="The ids are SteamID64s; find each one's account through its Steam sign-in.",
    )
    account.add_argument("--yes", action="store_true", help="Actually delete; without it, only show.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    revoke_sessions: Callable[[str], object] | None = None,
    artifact_store: ArtifactStore | None = None,
    runtime_settings: Settings = settings,
    out: TextIO = sys.stdout,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "account" and runtime_settings.auth_mode != "production":
        print(
            "Account deletion exists only for real accounts (AUTH_MODE=production); "
            "delete matches one by one with the demo command instead.",
            file=out,
        )
        return EXIT_USAGE
    factory = session_factory or _default_session_factory()
    with factory() as db:
        service = DeletionService(db, artifact_store=artifact_store, runtime_settings=runtime_settings)
        if args.command == "demo":
            return _delete_demos(db, service, args.ids, confirmed=args.yes, out=out)
        revoke = revoke_sessions or _default_revoke_sessions(runtime_settings)
        return _delete_accounts(
            db,
            service,
            args.ids,
            by_steam_id=args.steam_id,
            confirmed=args.yes,
            revoke_sessions=revoke,
            out=out,
        )


def _delete_demos(
    db: Session,
    service: DeletionService,
    demo_ids: Sequence[str],
    *,
    confirmed: bool,
    out: TextIO,
) -> int:
    status = EXIT_OK
    for demo_id in dict.fromkeys(demo_ids):
        row = db.execute(
            select(Demo.owner_id, Demo.status, Demo.created_at).where(Demo.id == demo_id)
        ).first()
        db.commit()
        if row is None:
            print(f"demo {demo_id}: not found", file=out)
            status = EXIT_INCOMPLETE
            continue
        if not confirmed:
            print(
                f"demo {demo_id}: would delete (owner {row.owner_id}, status {row.status}, "
                f"created {_iso(row.created_at)})",
                file=out,
            )
            continue
        if service.delete_demo(row.owner_id, demo_id):
            print(f"demo {demo_id}: deleted", file=out)
        else:
            print(f"demo {demo_id}: already gone", file=out)
    return _finish(status, confirmed, out)


def _delete_accounts(
    db: Session,
    service: DeletionService,
    ids: Sequence[str],
    *,
    by_steam_id: bool,
    confirmed: bool,
    revoke_sessions: Callable[[str], object],
    out: TextIO,
) -> int:
    status = EXIT_OK
    for given in dict.fromkeys(ids):
        label = f"steam {given}" if by_steam_id else f"account {given}"
        owner_id = _resolve_owner(db, given, by_steam_id=by_steam_id)
        if owner_id is None:
            print(f"{label}: not found", file=out)
            status = EXIT_INCOMPLETE
            continue
        if not confirmed:
            demo_count = db.execute(
                select(func.count()).select_from(Demo).where(Demo.owner_id == owner_id)
            ).scalar_one()
            db.commit()
            print(f"{label}: would delete account {owner_id} and its {demo_count} match(es)", file=out)
            continue
        if service.delete_account(owner_id, revoke_sessions=revoke_sessions):
            print(f"{label}: deleted account {owner_id}; its sessions are revoked", file=out)
        else:
            print(f"{label}: already gone", file=out)
    return _finish(status, confirmed, out)


def _resolve_owner(db: Session, value: str, *, by_steam_id: bool) -> str | None:
    if by_steam_id:
        owner_id = db.execute(
            select(ExternalIdentity.owner_id).where(
                ExternalIdentity.provider == "steam",
                ExternalIdentity.subject == value,
            )
        ).scalar_one_or_none()
    else:
        owner_id = db.execute(
            select(Account.owner_id).where(Account.owner_id == value)
        ).scalar_one_or_none()
    db.commit()
    return owner_id


def _finish(status: int, confirmed: bool, out: TextIO) -> int:
    if not confirmed:
        print("Nothing was deleted. Rerun with --yes to delete (irreversible).", file=out)
        return EXIT_INCOMPLETE
    return status


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _default_session_factory() -> Callable[[], Session]:
    from app.core.database import SessionLocal

    return SessionLocal


def _default_revoke_sessions(runtime_settings: Settings) -> Callable[[str], object]:
    from app.core.redis import get_redis_client
    from app.services.auth_service import AuthService

    return AuthService(runtime_settings, get_redis_client()).revoke_owner_sessions


if __name__ == "__main__":
    raise SystemExit(main())
