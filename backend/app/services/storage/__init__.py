"""Private artifact storage behind one provider-neutral contract.

Every artifact -- uploaded .dem source, replay JSON, rendered or manually
uploaded video -- goes through this package. Callers import from here; the
modules are the seams:

    contract.py    ArtifactReference / ArtifactMetadata / ArtifactRead and the
                   ArtifactStore protocol every backend implements
    errors.py      the error hierarchy callers catch
    local.py       LocalArtifactStore, the development/test backend
    s3.py          S3ArtifactStore, the production backend
    factory.py     backend selection from settings
    legacy.py      LocalStorageService, the pre-contract local adapter
    _directory.py  descriptor-relative directory primitives used by local.py
    _boundary.py   reference-identity rules shared by both backends
    staging.py     UploadStagingStore, local-disk parts of unfinished chunked
                   uploads (not an artifact kind; ignores the backend setting)
"""

from app.services.storage._directory import SUPPORTS_DIRECTORY_FD, _PortableArtifactDirectory, _PosixArtifactDirectory
from app.services.storage.contract import (
    ARTIFACT_CLEANUP_BATCH_SIZE,
    ARTIFACT_PURGE_BATCH_SIZE,
    ArtifactMetadata,
    ArtifactPurgeResult,
    ArtifactRead,
    ArtifactReference,
    ArtifactStore,
)
from app.services.storage.errors import (
    ArtifactBindingError,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRangeError,
    ArtifactReferenceError,
    ArtifactStoreError,
    ArtifactTooLargeError,
    StorageKeyError,
    StorageWriteError,
)
from app.services.storage.factory import artifact_store_from_settings, create_artifact_store
from app.services.storage.legacy import LocalStorageService
from app.services.storage.local import LocalArtifactStore
from app.services.storage.s3 import S3ArtifactStore
from app.services.storage.staging import (
    MAX_UPLOAD_PART_INDEX,
    ConcatPartStream,
    PartInfo,
    StagingDigestMismatch,
    StagingError,
    StagingIncomplete,
    StagingPartInvalid,
    StagingSessionDir,
    StagingSessionGone,
    UploadStagingStore,
    upload_part_count,
    upload_part_size,
    upload_staging_store_from_settings,
)

__all__ = [
    "ARTIFACT_CLEANUP_BATCH_SIZE",
    "ARTIFACT_PURGE_BATCH_SIZE",
    "MAX_UPLOAD_PART_INDEX",
    "SUPPORTS_DIRECTORY_FD",
    "ArtifactBindingError",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactMetadata",
    "ArtifactNotFoundError",
    "ArtifactPurgeResult",
    "ArtifactRangeError",
    "ArtifactRead",
    "ArtifactReference",
    "ArtifactReferenceError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactTooLargeError",
    "ConcatPartStream",
    "LocalArtifactStore",
    "LocalStorageService",
    "PartInfo",
    "S3ArtifactStore",
    "StagingDigestMismatch",
    "StagingError",
    "StagingIncomplete",
    "StagingPartInvalid",
    "StagingSessionDir",
    "StagingSessionGone",
    "StorageKeyError",
    "StorageWriteError",
    "UploadStagingStore",
    "_PortableArtifactDirectory",
    "_PosixArtifactDirectory",
    "artifact_store_from_settings",
    "create_artifact_store",
    "upload_part_count",
    "upload_part_size",
    "upload_staging_store_from_settings",
]
