"""Exceptions raised by the artifact storage boundary."""

from __future__ import annotations


class StorageKeyError(ValueError):
    pass


class StorageWriteError(RuntimeError):
    pass


class ArtifactStoreError(RuntimeError):
    """Safe storage-boundary failure without backend details in its message."""


class ArtifactReferenceError(ArtifactStoreError, ValueError):
    pass


class ArtifactBindingError(ArtifactStoreError):
    pass


class ArtifactNotFoundError(ArtifactStoreError):
    pass


class ArtifactConflictError(ArtifactStoreError):
    pass


class ArtifactIntegrityError(ArtifactStoreError):
    pass


class ArtifactTooLargeError(ArtifactStoreError):
    pass


class ArtifactRangeError(ArtifactStoreError):
    pass
