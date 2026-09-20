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
"""

from app.services.storage._directory import SUPPORTS_DIRECTORY_FD, _PortableArtifactDirectory, _PosixArtifactDirectory
from app.services.storage.contract import (
    ARTIFACT_CLEANUP_BATCH_SIZE,
    ArtifactMetadata,
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

__all__ = [
    "ARTIFACT_CLEANUP_BATCH_SIZE",
    "SUPPORTS_DIRECTORY_FD",
    "ArtifactBindingError",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactMetadata",
    "ArtifactNotFoundError",
    "ArtifactRangeError",
    "ArtifactRead",
    "ArtifactReference",
    "ArtifactReferenceError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactTooLargeError",
    "LocalArtifactStore",
    "LocalStorageService",
    "S3ArtifactStore",
    "StorageKeyError",
    "StorageWriteError",
    "_PortableArtifactDirectory",
    "_PosixArtifactDirectory",
    "artifact_store_from_settings",
    "create_artifact_store",
]
