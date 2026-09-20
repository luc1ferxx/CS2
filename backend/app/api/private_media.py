from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import get_db
from app.services.demo_service import DemoService, PrivateVideoHandle

router = APIRouter(tags=["media"])

PRIVATE_MEDIA_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Vary": "Cookie, Origin",
    "X-Content-Type-Options": "nosniff",
}


@router.api_route(
    "/demos/{demo_id}/media/video",
    methods=["GET", "HEAD"],
)
def get_private_demo_video(
    demo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> Response:
    return _private_video_response(demo_id, request, db, owner_id)


@router.api_route(
    "/demos/{demo_id}/render/jobs/{job_id}/media/video",
    methods=["GET", "HEAD"],
)
def get_private_render_video(
    demo_id: str,
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> Response:
    return _private_video_response(demo_id, request, db, owner_id, job_id=job_id)


def _private_video_response(
    demo_id: str,
    request: Request,
    db: Session,
    owner_id: str,
    *,
    job_id: str | None = None,
) -> Response:
    if (
        settings.auth_mode == "production"
        and request.headers.get("sec-fetch-site") in {"same-site", "cross-site"}
    ):
        raise _media_not_found()

    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise _media_not_found()

    try:
        opened = (
            service.open_private_render_video(demo, job_id)
            if job_id is not None else service.open_private_video(demo)
        )
    except (OSError, ValueError):
        raise _media_not_found() from None

    if opened is None:
        raise _media_not_found()

    handle, stat_result = opened
    size = stat_result.st_size
    byte_range = _parse_single_range(request.headers.get("range"), size)
    if request.headers.get("range") is not None and byte_range is None:
        handle.close()
        return Response(
            status_code=416,
            headers={
                **PRIVATE_MEDIA_HEADERS,
                "Accept-Ranges": "bytes",
                "Content-Range": f"*/{size}",
            },
        )

    start, end = byte_range if byte_range is not None else (0, max(0, size - 1))
    content_length = 0 if size == 0 else end - start + 1
    status_code = 206 if byte_range is not None else 200
    headers = {
        **PRIVATE_MEDIA_HEADERS,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }
    if byte_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    if request.method == "HEAD" or size == 0:
        handle.close()
        return Response(
            status_code=status_code,
            media_type="video/mp4",
            headers=headers,
        )

    return StreamingResponse(
        _stream_file_range(handle, start, content_length),
        status_code=status_code,
        media_type="video/mp4",
        headers=headers,
    )


def _parse_single_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        return None

    spec = value[6:].strip()
    if "-" not in spec:
        return None
    start_text, end_text = (part.strip() for part in spec.split("-", 1))
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            return max(0, size - suffix_length), size - 1

        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= size:
        return None
    return start, min(end, size - 1)


def _stream_file_range(
    handle: PrivateVideoHandle,
    start: int,
    length: int,
) -> Iterator[bytes]:
    remaining = length
    try:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def _media_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Media not found")
