"""Production gates for dev/QA routes and the matching /auth/me capabilities.

The gates read the process-wide settings, like GET /diagnostics does, and
answer 404 so a production client cannot tell a hidden route from a missing
one. Use them as route-decorator dependencies: those run before the owner
dependency, but SessionCsrfMiddleware still rejects anonymous production
writes with 401 before routing.
"""

from typing import Any

from fastapi import HTTPException

from app.core.config import Settings, settings


def require_dev_tools() -> None:
    if settings.auth_mode == "production":
        raise HTTPException(status_code=404, detail="Not found")


def require_render_clips() -> None:
    if not settings.render_clips_available:
        raise HTTPException(status_code=404, detail="Not found")


def feature_capabilities(runtime_settings: Settings) -> dict[str, bool]:
    return {
        "devTools": runtime_settings.auth_mode != "production",
        "renderClips": runtime_settings.render_clips_available,
    }


def api_docs_kwargs(runtime_settings: Settings) -> dict[str, Any]:
    # The generated schema lists every route, including the ones production hides.
    if runtime_settings.auth_mode != "production":
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
