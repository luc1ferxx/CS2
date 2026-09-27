"""Tolerating demo and job rows that were deleted while an operation ran.

A match can be deleted in any state, so every worker transition and every
request that loaded a row before writing to it can find the row gone by the
time it flushes. SQLAlchemy reports that in several shapes -- StaleDataError on
a 0-row UPDATE, ObjectDeletedError or InvalidRequestError when refreshing a
vanished row, an FK IntegrityError when inserting a child row -- and our own
code adds ValueErrors and AttributeErrors when a re-read comes back empty. The
only reliable test is therefore to roll back and look the row up again, which
is what everything here does.
"""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models.demo import Demo
from app.models.job import DemoJob
from app.services.demo_service.errors import DemoGoneError


def row_identity(obj: Any) -> str | None:
    """The primary key of an ORM row, read without triggering a refresh.

    Accessing `obj.id` on an expired instance reloads it, which is exactly
    what raises when the row is gone.
    """
    state = sa_inspect(obj, raiseerr=False)
    identity = getattr(state, "identity", None)
    if identity:
        return str(identity[0])
    value = getattr(obj, "__dict__", {}).get("id")
    return str(value) if value is not None else None


def rows_missing(
    db: Session,
    *,
    demo_id: str | None = None,
    job_id: str | None = None,
) -> bool:
    """Roll back, then report whether the demo or the job (or its demo) is gone.

    False when the lookup itself fails: an unknown answer must not turn a
    real error into a silent no-op.
    """
    try:
        db.rollback()
        if demo_id is not None and (
            db.query(Demo.id).filter(Demo.id == demo_id).first() is None
        ):
            return True
        if job_id is not None and (
            db.query(DemoJob.id)
            .join(Demo, Demo.id == DemoJob.demo_id)
            .filter(DemoJob.id == job_id)
            .first()
            is None
        ):
            return True
    except Exception:
        return False
    return False


@contextmanager
def gone_rows_raise(
    db: Session,
    *,
    demo_id: str | None = None,
    job_id: str | None = None,
) -> Iterator[None]:
    """Turn any failure caused by the demo/job vanishing into DemoGoneError.

    Failures with the rows still present propagate unchanged (after a
    rollback, which every such failure path needs anyway).
    """
    try:
        yield
    except DemoGoneError:
        raise
    except Exception:
        if rows_missing(db, demo_id=demo_id, job_id=job_id):
            raise DemoGoneError("Demo was deleted") from None
        raise


def job_gone_raises[M: Callable[..., Any]](method: M) -> M:
    """Run a component method whose first argument is a DemoJob under gone_rows_raise."""

    @functools.wraps(method)
    def wrapper(self: Any, job: Any, *args: Any, **kwargs: Any) -> Any:
        with gone_rows_raise(self.db, job_id=row_identity(job)):
            return method(self, job, *args, **kwargs)

    return cast(M, wrapper)


def demo_gone_raises[M: Callable[..., Any]](method: M) -> M:
    """Run a component method whose first argument is a Demo under gone_rows_raise."""

    @functools.wraps(method)
    def wrapper(self: Any, demo: Any, *args: Any, **kwargs: Any) -> Any:
        with gone_rows_raise(self.db, demo_id=row_identity(demo)):
            return method(self, demo, *args, **kwargs)

    return cast(M, wrapper)
