import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.storage import (
    ArtifactMetadata,
    ArtifactBindingError,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactReferenceError,
    ArtifactStoreError,
    ArtifactTooLargeError,
    LocalArtifactStore,
    S3ArtifactStore,
    create_artifact_store,
)


class _FakeS3Error(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@dataclass
class _FakeS3Object:
    body: bytes
    metadata: dict[str, str]
    content_type: str | None
    etag: str
    version_id: str
    last_modified: datetime


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], list[_FakeS3Object]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.copy_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self._version = 0

    def put_object(self, **kwargs):
        self.put_calls.append(dict(kwargs))
        if kwargs.get("IfNoneMatch") == "*" and self.objects.get(
            (kwargs["Bucket"], kwargs["Key"])
        ):
            raise _FakeS3Error("PreconditionFailed")
        body = kwargs["Body"]
        body.seek(0)
        payload = body.read()
        self._version += 1
        stored = _FakeS3Object(
            body=payload,
            metadata=dict(kwargs.get("Metadata", {})),
            content_type=kwargs.get("ContentType"),
            etag=hashlib.sha256(payload).hexdigest(),
            version_id=str(self._version),
            last_modified=datetime.now(timezone.utc),
        )
        self.objects.setdefault((kwargs["Bucket"], kwargs["Key"]), []).append(stored)
        return {"ETag": f'"{stored.etag}"', "VersionId": stored.version_id}

    def head_object(self, **kwargs):
        stored = self._get(kwargs["Bucket"], kwargs["Key"], kwargs.get("VersionId"))
        return {
            "ContentLength": len(stored.body),
            "Metadata": dict(stored.metadata),
            "ContentType": stored.content_type,
            "ETag": f'"{stored.etag}"',
            "VersionId": stored.version_id,
            "LastModified": stored.last_modified,
        }

    def get_object(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        stored = self._get(kwargs["Bucket"], kwargs["Key"], kwargs.get("VersionId"))
        if kwargs.get("IfMatch") not in {None, stored.etag, f'"{stored.etag}"'}:
            raise _FakeS3Error("PreconditionFailed")
        start = 0
        end = len(stored.body) - 1
        if "Range" in kwargs:
            value = str(kwargs["Range"])
            if not value.startswith("bytes="):
                raise _FakeS3Error("InvalidRange")
            raw_start, raw_end = value[len("bytes=") :].split("-", 1)
            start = int(raw_start)
            end = int(raw_end)
        body = stored.body[start : end + 1]
        return {
            "Body": io.BytesIO(body),
            "ContentLength": len(body),
            "ETag": f'"{stored.etag}"',
            "VersionId": stored.version_id,
        }

    def copy_object(self, **kwargs):
        self.copy_calls.append(dict(kwargs))
        source = kwargs["CopySource"]
        stored = self._get(source["Bucket"], source["Key"], source.get("VersionId"))
        if kwargs.get("CopySourceIfMatch") not in {
            None,
            stored.etag,
            f'"{stored.etag}"',
        }:
            raise _FakeS3Error("PreconditionFailed")
        return self.put_object(
            Bucket=kwargs["Bucket"],
            Key=kwargs["Key"],
            Body=io.BytesIO(stored.body),
            Metadata=kwargs["Metadata"],
            ContentType=kwargs.get("ContentType"),
            IfNoneMatch=kwargs.get("IfNoneMatch"),
        )

    def delete_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        versions = self.objects.get(key, [])
        if not versions:
            return {}
        if kwargs.get("IfMatch") not in {
            None,
            versions[-1].etag,
            f'"{versions[-1].etag}"',
        }:
            raise _FakeS3Error("PreconditionFailed")
        version_id = kwargs.get("VersionId")
        if version_id is not None:
            self.objects[key] = [item for item in versions if item.version_id != version_id]
        else:
            self.objects.pop(key, None)
        if not self.objects.get(key):
            self.objects.pop(key, None)
        return {}

    def list_objects_v2(self, **kwargs):
        bucket = kwargs["Bucket"]
        prefix = kwargs["Prefix"]
        contents = []
        for (stored_bucket, key), versions in sorted(self.objects.items()):
            if stored_bucket == bucket and key.startswith(prefix) and versions:
                contents.append(
                    {
                        "Key": key,
                        "LastModified": versions[-1].last_modified,
                    }
                )
        return {"Contents": contents, "IsTruncated": False}

    def _get(self, bucket: str, key: str, version_id: str | None = None) -> _FakeS3Object:
        versions = self.objects.get((bucket, key), [])
        if version_id is None:
            if not versions:
                raise _FakeS3Error("NoSuchKey")
            return versions[-1]
        for stored in versions:
            if stored.version_id == version_id:
                return stored
        raise _FakeS3Error("NoSuchVersion")


class _AcceptedPutRaceS3Client(_FakeS3Client):
    def __init__(self) -> None:
        super().__init__()
        self.arm_race = False

    def put_object(self, **kwargs):
        if (
            self.arm_race
            and "/accepted/" in kwargs["Key"]
            and kwargs.get("IfNoneMatch") == "*"
        ):
            self.arm_race = False
            competing = dict(kwargs)
            competing.pop("IfNoneMatch", None)
            body = kwargs["Body"]
            body.seek(0)
            competing["Body"] = io.BytesIO(body.read())
            super().put_object(**competing)
            body.seek(0)
        return super().put_object(**kwargs)


class _TimeoutAfterAcceptedPutS3Client(_FakeS3Client):
    def __init__(self) -> None:
        super().__init__()
        self.arm_timeout = False
        self.target_fragment = "/accepted/"

    def put_object(self, **kwargs):
        response = super().put_object(**kwargs)
        if self.arm_timeout and self.target_fragment in kwargs["Key"]:
            self.arm_timeout = False
            raise OSError("timeout after server persisted private object")
        return response


class _UnknownFailureAfterCompetingPutS3Client(_FakeS3Client):
    def __init__(self) -> None:
        super().__init__()
        self.arm_fragment: str | None = None

    def put_object(self, **kwargs):
        if self.arm_fragment and self.arm_fragment in kwargs["Key"]:
            self.arm_fragment = None
            competing = dict(kwargs)
            competing.pop("IfNoneMatch", None)
            body = kwargs["Body"]
            body.seek(0)
            competing["Body"] = io.BytesIO(body.read())
            body.seek(0)
            competing_metadata = dict(kwargs.get("Metadata", {}))
            competing_metadata["write-token"] = "competing-operation"
            competing["Metadata"] = competing_metadata
            super().put_object(**competing)
            raise OSError("unknown failure after a competing object appeared")
        return super().put_object(**kwargs)


class _UnversionedFakeS3Client(_FakeS3Client):
    def put_object(self, **kwargs):
        response = super().put_object(**kwargs)
        response.pop("VersionId", None)
        return response

    def head_object(self, **kwargs):
        response = super().head_object(**kwargs)
        response.pop("VersionId", None)
        return response

    def get_object(self, **kwargs):
        response = super().get_object(**kwargs)
        response.pop("VersionId", None)
        return response


class _ReadOnlyStream:
    def __init__(self, payload: bytes) -> None:
        self._body = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class ArtifactStoreContractTest(unittest.TestCase):
    def test_logical_reference_round_trips_owner_demo_kind_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))

            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="a" * 32,
            )
            parsed = store.parse_reference(reference)

            self.assertEqual(parsed.owner_id, "owner-a")
            self.assertEqual(parsed.demo_id, "demo-a")
            self.assertEqual(parsed.kind, "source")
            self.assertEqual(parsed.state, "quarantine")
            self.assertEqual(parsed.artifact_id, "a" * 32)
            self.assertEqual(parsed.to_uri(), reference)

    def test_metadata_rejects_control_characters_in_generation(self) -> None:
        reference = (
            "artifact://v1/accepted/source/b3duZXItYQ/ZGVtby1h/"
            + "a" * 32
        )

        with self.assertRaises(ArtifactIntegrityError):
            ArtifactMetadata(
                reference=reference,
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="accepted",
                size_bytes=1,
                sha256=hashlib.sha256(b"x").hexdigest(),
                generation="version:secret\nheader",
                created_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            )

    def test_binding_rejects_another_owner_or_demo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="video",
                state="accepted",
                artifact_id="b" * 32,
            )

            with self.assertRaises(ArtifactBindingError):
                store.require_binding(
                    reference,
                    owner_id="owner-b",
                    demo_id="demo-a",
                    kind="video",
                    state="accepted",
                )
            with self.assertRaises(ArtifactBindingError):
                store.require_binding(
                    reference,
                    owner_id="owner-a",
                    demo_id="demo-b",
                    kind="video",
                    state="accepted",
                )

    def test_bounded_write_and_head_preserve_integrity_metadata(self) -> None:
        payload = b"bounded artifact payload"
        created_at = datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="c" * 32,
            )

            written = store.write_stream(
                reference,
                io.BytesIO(payload),
                max_bytes=1024,
                chunk_size=4,
                expected_size=len(payload),
                content_type="application/octet-stream",
                policy_version="artifact_intake_v1",
                now=created_at,
            )
            found = store.head(reference)

            self.assertEqual(found, written)
            self.assertEqual(written.reference, reference)
            self.assertEqual(written.size_bytes, len(payload))
            self.assertEqual(written.sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(written.created_at, created_at)
            self.assertEqual(written.content_type, "application/octet-stream")
            self.assertEqual(written.policy_version, "artifact_intake_v1")
            self.assertEqual(
                written.as_snapshot()["policyVersion"],
                "artifact_intake_v1",
            )
            self.assertTrue(written.generation)

    def test_oversized_write_leaves_no_consumable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="d" * 32,
            )

            with self.assertRaises(ArtifactTooLargeError):
                store.write_stream(
                    reference,
                    io.BytesIO(b"0123456789"),
                    max_bytes=8,
                    chunk_size=3,
                )

            self.assertIsNone(store.head(reference))

    def test_range_read_is_bound_to_the_validated_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="video",
                state="accepted",
                artifact_id="e" * 32,
            )
            metadata = store.write_stream(
                reference,
                io.BytesIO(b"0123456789"),
                max_bytes=10,
            )

            with store.read_range(
                reference,
                start=2,
                end_inclusive=5,
                expected_generation=metadata.generation,
            ) as opened:
                self.assertEqual(opened.read(), b"2345")
                self.assertEqual(opened.start, 2)
                self.assertEqual(opened.end_inclusive, 5)
                self.assertEqual(opened.total_size, 10)

            with self.assertRaises(ArtifactIntegrityError):
                store.read_range(
                    reference,
                    expected_generation="wrong-generation",
                )

    def test_promotion_verifies_integrity_and_removes_quarantine(self) -> None:
        payload = b"accepted demo artifact"
        accepted_at = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            quarantine = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="f" * 32,
            )
            written = store.write_stream(
                quarantine,
                io.BytesIO(payload),
                max_bytes=1024,
            )

            accepted = store.promote(
                quarantine,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
                now=accepted_at,
            )

            self.assertEqual(accepted.state, "accepted")
            self.assertEqual(accepted.created_at, accepted_at)
            self.assertIsNone(store.head(quarantine))
            self.assertEqual(store.head(accepted.reference), accepted)
            with store.read_range(
                accepted.reference,
                expected_generation=accepted.generation,
            ) as opened:
                self.assertEqual(opened.read(), payload)

    def test_local_promotion_rehashes_same_size_content_before_accepting(self) -> None:
        payload = b"original artifact bytes"
        replacement = b"tampered artifact bytes"
        self.assertEqual(len(payload), len(replacement))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root)
            quarantine = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="0" * 32,
            )
            written = store.write_stream(
                quarantine,
                io.BytesIO(payload),
                max_bytes=1024,
            )
            stored_blob = next(root.rglob("0" * 32 + ".blob"))
            original_stat = stored_blob.stat()
            stored_blob.write_bytes(replacement)
            stored_blob.chmod(0o600)
            os.utime(
                stored_blob,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            self.assertEqual(store.head(quarantine), written)

            with self.assertRaises(ArtifactIntegrityError):
                store.promote(
                    quarantine,
                    expected_generation=written.generation,
                    expected_size=written.size_bytes,
                    expected_sha256=written.sha256,
                )

            accepted_reference = quarantine.replace("/quarantine/", "/accepted/")
            self.assertIsNone(store.head(accepted_reference))
            self.assertIsNotNone(store.head(quarantine))

    def test_local_promotion_rehashes_the_published_target_after_link_race(self) -> None:
        payload = b"original promotion data"
        replacement = b"tampered promotion data"
        self.assertEqual(len(payload), len(replacement))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root)
            quarantine = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="6" * 32,
            )
            written = store.write_stream(
                quarantine,
                io.BytesIO(payload),
                max_bytes=1024,
            )
            stored_blob = next(root.rglob("6" * 32 + ".blob"))
            original_stat = stored_blob.stat()
            original_link = os.link
            raced = False

            def link_after_replacement(*args, **kwargs):
                nonlocal raced
                if not raced:
                    raced = True
                    stored_blob.write_bytes(replacement)
                    stored_blob.chmod(0o600)
                    os.utime(
                        stored_blob,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                return original_link(*args, **kwargs)

            with patch("app.services.storage.os.link", side_effect=link_after_replacement):
                with self.assertRaises(ArtifactIntegrityError):
                    store.promote(
                        quarantine,
                        expected_generation=written.generation,
                        expected_size=written.size_bytes,
                        expected_sha256=written.sha256,
                    )

            accepted_reference = quarantine.replace("/quarantine/", "/accepted/")
            self.assertIsNone(store.head(accepted_reference))

    def test_materialization_is_verified_private_and_cleaned_on_exit(self) -> None:
        payload = b"materialized accepted artifact"
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="accepted",
                artifact_id="1" * 32,
            )
            metadata = store.write_stream(
                reference,
                io.BytesIO(payload),
                max_bytes=1024,
            )

            with store.materialize(
                reference,
                expected_generation=metadata.generation,
                expected_size=metadata.size_bytes,
                expected_sha256=metadata.sha256,
                max_bytes=1024,
                suffix=".dem",
            ) as materialized:
                self.assertEqual(materialized.read_bytes(), payload)
                self.assertEqual(materialized.suffix, ".dem")
                self.assertEqual(materialized.stat().st_mode & 0o777, 0o600)
                materialized_parent = materialized.parent

            self.assertFalse(materialized.exists())
            self.assertFalse(materialized_parent.exists())

    def test_materialization_cleans_private_file_when_consumer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="accepted",
                artifact_id="5" * 32,
            )
            metadata = store.write_stream(
                reference,
                io.BytesIO(b"cleanup after consumer failure"),
                max_bytes=1024,
            )

            with self.assertRaisesRegex(RuntimeError, "consumer failed"):
                with store.materialize(
                    reference,
                    expected_generation=metadata.generation,
                    expected_size=metadata.size_bytes,
                    expected_sha256=metadata.sha256,
                    max_bytes=1024,
                ) as materialized:
                    materialized_parent = materialized.parent
                    raise RuntimeError("consumer failed")

            self.assertFalse(materialized.exists())
            self.assertFalse(materialized_parent.exists())

    def test_ttl_cleanup_lists_only_old_valid_quarantine_objects(self) -> None:
        now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            old_quarantine = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-old",
                kind="source",
                state="quarantine",
                artifact_id="2" * 32,
            )
            fresh_quarantine = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-fresh",
                kind="source",
                state="quarantine",
                artifact_id="3" * 32,
            )
            accepted = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-accepted",
                kind="source",
                state="accepted",
                artifact_id="4" * 32,
            )
            old_metadata = store.write_stream(
                old_quarantine,
                io.BytesIO(b"old quarantine"),
                max_bytes=1024,
                now=now - timedelta(hours=2),
            )
            store.write_stream(
                fresh_quarantine,
                io.BytesIO(b"fresh quarantine"),
                max_bytes=1024,
                now=now - timedelta(minutes=15),
            )
            store.write_stream(
                accepted,
                io.BytesIO(b"accepted artifact"),
                max_bytes=1024,
                now=now - timedelta(days=1),
            )

            abandoned = list(
                store.iter_quarantine_before(
                    now - timedelta(hours=1),
                    owner_id="owner-a",
                )
            )
            cleaned = store.cleanup_quarantine_before(
                now - timedelta(hours=1),
                owner_id="owner-a",
            )

            self.assertEqual(abandoned, [old_metadata])
            self.assertEqual(cleaned, [old_quarantine])
            self.assertIsNone(store.head(old_quarantine))
            self.assertIsNotNone(store.head(fresh_quarantine))
            self.assertIsNotNone(store.head(accepted))

    def test_logical_references_reject_traversal_and_noncanonical_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))

            for reference in (
                "artifact://v1/quarantine/source/../demo/" + "a" * 32,
                "artifact://v1/quarantine/source/b3duZXItYQ/ZGVtby1h/" + "a" * 32 + "?x=1",
                "artifact://v1/quarantine/source/b3duZXItYQ/ZGVtby1h/%2e%2e",
                "artifact://other/quarantine/source/b3duZXItYQ/ZGVtby1h/" + "a" * 32,
            ):
                with self.subTest(reference=reference):
                    with self.assertRaises(ArtifactReferenceError):
                        store.parse_reference(reference)

    def test_local_store_rejects_symlink_replacement_without_reading_bytes(self) -> None:
        payload = b"private artifact"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root)
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="video",
                state="accepted",
                artifact_id="5" * 32,
            )
            store.write_stream(reference, io.BytesIO(payload), max_bytes=1024)
            stored_blob = next(root.rglob("5" * 32 + ".blob"))
            outside = root / "outside.bin"
            outside.write_bytes(b"outside secret")
            stored_blob.unlink()
            stored_blob.symlink_to(outside)

            with self.assertRaises(ArtifactIntegrityError):
                store.head(reference)
            with self.assertRaises(ArtifactIntegrityError):
                store.read_range(reference)

    def test_local_store_rejects_symlinked_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root)
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="4" * 32,
            )
            store.write_stream(
                reference,
                io.BytesIO(b"parent symlink artifact"),
                max_bytes=1024,
            )
            stored_blob = next(root.rglob("4" * 32 + ".blob"))
            demo_directory = stored_blob.parent
            moved_directory = root / "moved-demo-directory"
            demo_directory.rename(moved_directory)
            demo_directory.symlink_to(moved_directory, target_is_directory=True)

            with self.assertRaises(ArtifactIntegrityError):
                store.head(reference)
            with self.assertRaises(ArtifactNotFoundError):
                store.read_range(reference)

    def test_local_delete_rechecks_generation_inside_the_delete_directory_fd(self) -> None:
        class DeleteRaceStore(LocalArtifactStore):
            def __init__(self, root: Path):
                super().__init__(root)
                self.arm_race = False
                self.open_count = 0

            def _open_artifact_directory(self, reference, *, create):
                directory_fd = super()._open_artifact_directory(reference, create=create)
                if self.arm_race and not create:
                    self.open_count += 1
                    if self.open_count == 1:
                        metadata_path = next(
                            self.artifact_root.rglob(
                                reference.artifact_id + ".metadata.json"
                            )
                        )
                        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                        payload["generation"] = "replacement-generation"
                        metadata_path.write_text(
                            json.dumps(payload, separators=(",", ":")),
                            encoding="utf-8",
                        )
                return directory_fd

        with tempfile.TemporaryDirectory() as directory:
            store = DeleteRaceStore(Path(directory))
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id="f" * 32,
            )
            metadata = store.write_stream(
                reference,
                io.BytesIO(b"delete race"),
                max_bytes=1024,
            )
            store.arm_race = True

            with self.assertRaises(ArtifactIntegrityError):
                store.delete(reference, expected_generation=metadata.generation)

            remaining = store.head(reference)
            self.assertIsNotNone(remaining)
            self.assertEqual(remaining.generation, "replacement-generation")

    def test_s3_store_writes_private_object_and_returns_logical_metadata(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="6" * 32,
        )

        written = store.write_stream(
            reference,
            io.BytesIO(b"object payload"),
            max_bytes=1024,
            expected_size=14,
            policy_version="artifact_intake_v1",
        )

        self.assertEqual(store.head(reference), written)
        self.assertEqual(written.reference, reference)
        self.assertEqual(written.generation, "version:1")
        self.assertEqual(written.policy_version, "artifact_intake_v1")
        self.assertNotIn("ACL", client.put_calls[0])
        self.assertNotIn("private-test-bucket", reference)
        self.assertNotIn(client.put_calls[0]["Key"], reference)

    def test_s3_seekable_write_does_not_duplicate_the_whole_input_to_scratch(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="video",
            state="quarantine",
            artifact_id="4" * 32,
        )

        with patch(
            "app.services.storage.tempfile.SpooledTemporaryFile",
            side_effect=AssertionError("seekable uploads must not use scratch spool"),
        ):
            written = store.write_stream(
                reference,
                io.BytesIO(b"seekable upload body"),
                max_bytes=1024,
            )

        self.assertEqual(written.size_bytes, len(b"seekable upload body"))
        self.assertEqual(store.head(reference), written)

    def test_s3_nonseekable_write_fails_before_consuming_or_spooling(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="5" * 32,
        )

        with self.assertRaisesRegex(ArtifactStoreError, "seekable"):
            store.write_stream(
                reference,
                _ReadOnlyStream(b"must not be buffered"),
                max_bytes=1024,
            )

        self.assertEqual(client.put_calls, [])
        self.assertIsNone(store.head(reference))

    def test_s3_store_refuses_to_overwrite_an_existing_logical_object(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="9" * 32,
        )
        original = store.write_stream(
            reference,
            io.BytesIO(b"original"),
            max_bytes=1024,
        )

        with self.assertRaises(ArtifactConflictError):
            store.write_stream(
                reference,
                io.BytesIO(b"replacement"),
                max_bytes=1024,
            )

        self.assertEqual(store.head(reference), original)
        self.assertEqual(client.put_calls[0]["IfNoneMatch"], "*")

    def test_s3_write_timeout_removes_partial_quarantine_object(self) -> None:
        client = _TimeoutAfterAcceptedPutS3Client()
        client.target_fragment = "/quarantine/"
        client.arm_timeout = True
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="2" * 32,
        )

        with self.assertRaises(ArtifactStoreError):
            store.write_stream(
                reference,
                io.BytesIO(b"write timeout"),
                max_bytes=1024,
            )

        self.assertIsNone(store.head(reference))

    def test_s3_unknown_write_failure_never_deletes_a_competing_object(self) -> None:
        client = _UnknownFailureAfterCompetingPutS3Client()
        client.arm_fragment = "/quarantine/"
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="b" * 32,
        )

        with self.assertRaises(ArtifactStoreError):
            store.write_stream(
                reference,
                io.BytesIO(b"competing write"),
                max_bytes=1024,
            )

        self.assertIsNotNone(store.head(reference))

    def test_s3_range_read_is_version_bound_and_rejects_replacement(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="video",
            state="accepted",
            artifact_id="7" * 32,
        )
        metadata = store.write_stream(
            reference,
            io.BytesIO(b"0123456789"),
            max_bytes=10,
        )

        with store.read_range(
            reference,
            start=2,
            end_inclusive=5,
            expected_generation=metadata.generation,
        ) as opened:
            self.assertEqual(opened.read(), b"2345")
        self.assertEqual(client.get_calls[-1]["VersionId"], "1")

        first_put = client.put_calls[0]
        client.put_object(
            Bucket=first_put["Bucket"],
            Key=first_put["Key"],
            Body=io.BytesIO(b"replacement"),
            Metadata=first_put["Metadata"],
        )
        with self.assertRaises(ArtifactIntegrityError):
            store.read_range(reference, expected_generation=metadata.generation)

    def test_s3_unversioned_reads_and_deletes_use_quoted_etag_conditions(self) -> None:
        client = _UnversionedFakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="video",
            state="accepted",
            artifact_id="3" * 32,
        )
        metadata = store.write_stream(
            reference,
            io.BytesIO(b"etag object"),
            max_bytes=1024,
        )

        with store.read_range(
            reference,
            expected_generation=metadata.generation,
        ) as opened:
            self.assertEqual(opened.read(), b"etag object")

        self.assertTrue(metadata.generation.startswith("etag:"))
        self.assertRegex(client.get_calls[-1]["IfMatch"], r'^"[a-f0-9]{64}"$')
        self.assertTrue(
            store.delete(reference, expected_generation=metadata.generation)
        )
        self.assertIsNone(store.head(reference))

    def test_s3_promotion_verifies_target_and_removes_quarantine_generation(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="8" * 32,
        )
        written = store.write_stream(
            quarantine,
            io.BytesIO(b"promoted object"),
            max_bytes=1024,
        )

        with patch(
            "app.services.storage.tempfile.SpooledTemporaryFile",
            side_effect=AssertionError("promotion must stay inside object storage"),
        ):
            accepted = store.promote(
                quarantine,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
            )

        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(client.copy_calls[0]["IfNoneMatch"], "*")
        self.assertEqual(client.copy_calls[0]["CopySource"]["VersionId"], "1")
        self.assertIsNone(store.head(quarantine))
        self.assertEqual(store.head(accepted.reference), accepted)
        with store.read_range(
            accepted.reference,
            expected_generation=accepted.generation,
        ) as opened:
            self.assertEqual(opened.read(), b"promoted object")

    def test_s3_promotion_never_overwrites_a_racing_accepted_object(self) -> None:
        client = _AcceptedPutRaceS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="e" * 32,
        )
        written = store.write_stream(
            quarantine,
            io.BytesIO(b"race-safe promotion"),
            max_bytes=1024,
        )
        client.arm_race = True

        with self.assertRaises(ArtifactConflictError):
            store.promote(
                quarantine,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
            )

        accepted_reference = quarantine.replace("/quarantine/", "/accepted/")
        self.assertIsNotNone(store.head(accepted_reference))
        self.assertIsNotNone(store.head(quarantine))

    def test_s3_promotion_timeout_cleans_partial_accepted_target(self) -> None:
        client = _TimeoutAfterAcceptedPutS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="1" * 32,
        )
        written = store.write_stream(
            quarantine,
            io.BytesIO(b"timeout promotion"),
            max_bytes=1024,
        )
        client.arm_timeout = True

        with self.assertRaises(ArtifactStoreError):
            store.promote(
                quarantine,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
            )

        accepted_reference = quarantine.replace("/quarantine/", "/accepted/")
        self.assertIsNone(store.head(accepted_reference))
        self.assertIsNotNone(store.head(quarantine))

    def test_s3_unknown_promotion_failure_never_deletes_a_competing_target(self) -> None:
        client = _UnknownFailureAfterCompetingPutS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="quarantine",
            artifact_id="c" * 32,
        )
        written = store.write_stream(
            quarantine,
            io.BytesIO(b"competing promotion"),
            max_bytes=1024,
        )
        client.arm_fragment = "/accepted/"

        with self.assertRaises(ArtifactStoreError):
            store.promote(
                quarantine,
                expected_generation=written.generation,
                expected_size=written.size_bytes,
                expected_sha256=written.sha256,
            )

        accepted_reference = quarantine.replace("/quarantine/", "/accepted/")
        self.assertIsNotNone(store.head(accepted_reference))
        self.assertIsNotNone(store.head(quarantine))

    def test_s3_materialization_is_bounded_and_cleaned(self) -> None:
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        reference = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-a",
            kind="source",
            state="accepted",
            artifact_id="a" * 32,
        )
        metadata = store.write_stream(
            reference,
            io.BytesIO(b"object materialization"),
            max_bytes=1024,
        )

        with store.materialize(
            reference,
            expected_generation=metadata.generation,
            expected_size=metadata.size_bytes,
            expected_sha256=metadata.sha256,
            max_bytes=1024,
        ) as materialized:
            self.assertEqual(materialized.read_bytes(), b"object materialization")
            materialized_parent = materialized.parent

        self.assertFalse(materialized.exists())
        self.assertFalse(materialized_parent.exists())

    def test_s3_ttl_cleanup_scans_only_quarantine_prefix(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        old_quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-old",
            kind="source",
            state="quarantine",
            artifact_id="b" * 32,
        )
        fresh_quarantine = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-fresh",
            kind="source",
            state="quarantine",
            artifact_id="c" * 32,
        )
        accepted = store.new_reference(
            owner_id="owner-a",
            demo_id="demo-accepted",
            kind="source",
            state="accepted",
            artifact_id="d" * 32,
        )
        old_metadata = store.write_stream(
            old_quarantine,
            io.BytesIO(b"old"),
            max_bytes=1024,
            now=now - timedelta(hours=2),
        )
        store.write_stream(
            fresh_quarantine,
            io.BytesIO(b"fresh"),
            max_bytes=1024,
            now=now - timedelta(minutes=5),
        )
        store.write_stream(
            accepted,
            io.BytesIO(b"accepted"),
            max_bytes=1024,
            now=now - timedelta(days=1),
        )

        abandoned = list(store.iter_quarantine_before(now - timedelta(hours=1)))
        cleaned = store.cleanup_quarantine_before(now - timedelta(hours=1))

        self.assertEqual(abandoned, [old_metadata])
        self.assertEqual(cleaned, [old_quarantine])
        self.assertIsNone(store.head(old_quarantine))
        self.assertIsNotNone(store.head(fresh_quarantine))
        self.assertIsNotNone(store.head(accepted))

    def test_s3_ttl_cleanup_is_bounded_to_one_maintenance_batch(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        client = _FakeS3Client()
        store = S3ArtifactStore(
            bucket="private-test-bucket",
            prefix="cs2-artifacts-v1",
            client=client,
        )
        for index in range(1001):
            reference = store.new_reference(
                owner_id="owner-a",
                demo_id="demo-a",
                kind="source",
                state="quarantine",
                artifact_id=f"{index:032x}",
            )
            store.write_stream(
                reference,
                io.BytesIO(b"x"),
                max_bytes=1,
                now=now - timedelta(hours=2),
            )

        cleaned = store.cleanup_quarantine_before(now - timedelta(hours=1))

        self.assertEqual(len(cleaned), 1000)
        remaining = list(store.iter_quarantine_before(now - timedelta(hours=1)))
        self.assertEqual(len(remaining), 1)

    def test_factory_selects_provider_without_exposing_backend_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = create_artifact_store(
                backend="local",
                artifact_root=Path(directory),
            )
            object_store = create_artifact_store(
                backend="s3",
                bucket="private-test-bucket",
                prefix="cs2-artifacts-v1",
                client=_FakeS3Client(),
            )

            self.assertIsInstance(local, LocalArtifactStore)
            self.assertIsInstance(object_store, S3ArtifactStore)
            with self.assertRaisesRegex(Exception, "backend"):
                create_artifact_store(backend="unknown")


if __name__ == "__main__":
    unittest.main()
