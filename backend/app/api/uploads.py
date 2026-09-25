from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.core.features import require_dev_tools
from app.schemas.demo import DemoListItem
from app.services.artifact_intake import ArtifactIntakeError
from app.services.demo_service import DemoArtifactBindError, DemoDispatchError, DemoService
from app.services.upload_quota import UploadQuotaExceeded, UploadQuotaService, parse_admission
from app.services.upload_service import DemoUploadValidationError

router = APIRouter(tags=["uploads"])


@router.post(
    "/uploads/mock",
    response_model=DemoListItem,
    status_code=201,
    dependencies=[Depends(require_dev_tools)],
)
def create_mock_upload(
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem:
    return DemoService(db, owner_id=owner_id).create_mock_demo()


@router.post("/uploads/demo", response_model=DemoListItem, status_code=201)
def create_demo_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> DemoListItem | JSONResponse:
    quota = UploadQuotaService(db)
    try:
        quota.check_new_upload(owner_id)
    except UploadQuotaExceeded as exc:
        return exc.to_response()
    service = DemoService(db, owner_id=owner_id)
    try:
        # Stream to storage first; only the final check and the commit that
        # makes the demo active run inside the admission.
        prepared = service.prepare_real_demo(
            stream=file.file,
            filename=file.filename or "demo.dem",
            content_type=file.content_type,
        )
        committed = False
        try:
            with parse_admission(db):
                quota.check_new_upload(owner_id)
                service.commit_prepared_real_demo(prepared)
            committed = True
        except UploadQuotaExceeded as exc:
            return exc.to_response()
        finally:
            if not committed:
                service.discard_prepared_real_demo(prepared)
        service.dispatch_prepared_real_demo(prepared)
        return service.demo_list_item(prepared.demo)
    except ArtifactIntakeError as exc:
        status_code = {
            "INTAKE_TOO_LARGE": 413,
            "INTAKE_STORAGE_UNAVAILABLE": 503,
        }.get(exc.code, 400)
        return JSONResponse(
            status_code=status_code,
            content={"detail": exc.safe_message, "errorCode": exc.code},
        )
    except (DemoArtifactBindError, DemoDispatchError) as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "errorCode": "INTAKE_UNAVAILABLE"},
        )
    except DemoUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
