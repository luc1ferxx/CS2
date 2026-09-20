"""The owner-scoped Demo Library: list/search/sort, read, rename, soft archive, status and coaching rows."""

from sqlalchemy import ColumnElement, asc, case, desc, func, or_

from app.models.demo import Demo
from app.schemas.demo import DemoListItem, DemoStatus
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import _optional_str
from app.services.demo_service.constants import DEMO_STATUS_ORDER, DEMO_STATUS_SEARCH_ALIASES


class DemoLibrary(ServiceComponent):
    """Reached as ``DemoService.library``."""

    def list_demos(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
        map_name: str | None = None,
        sort: str = "recent",
        order: str | None = None,
        include_archived: bool = False,
    ) -> list[DemoListItem]:
        query = self.db.query(Demo).filter(Demo.owner_id == self._service.require_owner_id())

        if not include_archived:
            query = query.filter(Demo.archived.is_(False))

        normalized_search = (search or "").strip().lower()
        if normalized_search:
            for search_token in normalized_search.split():
                like_search = f"%{search_token}%"
                search_terms: list[ColumnElement[bool]] = [
                    func.lower(Demo.id).like(like_search),
                    func.lower(Demo.name).like(like_search),
                    func.lower(Demo.original_filename).like(like_search),
                    func.lower(Demo.map_name).like(like_search),
                    func.lower(Demo.status).like(like_search),
                    func.lower(Demo.error_message).like(like_search),
                ]
                status_alias = DEMO_STATUS_SEARCH_ALIASES.get(search_token)
                if status_alias:
                    search_terms.append(Demo.status == status_alias)
                query = query.filter(or_(*search_terms))

        if status and status != "all":
            query = query.filter(Demo.status == status)
        if map_name and map_name != "all":
            query = query.filter(Demo.map_name == map_name)

        status_sort = case(
            *[(Demo.status == item, index) for index, item in enumerate(DEMO_STATUS_ORDER)],
            else_=len(DEMO_STATUS_ORDER),
        )
        sort_column = {
            "recent": Demo.created_at,
            "created": Demo.created_at,
            "updated": Demo.updated_at,
            "name": func.lower(Demo.name),
            "map": func.lower(Demo.map_name),
            "status": status_sort,
        }.get(sort, Demo.created_at)
        normalized_order = order or ("desc" if sort in {"recent", "created", "updated"} else "asc")
        direction = desc if normalized_order == "desc" else asc
        demos = (
            query.order_by(
                direction(sort_column),
                asc(func.lower(Demo.name)),
                asc(func.lower(Demo.original_filename)),
                asc(Demo.id),
            )
            .all()
        )
        return [self.demo_list_item(demo) for demo in demos]

    def demo_list_item(self, demo: Demo) -> DemoListItem:
        video = self._service.replay.public_video_status_for_list(demo)
        latest_render_job = self._service.render.latest_render_clip_job(demo)
        return DemoListItem.model_validate(demo).model_copy(
            update={
                "video_status": _optional_str(video.get("status")),
                "video_source": _optional_str(video.get("source")),
                "latest_render_status": latest_render_job.status if latest_render_job else None,
                "ingestion": self._service.parse.demo_ingestion_status(demo),
            }
        )

    def demo_status(self, demo: Demo) -> DemoStatus:
        return DemoStatus.model_validate(demo).model_copy(
            update={"ingestion": self._service.parse.demo_ingestion_status(demo)}
        )

    def get_demo(self, demo_id: str) -> Demo | None:
        return (
            self.db.query(Demo)
            .filter(Demo.id == demo_id, Demo.owner_id == self._service.require_owner_id())
            .one_or_none()
        )

    def update_demo(
        self,
        demo_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> Demo | None:
        demo = self.get_demo(demo_id)
        if demo is None:
            return None

        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("name cannot be blank")
            if len(normalized_name) > 255:
                raise ValueError("name must be 255 characters or fewer")
            demo.name = normalized_name
        if archived is not None:
            demo.archived = archived

        self.db.commit()
        self.db.refresh(demo)
        return demo

    def archive_demo(self, demo_id: str) -> Demo | None:
        return self.update_demo(demo_id, archived=True)

