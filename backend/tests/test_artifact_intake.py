import io
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.artifact_intake import (
    AcceptedArtifact,
    ArtifactIntakeError,
    ArtifactIntakePolicy,
    ArtifactIntakeService,
)
from app.services.storage import (
    ArtifactIntegrityError,
    ArtifactStoreError,
    LocalArtifactStore,
)

NOW = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
VALID_DEMO = b"HL2DEMO\x00" + (b"bounded-demo-payload" * 2)


class TrackingStore:
    def __init__(self, delegate: LocalArtifactStore):
        self.delegate = delegate
        self.references: list[str] = []
        self.deleted: list[str] = []

    def new_reference(self, **kwargs):
        reference = self.delegate.new_reference(**kwargs)
        self.references.append(reference)
        return reference

    def delete(self, reference, **kwargs):
        self.deleted.append(reference)
        return self.delegate.delete(reference, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class InterruptingStream:
    def __init__(self):
        self.read_count = 0

    def read(self, _size=-1):
        self.read_count += 1
        if self.read_count == 1:
            return VALID_DEMO[:20]
        raise OSError("/Users/private/source.dem secret-access-key traceback")


class PromoteThenFailStore(TrackingStore):
    def __init__(self, delegate: LocalArtifactStore):
        super().__init__(delegate)
        self.partial_accepted_reference: str | None = None

    def promote(self, *args, **kwargs):
        accepted = self.delegate.promote(*args, **kwargs)
        self.partial_accepted_reference = accepted.reference
        raise ArtifactStoreError(
            "s3://private-bucket/object?secret=credential /private/path traceback"
        )


class TamperedAcceptedHeadStore(TrackingStore):
    def __init__(self, delegate: LocalArtifactStore, field: str):
        super().__init__(delegate)
        self.field = field
        self.accepted_reference: str | None = None

    def promote(self, *args, **kwargs):
        accepted = self.delegate.promote(*args, **kwargs)
        self.accepted_reference = accepted.reference
        return accepted

    def head(self, reference):
        metadata = self.delegate.head(reference)
        if metadata is None or reference != self.accepted_reference:
            return metadata
        if self.field == "generation":
            return replace(metadata, generation="different-generation")
        if self.field == "size_bytes":
            return replace(metadata, size_bytes=metadata.size_bytes + 1)
        if self.field == "sha256":
            return replace(metadata, sha256="0" * 64)
        raise AssertionError(f"Unsupported tamper field: {self.field}")


class AcceptedHeadFailureStore(TrackingStore):
    def __init__(self, delegate: LocalArtifactStore):
        super().__init__(delegate)
        self.accepted_reference: str | None = None

    def promote(self, *args, **kwargs):
        accepted = self.delegate.promote(*args, **kwargs)
        self.accepted_reference = accepted.reference
        return accepted

    def head(self, reference):
        if reference == self.accepted_reference:
            raise ArtifactStoreError(
                "endpoint=https://objects.invalid bucket=secret /private/path traceback"
            )
        return self.delegate.head(reference)


class ArtifactIntakeTest(unittest.TestCase):
    def _service(
        self,
        store,
        *,
        max_bytes: int = 1024,
        min_bytes: int = 16,
        now=lambda: NOW,
    ) -> ArtifactIntakeService:
        return ArtifactIntakeService(
            store,
            policy=ArtifactIntakePolicy(
                max_source_bytes=max_bytes,
                min_source_bytes=min_bytes,
                stream_chunk_bytes=1024 * 1024,
                quarantine_ttl_seconds=3600,
            ),
            now=now,
        )

    def _intake(self, service: ArtifactIntakeService, **overrides) -> AcceptedArtifact:
        values = {
            "owner_id": "owner-a",
            "demo_id": "demo-a",
            "filename": "match.dem",
            "content_type": "application/octet-stream",
            "stream": io.BytesIO(VALID_DEMO),
        }
        values.update(overrides)
        return service.intake_demo(**values)

    def test_policy_cannot_weaken_the_v1_minimum_or_one_gib_maximum(self) -> None:
        invalid_values = (
            {"min_source_bytes": 15},
            {"max_source_bytes": (1024 * 1024 * 1024) + 1},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ArtifactIntakePolicy(**values)

    def test_valid_demo_moves_from_quarantine_to_verified_accepted_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(LocalArtifactStore(Path(directory)))
            accepted = self._intake(self._service(store), filename=" Match 01.DEM ")

            self.assertEqual(accepted.policy_version, "artifact_intake_v1")
            self.assertEqual(accepted.state, "accepted")
            self.assertEqual(accepted.owner_id, "owner-a")
            self.assertEqual(accepted.demo_id, "demo-a")
            self.assertEqual(accepted.kind, "source")
            self.assertEqual(accepted.display_filename, "Match_01.DEM")
            self.assertEqual(accepted.size_bytes, len(VALID_DEMO))
            self.assertEqual(accepted.accepted_at, NOW)
            self.assertEqual(len(store.references), 1)

            quarantine = store.references[0]
            self.assertIsNone(store.head(quarantine))
            found = store.head(accepted.reference)
            self.assertIsNotNone(found)
            self.assertEqual(found.generation, accepted.generation)
            self.assertEqual(found.size_bytes, accepted.size_bytes)
            self.assertEqual(found.sha256, accepted.sha256)
            with store.read_range(
                accepted.reference,
                expected_generation=accepted.generation,
            ) as opened:
                self.assertEqual(b"".join(opened.iter_chunks(7)), VALID_DEMO)

            self.assertEqual(
                accepted.as_snapshot(),
                {
                    "policyVersion": "artifact_intake_v1",
                    "state": "accepted",
                    "reference": accepted.reference,
                    "sizeBytes": len(VALID_DEMO),
                    "sha256": accepted.sha256,
                    "acceptedAt": "2026-07-16T10:30:00Z",
                    "generation": accepted.generation,
                },
            )

    def test_only_public_dem_extension_is_accepted_before_any_write(self) -> None:
        filenames = ("match.zip", "match.mp4", "match", "match.dem.zip")
        for filename in filenames:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                store = TrackingStore(LocalArtifactStore(Path(directory)))
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(self._service(store), filename=filename)
                self.assertEqual(raised.exception.code, "INTAKE_TYPE_REJECTED")
                self.assertEqual(store.references, [])

    def test_path_absolute_and_separator_filenames_are_rejected_before_write(self) -> None:
        filenames = (
            "../match.dem",
            "/tmp/match.dem",
            "nested/match.dem",
            "nested\\match.dem",
            "C:\\temp\\match.dem",
            "..\\match.dem",
        )
        for filename in filenames:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                store = TrackingStore(LocalArtifactStore(Path(directory)))
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(self._service(store), filename=filename)
                self.assertEqual(raised.exception.code, "INTAKE_TYPE_REJECTED")
                self.assertEqual(store.references, [])

    def test_explicit_archive_or_executable_media_type_is_content_mismatch(self) -> None:
        content_types = (
            "application/zip",
            "application/x-7z-compressed",
            "application/x-msdownload",
            "application/vnd.microsoft.portable-executable",
        )
        for content_type in content_types:
            with self.subTest(content_type=content_type), tempfile.TemporaryDirectory() as directory:
                store = TrackingStore(LocalArtifactStore(Path(directory)))
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(self._service(store), content_type=content_type)
                self.assertEqual(raised.exception.code, "INTAKE_CONTENT_MISMATCH")
                self.assertEqual(store.references, [])

    def test_zip_content_disguised_as_dem_is_deleted_from_quarantine(self) -> None:
        payloads = (
            b"PK\x03\x04" + b"archive" * 4,
            b"PK\x05\x06" + b"empty-archive" * 2,
            b"PK\x07\x08" + b"spanned" * 4,
        )
        for payload in payloads:
            with self.subTest(prefix=payload[:4]), tempfile.TemporaryDirectory() as directory:
                store = TrackingStore(LocalArtifactStore(Path(directory)))
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(self._service(store), stream=io.BytesIO(payload))
                self.assertEqual(raised.exception.code, "INTAKE_CONTENT_MISMATCH")
                self.assertEqual(len(store.references), 1)
                self.assertIsNone(store.head(store.references[0]))

    def test_empty_truncated_and_oversized_streams_have_stable_codes_and_no_artifact(self) -> None:
        cases = (
            (b"", 32, "INTAKE_EMPTY"),
            (b"short", 32, "INTAKE_TRUNCATED"),
            (b"x" * 33, 32, "INTAKE_TOO_LARGE"),
        )
        for payload, max_bytes, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                store = TrackingStore(LocalArtifactStore(Path(directory)))
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(
                        self._service(store, max_bytes=max_bytes),
                        stream=io.BytesIO(payload),
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(len(store.references), 1)
                self.assertIsNone(store.head(store.references[0]))

    def test_stream_interruption_is_safe_and_leaves_no_consumable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(LocalArtifactStore(Path(directory)))
            with self.assertRaises(ArtifactIntakeError) as raised:
                self._intake(self._service(store), stream=InterruptingStream())

            self.assertEqual(raised.exception.code, "INTAKE_STORAGE_UNAVAILABLE")
            self.assertEqual(len(store.references), 1)
            self.assertIsNone(store.head(store.references[0]))
            self.assertNotIn("/Users/private", str(raised.exception))
            self.assertNotIn("secret-access-key", str(raised.exception))
            self.assertNotIn("traceback", str(raised.exception).lower())

    def test_failed_promotion_removes_partial_accepted_and_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            delegate = LocalArtifactStore(Path(directory))
            store = PromoteThenFailStore(delegate)
            with self.assertRaises(ArtifactIntakeError) as raised:
                self._intake(self._service(store))

            self.assertEqual(raised.exception.code, "INTAKE_STORAGE_UNAVAILABLE")
            self.assertIsNotNone(store.partial_accepted_reference)
            self.assertIsNone(delegate.head(store.partial_accepted_reference))
            self.assertIsNone(delegate.head(store.references[0]))
            self.assertNotIn("private-bucket", str(raised.exception))
            self.assertNotIn("credential", str(raised.exception))
            self.assertNotIn("/private/path", str(raised.exception))

    def test_accepted_head_generation_size_or_digest_mismatch_is_integrity_failure(self) -> None:
        for field in ("generation", "size_bytes", "sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                delegate = LocalArtifactStore(Path(directory))
                store = TamperedAcceptedHeadStore(delegate, field)
                with self.assertRaises(ArtifactIntakeError) as raised:
                    self._intake(self._service(store))

                self.assertEqual(raised.exception.code, "INTAKE_INTEGRITY_FAILED")
                self.assertIsNotNone(store.accepted_reference)
                self.assertIsNone(delegate.head(store.accepted_reference))
                self.assertIsNone(delegate.head(store.references[0]))

    def test_accepted_head_storage_failure_is_safe_and_cleans_promoted_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            delegate = LocalArtifactStore(Path(directory))
            store = AcceptedHeadFailureStore(delegate)
            with self.assertRaises(ArtifactIntakeError) as raised:
                self._intake(self._service(store))

            self.assertEqual(raised.exception.code, "INTAKE_STORAGE_UNAVAILABLE")
            self.assertIsNotNone(store.accepted_reference)
            self.assertIsNone(delegate.head(store.accepted_reference))
            self.assertNotIn("objects.invalid", str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))
            self.assertNotIn("/private/path", str(raised.exception))
            self.assertNotIn("traceback", str(raised.exception).lower())

    def test_integrity_exception_from_promotion_has_no_raw_storage_details(self) -> None:
        class IntegrityFailureStore(TrackingStore):
            def promote(self, *args, **kwargs):
                raise ArtifactIntegrityError(
                    "etag mismatch s3://private-bucket /private/path traceback secret"
                )

        with tempfile.TemporaryDirectory() as directory:
            store = IntegrityFailureStore(LocalArtifactStore(Path(directory)))
            with self.assertRaises(ArtifactIntakeError) as raised:
                self._intake(self._service(store))

            self.assertEqual(raised.exception.code, "INTAKE_INTEGRITY_FAILED")
            self.assertEqual(str(raised.exception), "Uploaded demo failed integrity validation")
            self.assertIsNone(raised.exception.__cause__)
            self.assertTrue(raised.exception.__suppress_context__)

    def test_fake_clock_cleanup_removes_only_expired_scoped_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            old_owned = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
            )
            recent_owned = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-b",
                kind="source",
                state="quarantine",
            )
            old_foreign = store.new_reference(
                owner_id="owner-b",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
            )
            accepted = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="accepted",
            )
            store.write_stream(
                old_owned,
                io.BytesIO(VALID_DEMO),
                max_bytes=1024,
                now=NOW - timedelta(hours=2),
            )
            store.write_stream(
                recent_owned,
                io.BytesIO(VALID_DEMO),
                max_bytes=1024,
                now=NOW - timedelta(minutes=30),
            )
            store.write_stream(
                old_foreign,
                io.BytesIO(VALID_DEMO),
                max_bytes=1024,
                now=NOW - timedelta(hours=2),
            )
            store.write_stream(
                accepted,
                io.BytesIO(VALID_DEMO),
                max_bytes=1024,
                now=NOW - timedelta(hours=2),
            )

            service = self._service(store, now=lambda: NOW)
            cleaned = service.cleanup_abandoned(owner_id="owner-a")

            self.assertEqual(cleaned, 1)
            self.assertIsNone(store.head(old_owned))
            self.assertIsNotNone(store.head(recent_owned))
            self.assertIsNotNone(store.head(old_foreign))
            self.assertIsNotNone(store.head(accepted))
            self.assertEqual(service.cleanup_abandoned(owner_id="owner-a"), 0)

    def test_cleanup_storage_failure_is_stable_and_safe(self) -> None:
        class CleanupFailureStore:
            def cleanup_quarantine_before(self, *args, **kwargs):
                raise RuntimeError(
                    "endpoint=https://secret.invalid /Users/private token traceback"
                )

        service = self._service(CleanupFailureStore())
        with self.assertRaises(ArtifactIntakeError) as raised:
            service.cleanup_abandoned()

        self.assertEqual(raised.exception.code, "INTAKE_STORAGE_UNAVAILABLE")
        self.assertEqual(str(raised.exception), "Artifact storage is temporarily unavailable")
        self.assertNotIn("secret.invalid", str(raised.exception))
        self.assertNotIn("/Users/private", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
