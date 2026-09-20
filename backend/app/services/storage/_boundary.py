"""The reference-identity and binding rules shared by every artifact store backend."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.services.storage._directory import _LEAF_CREATE_FLAGS
from app.services.storage.contract import ARTIFACT_CLEANUP_BATCH_SIZE, ArtifactMetadata, ArtifactRead, ArtifactReference
from app.services.storage.errors import (
    ArtifactBindingError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStoreError,
    ArtifactTooLargeError,
)


class _ArtifactReferenceBoundary(ABC):
    def new_reference(
        self,
        *,
        owner_id: str,
        demo_id: str,
        kind: str,
        state: str,
        artifact_id: str | None = None,
    ) -> str:
        return ArtifactReference(
            owner_id=owner_id,
            demo_id=demo_id,
            kind=kind,
            state=state,
            artifact_id=artifact_id or uuid.uuid4().hex,
        ).to_uri()

    def parse_reference(self, reference: str) -> ArtifactReference:
        return ArtifactReference.parse(reference)

    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> ArtifactReference:
        parsed = self.parse_reference(reference)
        if (
            parsed.owner_id != owner_id
            or parsed.demo_id != demo_id
            or (kind is not None and parsed.kind != kind)
            or (state is not None and parsed.state != state)
        ):
            raise ArtifactBindingError("Artifact binding does not match")
        return parsed

    @contextmanager
    def materialize(
        self,
        reference: str,
        *,
        expected_generation: str,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
        suffix: str = ".dem",
    ) -> Iterator[Path]:
        parsed = self.parse_reference(reference)
        if parsed.state != "accepted":
            raise ArtifactBindingError("Only accepted artifacts can be materialized")
        if (
            expected_size < 0
            or expected_size > max_bytes
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
            or not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix)
        ):
            raise ArtifactIntegrityError("Artifact materialization metadata is invalid")
        metadata = self.head(reference)
        if metadata is None:
            raise ArtifactNotFoundError("Artifact was not found")
        if (
            metadata.generation != expected_generation
            or metadata.size_bytes != expected_size
            or not hmac.compare_digest(metadata.sha256, expected_sha256)
        ):
            raise ArtifactIntegrityError("Artifact materialization integrity check failed")

        with tempfile.TemporaryDirectory(prefix="cs2-artifact-") as directory:
            directory_path = Path(directory)
            os.chmod(directory_path, 0o700)
            materialized_path = directory_path / f"artifact{suffix}"
            file_fd = os.open(materialized_path, _LEAF_CREATE_FLAGS, 0o600)
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                with os.fdopen(file_fd, "wb", closefd=True) as output:
                    with self.read_range(
                        reference,
                        expected_generation=expected_generation,
                    ) as opened:
                        for chunk in opened.iter_chunks():
                            size_bytes += len(chunk)
                            if size_bytes > max_bytes:
                                raise ArtifactTooLargeError(
                                    "Artifact exceeds materialization size limit"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                materialized_path.unlink(missing_ok=True)
                raise
            if (
                size_bytes != expected_size
                or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
            ):
                materialized_path.unlink(missing_ok=True)
                raise ArtifactIntegrityError("Artifact materialization integrity check failed")
            try:
                yield materialized_path
            finally:
                materialized_path.unlink(missing_ok=True)

    def cleanup_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> list[str]:
        removed: list[str] = []
        attempted = 0
        for metadata in self.iter_quarantine_before(
            cutoff,
            owner_id=owner_id,
            demo_id=demo_id,
        ):
            if attempted >= ARTIFACT_CLEANUP_BATCH_SIZE:
                break
            attempted += 1
            try:
                if self.delete(
                    metadata.reference,
                    expected_generation=metadata.generation,
                ):
                    removed.append(metadata.reference)
            except ArtifactStoreError:
                continue
        return removed

    # Provided by the concrete backend. The shared materialize/cleanup paths
    # above depend on exactly these four members of the ArtifactStore contract.
    @abstractmethod
    def head(self, reference: str) -> ArtifactMetadata | None: ...

    @abstractmethod
    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> ArtifactRead: ...

    @abstractmethod
    def delete(
        self,
        reference: str,
        *,
        expected_generation: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def iter_quarantine_before(
        self,
        cutoff: datetime,
        *,
        owner_id: str | None = None,
        demo_id: str | None = None,
    ) -> Iterator[ArtifactMetadata]: ...
