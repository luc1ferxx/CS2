"""DELETE /demos/{demo_id}: permanent, owner-scoped deletion of one match.

Archive (`POST /demos/{id}/archive`) stays the everyday action; this is the
explicit, irreversible one. The protocol lives in app/services/deletion_service.py.
Account deletion is `DELETE /auth/account` in app/api/auth.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.services.deletion_service import DeletionService

router = APIRouter(tags=["demos"])


@router.delete("/demos/{demo_id}", status_code=204)
def delete_demo(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> Response:
    # Someone else's demo, an unknown id and one already deleted all read the
    # same: 404. The frontend treats a 404 here as "already gone".
    if not DeletionService(db).delete_demo(owner_id, demo_id):
        raise HTTPException(status_code=404, detail="Demo not found")
    return Response(status_code=204)
