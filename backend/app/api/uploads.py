from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.core.auth import get_current_owner_id
from app.core.config import settings
from app.core.database import get_db
from app.core.features import require_dev_tools
from app.core.request_limits import PART_BUSY_RETRY_AFTER_SECONDS
from app.core.upload_slots import IntakeSlot, get_intake_slot
from app.schemas.demo import DemoListItem
from app.schemas.upload_quota import UploadQuotaResponse
from app.schemas.upload_session import (
    UploadPartReceived,
    UploadSessionCompleting,
    UploadSessionCreate,
    UploadSessionCreated,
    UploadSessionCurrent,
    UploadSessionStatus,
    UploadSessionTokenResponse,
)
from app.services.artifact_intake import ArtifactIntakeError, has_incompatible_content_prefix
from app.services.auth_service import AuthService, get_auth_service
from app.services.demo_service import DemoArtifactBindError, DemoDispatchError, DemoService
from app.services.storage import (
    PartInfo,
    StagingDigestMismatch,
    StagingPartInvalid,
    StagingSessionGone,
    UploadStagingStore,
)
from app.services.upload_admission import AccountDeletedError, admit_and_commit
from app.services.upload_quota import UploadQuotaExceeded, UploadQuotaService
from app.services.upload_service import DemoUploadValidationError
from app.services.upload_session_service import (
    NO_STORE,
    PART_INDEX_PATTERN,
    PART_SHA256_PATTERN,
    SIGNATURE_PREFIX_BYTES,
    PartTarget,
    UploadIntakeRefusal,
    UploadSessionError,
    UploadSessionService,
    fail_session_for_content,
    get_upload_session_factory,
    get_upload_staging,
    intake_busy,
    is_session_id,
    part_invalid,
    parts_in_flight,
    resolve_part_target,
    session_not_found,
    storage_unavailable,
)

router = APIRouter(tags=["uploads"])

_SESSION_REFUSALS = (UploadSessionError, UploadIntakeRefusal, UploadQuotaExceeded)


@router.get("/uploads/quota", response_model=UploadQuotaResponse)
def get_upload_quota(
    response: Response,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
) -> UploadQuotaResponse:
    snapshot = UploadQuotaService(db).snapshot(owner_id)
    response.headers["Cache-Control"] = "private, no-store"
    return UploadQuotaResponse(
        dailyLimit=snapshot.daily_limit,
        dailyUsed=snapshot.daily_used,
        dailyResetSeconds=snapshot.daily_reset_seconds,
        activeLimit=snapshot.active_limit,
        activeCount=snapshot.active_count,
        maxUploadBytes=settings.max_demo_upload_bytes,
    )


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
        try:
            admit_and_commit(db, service, quota, owner_id, prepared)
        except UploadQuotaExceeded as exc:
            return exc.to_response()
        except AccountDeletedError as exc:
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": exc.code, "message": exc.message}},
                headers={"Cache-Control": "private, no-store"},
            )
        service.reload_prepared_real_demo(prepared)
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


# -- Chunked, resumable upload sessions ---------------------------------------
# Every response is `Cache-Control: private, no-store`. The cookie routes are
# owner-scoped (another owner's session is a 404) and, in production, behind
# SessionCsrfMiddleware's session and Origin checks. The part PUT is the one
# route authenticated by the session's upload token instead.


def _json(status_code: int, payload: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload), headers=dict(NO_STORE))


def _session_service(
    db: Session,
    owner_id: str,
    staging: UploadStagingStore,
) -> UploadSessionService:
    return UploadSessionService(db, owner_id, staging=staging)


@router.post(
    "/uploads/sessions",
    response_model=UploadSessionCreated,
    status_code=201,
)
def create_upload_session(
    body: UploadSessionCreate,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> JSONResponse:
    service = _session_service(db, owner_id, staging)
    try:
        snapshot, token = service.create(
            filename=body.filename,
            size=body.size,
            content_type=body.contentType,
            replace=body.replace,
        )
    except ArtifactIntakeError as exc:
        return UploadIntakeRefusal.from_intake_error(exc).to_response()
    except _SESSION_REFUSALS as exc:
        return exc.to_response()
    return _json(
        201,
        UploadSessionCreated(
            sessionId=snapshot.id,
            uploadToken=token,
            partSize=snapshot.part_size,
            partCount=snapshot.part_count,
            maxParallelParts=service.settings.upload_max_parallel_parts,
            expiresAt=snapshot.expires_at,
            receivedParts=[],
        ),
    )


# Declared before /uploads/sessions/{session_id} so "current" is not taken for an id.
@router.get("/uploads/sessions/current", response_model=UploadSessionCurrent)
def get_current_upload_session(
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> JSONResponse:
    # 200 with a null session rather than 204: the frontend's JSON helper
    # always parses the body.
    current = _session_service(db, owner_id, staging).current()
    return _json(200, UploadSessionCurrent(session=current))


@router.get("/uploads/sessions/{session_id}", response_model=UploadSessionStatus)
def get_upload_session(
    session_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> JSONResponse:
    try:
        status = _session_service(db, owner_id, staging).status(session_id)
    except _SESSION_REFUSALS as exc:
        return exc.to_response()
    return _json(200, status)


@router.post("/uploads/sessions/{session_id}/token", response_model=UploadSessionTokenResponse)
def reissue_upload_token(
    session_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> JSONResponse:
    service = _session_service(db, owner_id, staging)
    try:
        snapshot, token = service.reissue_token(session_id)
        status = service.status_of(snapshot)
    except _SESSION_REFUSALS as exc:
        return exc.to_response()
    return _json(200, UploadSessionTokenResponse(**status.model_dump(), uploadToken=token))


@router.post(
    "/uploads/sessions/{session_id}/complete",
    response_model=DemoListItem,
    status_code=201,
    responses={
        200: {"model": DemoListItem, "description": "Already completed; the demo it created."},
        202: {"model": UploadSessionCompleting, "description": "Another attempt is completing it; poll."},
    },
)
def complete_upload_session(
    session_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
    intake_slot: IntakeSlot = Depends(get_intake_slot),
) -> JSONResponse:
    try:
        result = _session_service(db, owner_id, staging).complete(session_id, intake_slot=intake_slot)
    except _SESSION_REFUSALS as exc:
        return exc.to_response()
    if result.demo is None:
        return _json(202, UploadSessionCompleting())
    return _json(result.status_code, result.demo)


@router.delete("/uploads/sessions/{session_id}", status_code=204)
def delete_upload_session(
    session_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner_id),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> Response:
    try:
        _session_service(db, owner_id, staging).delete(session_id)
    except _SESSION_REFUSALS as exc:
        return exc.to_response()
    return Response(status_code=204, headers=dict(NO_STORE))


@router.put(
    "/uploads/sessions/{session_id}/parts/{index}",
    response_model=UploadPartReceived,
    responses={404: {"description": "No such session, or the upload token does not match."}},
)
async def put_upload_part(
    session_id: str,
    index: str,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    session_factory: Callable[[], Session] = Depends(get_upload_session_factory),
    staging: UploadStagingStore = Depends(get_upload_staging),
) -> JSONResponse:
    """Store one part. Authenticated only by `X-Upload-Token` (never the cookie).

    No pooled database connection is held while the body is read: the token
    and state are checked in a short session that is closed first, and the
    part itself is never recorded in the database (the staging directory is
    the record).
    """
    runtime = auth.settings
    if runtime.auth_mode == "production" and request.headers.get("origin") not in runtime.runtime_cors_origins:
        return JSONResponse(
            status_code=403,
            content={"detail": "Untrusted request origin"},
            headers=dict(NO_STORE),
        )
    if not is_session_id(session_id) or PART_INDEX_PATTERN.fullmatch(index) is None:
        return session_not_found().to_response()
    tokens = request.headers.getlist("x-upload-token")
    if len(tokens) != 1 or not tokens[0]:
        return session_not_found().to_response()
    token = tokens[0]
    part_index = int(index)

    # Enter before reading the state, so `complete` (which commits
    # `completing` and then waits for this counter) never misses this part.
    tracker = parts_in_flight()
    if not tracker.try_enter(session_id, runtime.upload_max_parallel_parts):
        return intake_busy(PART_BUSY_RETRY_AFTER_SECONDS).to_response()
    try:
        try:
            target = await run_in_threadpool(_authorize_part, session_factory, session_id, token, part_index)
        except _SESSION_REFUSALS as exc:
            return exc.to_response()

        problem = _part_headers_problem(request, target)
        if problem is not None:
            return problem.to_response()
        expected_sha256 = _optional_sha256(request)

        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > target.expected_size:
                    return part_invalid("The part is longer than its declared length.").to_response()
        except ClientDisconnect:
            return part_invalid("The part was not received completely.").to_response()
        if len(body) != target.expected_size:
            return part_invalid("The part is shorter than its declared length.").to_response()
        data = bytes(body)
        del body

        if target.index == 0 and has_incompatible_content_prefix(data[:SIGNATURE_PREFIX_BYTES]):
            # An archive or executable: fail now, not after the whole file.
            await run_in_threadpool(
                _fail_for_content, session_factory, staging, target, "INTAKE_CONTENT_MISMATCH"
            )
            return UploadIntakeRefusal.from_intake_error(
                ArtifactIntakeError("INTAKE_CONTENT_MISMATCH")
            ).to_response()

        try:
            info, received_count = await run_in_threadpool(_write_part, staging, target, data, expected_sha256)
        except StagingSessionGone:
            # Deleted or expired meanwhile; the directory is never recreated.
            return session_not_found().to_response()
        except StagingDigestMismatch:
            return UploadSessionError(
                422,
                "upload_part_digest_mismatch",
                "The part does not match its X-Part-SHA256 digest.",
            ).to_response()
        except StagingPartInvalid:
            return part_invalid().to_response()
        except Exception:
            return storage_unavailable().to_response()
        return _json(
            200,
            UploadPartReceived(
                index=info.index,
                sizeBytes=info.size_bytes,
                sha256=info.sha256,
                receivedCount=received_count,
            ),
        )
    finally:
        tracker.leave(session_id)


def _authorize_part(
    session_factory: Callable[[], Session],
    session_id: str,
    token: str,
    index: int,
) -> PartTarget:
    db = session_factory()
    try:
        return resolve_part_target(db, session_id, token, index)
    finally:
        db.close()


def _fail_for_content(
    session_factory: Callable[[], Session],
    staging: UploadStagingStore,
    target: PartTarget,
    error_code: str,
) -> None:
    db = session_factory()
    try:
        fail_session_for_content(db, staging, target, error_code)
    finally:
        db.close()


def _write_part(
    staging: UploadStagingStore,
    target: PartTarget,
    data: bytes,
    expected_sha256: str | None,
) -> tuple[PartInfo, int]:
    info = staging.write_part(
        target.owner_id,
        target.session_id,
        target.index,
        data,
        expected_size=target.expected_size,
        expected_sha256=expected_sha256,
    )
    received_count = len(staging.list_parts(target.owner_id, target.session_id))
    return info, received_count


def _part_headers_problem(request: Request, target: PartTarget) -> UploadSessionError | None:
    lengths = request.headers.getlist("content-length")
    if len(lengths) != 1 or not lengths[0].strip().isdigit():
        return part_invalid("Content-Length is required and must equal the part length.")
    if int(lengths[0].strip()) != target.expected_size:
        return part_invalid("Content-Length must equal the part length.")
    digests = request.headers.getlist("x-part-sha256")
    if len(digests) > 1 or (digests and PART_SHA256_PATTERN.fullmatch(digests[0].strip().lower()) is None):
        return part_invalid("X-Part-SHA256 must be one lowercase hex sha256.")
    return None


def _optional_sha256(request: Request) -> str | None:
    value = request.headers.get("x-part-sha256")
    return value.strip().lower() if value else None
