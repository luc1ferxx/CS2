import gzip
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_owner_id
from app.core.database import get_db
from app.core.json_compression import accepts_gzip
from app.services.demo_service import DemoService
from app.services.demo_service.replay_response_cache import (
    CACHE_CONTROL,
    CachedReplay,
    ReplayArtifactMissingError,
    cached_replay_response,
)

router = APIRouter(tags=["replay"])


@router.get(
    "/demos/{demo_id}/replay",
    response_class=Response,
    responses={
        200: {"description": "The replay contract.", "content": {"application/json": {}}},
        304: {"description": "The client's copy is current (If-None-Match matched the ETag)."},
    },
)
def get_replay(
    demo_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    if_none_match: Annotated[str | None, Header()] = None,
    accept_encoding: Annotated[str | None, Header()] = None,
) -> Response:
    service = DemoService(db, owner_id=owner_id)
    demo = service.get_demo(demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "completed":
        raise HTTPException(status_code=409, detail="Replay is not ready")

    # Only after the owner and readiness checks: a foreign or deleted demo never reaches the cache.
    try:
        cached = cached_replay_response(service, demo, if_none_match=if_none_match)
    except ReplayArtifactMissingError:
        raise HTTPException(status_code=404, detail="Replay blob not found") from None
    if cached is not None:
        return _cached_response(cached, accept_encoding=accept_encoding or "")

    replay = service.public_replay(demo)
    if replay is None:
        raise HTTPException(status_code=404, detail="Replay blob not found")
    # Serialized once, compactly: a replay is tens of MB of plain JSON, and a
    # response-model pass over it roughly doubles the time to first byte.
    return Response(
        content=json.dumps(replay, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        media_type="application/json",
    )


def _cached_response(cached: CachedReplay, *, accept_encoding: str) -> Response:
    # A 304 carries the headers the 200 would have (RFC 9110 15.4.5).
    headers = {"ETag": cached.etag, "Cache-Control": CACHE_CONTROL, "Vary": "Accept-Encoding"}
    if cached.not_modified or cached.gzip_body is None:
        return Response(status_code=304, headers=headers)
    if accepts_gzip(accept_encoding):
        # Already encoded, so JsonGzipMiddleware passes it through untouched.
        return Response(
            content=cached.gzip_body,
            media_type="application/json",
            headers={**headers, "Content-Encoding": "gzip"},
        )
    return Response(content=gzip.decompress(cached.gzip_body), media_type="application/json", headers=headers)
