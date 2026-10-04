"""The commit that turns a prepared (already stored) .dem into a queued demo.

Shared by the legacy `POST /uploads/demo` and the chunked upload's `complete`,
so both count against the same quotas, fence and ledger in the same way:
inside `parse_admission` the authoritative quota check, the account fence,
the ledger row, the caller's extra writes and the commit of the demo and job
run in one transaction. Nothing here waits on storage or checks out another
connection while the admission is held: a failed attempt's prepared artifact
is discarded only after the admission has ended, and the caller reloads and
dispatches afterwards.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.deletion_service import AccountDeletedError, account_exists_for_write
from app.services.demo_service import DemoService, PreparedRealDemo
from app.services.upload_quota import UploadQuotaService, parse_admission, record_upload

__all__ = [
    "AccountDeletedError",
    "UploadSessionGone",
    "admit_and_commit",
]


class UploadSessionGone(Exception):
    """The upload session an admission was completing no longer exists in that state."""

    code = "upload_session_gone"
    message = "The upload session no longer exists."


def admit_and_commit(
    db: Session,
    service: DemoService,
    quota: UploadQuotaService,
    owner_id: str,
    prepared: PreparedRealDemo,
    *,
    extra_writes: Callable[[Session], None] | None = None,
) -> None:
    """Commit `prepared` under the parse admission, or discard it.

    Raises `UploadQuotaExceeded`, `AccountDeletedError`, `UploadSessionGone`
    (from `extra_writes`) or `DemoArtifactBindError` (a failed commit). On any
    failure the transaction is rolled back and the prepared artifact deleted
    unless a demo row turned out to be bound to it. The ledger row is written
    only in the committing transaction, so a refused attempt counts nothing.
    """
    committed = False
    try:
        with parse_admission(db):
            quota.check_new_upload(owner_id)
            # The account may have been deleted while the body streamed:
            # checked in the commit's own transaction (production only).
            if not account_exists_for_write(db, owner_id):
                raise AccountDeletedError
            record_upload(db, owner_id)
            if extra_writes is not None:
                extra_writes(db)
            # Only the commit: the reload and a failed commit's cleanup
            # need another pooled connection, so they run after the lock.
            service.commit_prepared_real_demo_rows()
        committed = True
    finally:
        if not committed:
            service.discard_prepared_real_demo(prepared)
