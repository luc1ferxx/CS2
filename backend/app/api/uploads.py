from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.demo import DemoListItem
from app.services.demo_service import DemoService

router = APIRouter(tags=["uploads"])


@router.post("/uploads/mock", response_model=DemoListItem, status_code=201)
def create_mock_upload(db: Session = Depends(get_db)) -> DemoListItem:
    return DemoService(db).create_mock_demo()
