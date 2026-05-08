from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.demo import DemoListItem
from app.services.demo_service import DemoService
from app.services.upload_service import DemoUploadValidationError

router = APIRouter(tags=["uploads"])


@router.post("/uploads/mock", response_model=DemoListItem, status_code=201)
def create_mock_upload(db: Session = Depends(get_db)) -> DemoListItem:
    return DemoService(db).create_mock_demo()


@router.post("/uploads/demo", response_model=DemoListItem, status_code=201)
async def create_demo_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DemoListItem:
    try:
        return await DemoService(db).create_real_demo(file)
    except DemoUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
