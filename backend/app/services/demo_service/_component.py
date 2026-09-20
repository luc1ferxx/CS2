"""Base class for the DemoService components."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.services.demo_service import DemoService
    from app.services.storage import ArtifactStore, LocalStorageService


class ServiceComponent:
    """One responsibility of `DemoService`.

    A component keeps no state of its own. The facade owns the session, the
    owner context and the stores; the component reaches them through the
    back-reference, so components can be built in any order and exercised
    against a stub facade.
    """

    def __init__(self, service: DemoService) -> None:
        self._service = service

    @property
    def db(self) -> Session:
        return self._service.db

    @property
    def owner_id(self) -> str | None:
        return self._service.owner_id

    @property
    def storage(self) -> LocalStorageService:
        return self._service.storage

    @property
    def artifact_store(self) -> ArtifactStore:
        return self._service.artifact_store
