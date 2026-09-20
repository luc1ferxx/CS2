"""Artifact store selection from runtime settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.storage.contract import ArtifactStore
from app.services.storage.errors import ArtifactStoreError
from app.services.storage.local import LocalArtifactStore
from app.services.storage.s3 import S3ArtifactStore


def create_artifact_store(
    *,
    backend: str,
    artifact_root: Path | None = None,
    bucket: str | None = None,
    prefix: str = "cs2-artifacts-v1",
    region: str | None = None,
    endpoint_url: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    client: Any | None = None,
) -> ArtifactStore:
    normalized_backend = str(backend).strip().lower()
    if normalized_backend == "local":
        if artifact_root is None:
            raise ArtifactStoreError("Local artifact storage root is required")
        return LocalArtifactStore(Path(artifact_root))
    if normalized_backend == "s3":
        if not bucket:
            raise ArtifactStoreError("Object storage bucket is required")
        return S3ArtifactStore(
            bucket=bucket,
            prefix=prefix,
            region=region,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            client=client,
        )
    raise ArtifactStoreError("Artifact storage backend is unsupported")


def artifact_store_from_settings(
    *,
    configured_settings: Any = settings,
    client: Any | None = None,
) -> ArtifactStore:
    return create_artifact_store(
        backend=configured_settings.artifact_storage_backend,
        artifact_root=configured_settings.artifact_storage_root,
        bucket=configured_settings.object_storage_bucket,
        prefix=configured_settings.object_storage_prefix,
        region=configured_settings.object_storage_region,
        endpoint_url=configured_settings.object_storage_endpoint_url or None,
        access_key_id=configured_settings.object_storage_access_key_id or None,
        secret_access_key=configured_settings.object_storage_secret_access_key or None,
        client=client,
    )
