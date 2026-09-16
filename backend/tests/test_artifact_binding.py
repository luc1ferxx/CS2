from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.artifact_binding import (
    ACCEPTED_ARTIFACT_POLICY_VERSION,
    AcceptedArtifactError,
    AcceptedArtifactSnapshot,
    head_accepted_video,
    materialize_accepted_demo,
    open_accepted_video_range,
    parse_source_artifact_snapshot,
    read_accepted_replay_json,
    verify_accepted_artifact,
)
from app.services.storage import ArtifactMetadata, ArtifactReference, LocalArtifactStore

NOW = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)
OWNER_ID = "owner-a"
DEMO_ID = "demo-a"
ARTIFACT_ID = "a" * 32


def accepted_reference(kind: str = "source") -> str:
    return ArtifactReference(
        owner_id=OWNER_ID,
        demo_id=DEMO_ID,
        kind=kind,
        state="accepted",
        artifact_id=ARTIFACT_ID,
    ).to_uri()


def metadata_for(
    *,
    kind: str = "source",
    body: bytes = b"0123456789abcdef",
    generation: str = "version:accepted-v1",
) -> ArtifactMetadata:
    return ArtifactMetadata(
        reference=accepted_reference(kind),
        owner_id=OWNER_ID,
        demo_id=DEMO_ID,
        kind=kind,
        state="accepted",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        generation=generation,
        created_at=NOW,
        content_type=("application/json" if kind == "replay" else None),
    )


def snapshot_for(metadata: ArtifactMetadata) -> dict[str, Any]:
    return {
        "policyVersion": ACCEPTED_ARTIFACT_POLICY_VERSION,
        "state": "accepted",
        "reference": metadata.reference,
        "sizeBytes": metadata.size_bytes,
        "sha256": metadata.sha256,
        "acceptedAt": "2026-07-16T08:30:00Z",
        "generation": metadata.generation,
    }


class FakeArtifactRead:
    def __init__(
        self,
        metadata: ArtifactMetadata,
        body: bytes,
        *,
        start: int,
        end_inclusive: int,
    ):
        self.metadata = metadata
        self.start = start
        self.end_inclusive = end_inclusive
        self.total_size = metadata.size_bytes
        self.length = max(0, end_inclusive - start + 1)
        self.body = body[start : end_inclusive + 1]
        self.closed = False

    def iter_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeArtifactRead:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class FakeArtifactStore:
    def __init__(self, metadata: ArtifactMetadata | Any | None, body: bytes = b""):
        self.metadata = metadata
        self.body = body
        self.require_calls: list[dict[str, Any]] = []
        self.read_calls: list[dict[str, Any]] = []
        self.materialize_calls: list[dict[str, Any]] = []
        self.last_read: FakeArtifactRead | None = None
        self.head_error: Exception | None = None
        self.read_error: Exception | None = None
        self.materialize_error: Exception | None = None
        self.cleanup_observed = False

    def require_binding(
        self,
        reference: str,
        *,
        owner_id: str,
        demo_id: str,
        kind: str | None = None,
        state: str | None = None,
    ) -> ArtifactReference:
        self.require_calls.append(
            {
                "reference": reference,
                "owner_id": owner_id,
                "demo_id": demo_id,
                "kind": kind,
                "state": state,
            }
        )
        parsed = ArtifactReference.parse(reference)
        if (
            parsed.owner_id != owner_id
            or parsed.demo_id != demo_id
            or (kind is not None and parsed.kind != kind)
            or (state is not None and parsed.state != state)
        ):
            raise RuntimeError("secret object key: private/source.dem")
        return parsed

    def head(self, reference: str) -> ArtifactMetadata | Any | None:
        if self.head_error is not None:
            raise self.head_error
        return self.metadata

    def read_range(
        self,
        reference: str,
        *,
        start: int = 0,
        end_inclusive: int | None = None,
        expected_generation: str | None = None,
    ) -> FakeArtifactRead:
        self.read_calls.append(
            {
                "reference": reference,
                "start": start,
                "end_inclusive": end_inclusive,
                "expected_generation": expected_generation,
            }
        )
        if self.read_error is not None:
            raise self.read_error
        assert self.metadata is not None
        normalized_end = len(self.body) - 1 if end_inclusive is None else end_inclusive
        self.last_read = FakeArtifactRead(
            self.metadata,
            self.body,
            start=start,
            end_inclusive=normalized_end,
        )
        return self.last_read

    @contextmanager
    def materialize(self, reference: str, **kwargs: Any) -> Iterator[Path]:
        self.materialize_calls.append({"reference": reference, **kwargs})
        if self.materialize_error is not None:
            raise self.materialize_error
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.dem"
            path.write_bytes(self.body)
            try:
                yield path
            finally:
                path.unlink(missing_ok=True)
                self.cleanup_observed = True


class AcceptedArtifactSnapshotTest(unittest.TestCase):
    def test_parse_normalizes_only_the_compact_v1_fields(self) -> None:
        metadata = metadata_for()

        parsed = parse_source_artifact_snapshot(snapshot_for(metadata))

        self.assertEqual(
            parsed.as_dict(),
            {
                "policyVersion": "artifact_intake_v1",
                "state": "accepted",
                "reference": metadata.reference,
                "sizeBytes": 16,
                "sha256": metadata.sha256,
                "acceptedAt": "2026-07-16T08:30:00Z",
                "generation": "version:accepted-v1",
            },
        )

    def test_parse_rejects_missing_extra_or_malformed_fields_without_echo(self) -> None:
        valid = snapshot_for(metadata_for())
        invalid: list[Mapping[str, Any] | Any] = [
            None,
            "secret://bucket/key",
            {key: value for key, value in valid.items() if key != "generation"},
            {**valid, "providerKey": "secret://bucket/key"},
            {**valid, "policyVersion": "artifact_intake_v2"},
            {**valid, "state": "quarantine"},
            {**valid, "reference": "secret://bucket/key"},
            {**valid, "sizeBytes": True},
            {**valid, "sizeBytes": -1},
            {**valid, "sha256": "SECRET-DIGEST"},
            {**valid, "acceptedAt": "2026-07-16T08:30:00"},
            {**valid, "generation": ""},
        ]

        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(AcceptedArtifactError) as raised:
                    parse_source_artifact_snapshot(payload)
                self.assertEqual(raised.exception.code, "ARTIFACT_SNAPSHOT_INVALID")
                rendered = str(raised.exception)
                self.assertNotIn("secret", rendered.lower())
                self.assertNotIn("bucket", rendered.lower())
                self.assertNotIn("key", rendered.lower())


class AcceptedArtifactVerificationTest(unittest.TestCase):
    def test_verify_binds_reference_head_and_snapshot(self) -> None:
        metadata = metadata_for()
        store = FakeArtifactStore(metadata)

        verified = verify_accepted_artifact(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            kind="source",
            snapshot=snapshot_for(metadata),
            max_bytes=1024,
        )

        self.assertEqual(verified.metadata, metadata)
        self.assertEqual(verified.snapshot, AcceptedArtifactSnapshot.from_metadata(metadata))
        self.assertEqual(
            store.require_calls,
            [
                {
                    "reference": metadata.reference,
                    "owner_id": OWNER_ID,
                    "demo_id": DEMO_ID,
                    "kind": "source",
                    "state": "accepted",
                }
            ],
        )

    def test_verify_without_snapshot_pins_the_current_immutable_head(self) -> None:
        metadata = metadata_for(kind="video", body=b"video-bytes")
        store = FakeArtifactStore(metadata)

        verified = verify_accepted_artifact(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            kind="video",
        )

        self.assertEqual(verified.snapshot, AcceptedArtifactSnapshot.from_metadata(metadata))

    def test_verify_fails_closed_for_binding_missing_or_store_errors(self) -> None:
        metadata = metadata_for()
        wrong_kind_reference = accepted_reference("video")
        wrong_state_reference = ArtifactReference(
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            kind="source",
            state="quarantine",
            artifact_id=ARTIFACT_ID,
        ).to_uri()
        cases: list[tuple[str, FakeArtifactStore, str, str, str]] = [
            ("malformed", FakeArtifactStore(metadata), "secret://bucket/key", OWNER_ID, DEMO_ID),
            ("cross owner", FakeArtifactStore(metadata), metadata.reference, "owner-b", DEMO_ID),
            ("cross demo", FakeArtifactStore(metadata), metadata.reference, OWNER_ID, "demo-b"),
            ("wrong kind", FakeArtifactStore(metadata), wrong_kind_reference, OWNER_ID, DEMO_ID),
            ("wrong state", FakeArtifactStore(metadata), wrong_state_reference, OWNER_ID, DEMO_ID),
            ("missing", FakeArtifactStore(None), metadata.reference, OWNER_ID, DEMO_ID),
        ]
        failed_store = FakeArtifactStore(metadata)
        failed_store.head_error = RuntimeError("secret endpoint https://storage.invalid/key")
        cases.append(("head failure", failed_store, metadata.reference, OWNER_ID, DEMO_ID))

        for label, store, reference, owner_id, demo_id in cases:
            with self.subTest(label=label):
                with self.assertRaises(AcceptedArtifactError) as raised:
                    verify_accepted_artifact(
                        store,
                        reference,
                        owner_id=owner_id,
                        demo_id=demo_id,
                        kind="source",
                        snapshot=snapshot_for(metadata),
                    )
                self.assertIn(
                    raised.exception.code,
                    {"ARTIFACT_BINDING_INVALID", "ARTIFACT_NOT_AVAILABLE"},
                )
                self.assertNotIn("secret", str(raised.exception).lower())
                self.assertNotIn("storage.invalid", str(raised.exception).lower())

    def test_verify_revalidates_a_snapshot_dataclass_instead_of_trusting_it(self) -> None:
        metadata = metadata_for()
        invalid = replace(
            AcceptedArtifactSnapshot.from_metadata(metadata),
            policy_version="artifact_intake_v2",
        )

        with self.assertRaises(AcceptedArtifactError) as raised:
            verify_accepted_artifact(
                FakeArtifactStore(metadata),
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                kind="source",
                snapshot=invalid,
            )

        self.assertEqual(raised.exception.code, "ARTIFACT_SNAPSHOT_INVALID")

    def test_verify_rejects_snapshot_and_head_drift(self) -> None:
        metadata = metadata_for()
        base_snapshot = snapshot_for(metadata)
        drifted_snapshots = [
            {**base_snapshot, "reference": accepted_reference("video")},
            {**base_snapshot, "sizeBytes": metadata.size_bytes + 1},
            {**base_snapshot, "sha256": "b" * 64},
            {**base_snapshot, "generation": "etag:replacement"},
            {**base_snapshot, "acceptedAt": "2026-07-16T08:31:00Z"},
        ]
        for snapshot in drifted_snapshots:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(AcceptedArtifactError) as raised:
                    verify_accepted_artifact(
                        FakeArtifactStore(metadata),
                        metadata.reference,
                        owner_id=OWNER_ID,
                        demo_id=DEMO_ID,
                        kind="source",
                        snapshot=snapshot,
                    )
                self.assertEqual(raised.exception.code, "ARTIFACT_INTEGRITY_FAILED")

        @dataclass(frozen=True)
        class DriftedHead:
            reference: str = metadata.reference
            owner_id: str = "owner-b"
            demo_id: str = DEMO_ID
            kind: str = "source"
            state: str = "accepted"
            size_bytes: int = metadata.size_bytes
            sha256: str = metadata.sha256
            generation: str = metadata.generation
            created_at: datetime = NOW
            content_type: str | None = None

        with self.assertRaises(AcceptedArtifactError) as raised:
            verify_accepted_artifact(
                FakeArtifactStore(DriftedHead()),
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                kind="source",
                snapshot=base_snapshot,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_INTEGRITY_FAILED")

    def test_verify_rejects_an_oversized_head_before_opening_bytes(self) -> None:
        metadata = metadata_for()
        store = FakeArtifactStore(metadata)

        with self.assertRaises(AcceptedArtifactError) as raised:
            verify_accepted_artifact(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                kind="source",
                max_bytes=metadata.size_bytes - 1,
            )

        self.assertEqual(raised.exception.code, "ARTIFACT_TOO_LARGE")
        self.assertEqual(store.read_calls, [])


class AcceptedReplayReadTest(unittest.TestCase):
    def test_replay_read_is_head_first_generation_conditional_and_bounded(self) -> None:
        body = json.dumps({"demoId": DEMO_ID, "frames": []}).encode("utf-8")
        metadata = metadata_for(kind="replay", body=body)
        store = FakeArtifactStore(metadata, body)

        payload = read_accepted_replay_json(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            snapshot=snapshot_for(metadata),
            max_bytes=1024,
            chunk_size=7,
        )

        self.assertEqual(payload, {"demoId": DEMO_ID, "frames": []})
        self.assertEqual(
            store.read_calls,
            [
                {
                    "reference": metadata.reference,
                    "start": 0,
                    "end_inclusive": metadata.size_bytes - 1,
                    "expected_generation": metadata.generation,
                }
            ],
        )
        self.assertTrue(store.last_read and store.last_read.closed)

    def test_replay_read_rejects_malformed_wrong_demo_or_changed_bytes(self) -> None:
        bodies = [
            b"[]",
            b'{"demoId":"demo-b"}',
            b'{"demoId":"demo-a"',
            b'{"demoId":"demo-a","value":NaN}',
        ]
        for body in bodies:
            with self.subTest(body=body):
                metadata = metadata_for(kind="replay", body=body)
                with self.assertRaises(AcceptedArtifactError) as raised:
                    read_accepted_replay_json(
                        FakeArtifactStore(metadata, body),
                        metadata.reference,
                        owner_id=OWNER_ID,
                        demo_id=DEMO_ID,
                        max_bytes=1024,
                    )
                self.assertEqual(raised.exception.code, "ARTIFACT_CONTENT_INVALID")

        original = json.dumps({"demoId": DEMO_ID}).encode("utf-8")
        changed = json.dumps({"demoId": DEMO_ID, "changed": True}).encode("utf-8")
        metadata = metadata_for(kind="replay", body=original)
        with self.assertRaises(AcceptedArtifactError) as raised:
            read_accepted_replay_json(
                FakeArtifactStore(metadata, changed),
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                max_bytes=1024,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_INTEGRITY_FAILED")

    def test_replay_read_rejects_an_oversized_head_without_reading(self) -> None:
        body = json.dumps({"demoId": DEMO_ID}).encode("utf-8")
        metadata = metadata_for(kind="replay", body=body)
        store = FakeArtifactStore(metadata, body)

        with self.assertRaises(AcceptedArtifactError) as raised:
            read_accepted_replay_json(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                max_bytes=metadata.size_bytes - 1,
            )

        self.assertEqual(raised.exception.code, "ARTIFACT_TOO_LARGE")
        self.assertEqual(store.read_calls, [])


class AcceptedVideoReadTest(unittest.TestCase):
    def test_video_head_and_range_share_the_pinned_generation(self) -> None:
        body = b"0123456789"
        metadata = metadata_for(kind="video", body=body)
        store = FakeArtifactStore(metadata, body)

        verified = head_accepted_video(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            max_bytes=1024,
        )
        opened = open_accepted_video_range(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            start=2,
            end_inclusive=5,
            verified=verified,
            max_bytes=1024,
        )

        self.assertEqual(b"".join(opened.iter_chunks()), b"2345")
        self.assertEqual(store.read_calls[-1]["expected_generation"], metadata.generation)
        opened.close()
        self.assertTrue(opened.closed)

    def test_video_range_rejects_invalid_bounds_or_replaced_open_metadata(self) -> None:
        body = b"0123456789"
        metadata = metadata_for(kind="video", body=body)
        store = FakeArtifactStore(metadata, body)
        with self.assertRaises(AcceptedArtifactError) as raised:
            open_accepted_video_range(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                start=9,
                end_inclusive=2,
                max_bytes=1024,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_RANGE_INVALID")
        self.assertEqual(store.read_calls, [])

        verified = head_accepted_video(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
        )
        store.metadata = replace(metadata, generation="etag:replacement")
        with self.assertRaises(AcceptedArtifactError) as raised:
            open_accepted_video_range(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                start=0,
                end_inclusive=1,
                verified=verified,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_INTEGRITY_FAILED")
        self.assertTrue(store.last_read and store.last_read.closed)


class AcceptedDemoMaterializationTest(unittest.TestCase):
    def test_binding_and_materialization_run_against_the_local_store_contract(self) -> None:
        body = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            quarantine = store.new_reference(
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                kind="source",
                state="quarantine",
                artifact_id=ARTIFACT_ID,
            )
            quarantined = store.write_stream(
                quarantine,
                io.BytesIO(body),
                max_bytes=1024,
                now=NOW,
            )
            accepted = store.promote(
                quarantine,
                expected_generation=quarantined.generation,
                expected_size=quarantined.size_bytes,
                expected_sha256=quarantined.sha256,
                now=NOW,
            )

            with materialize_accepted_demo(
                store,
                accepted.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                snapshot=AcceptedArtifactSnapshot.from_metadata(accepted),
                max_bytes=1024,
            ) as path:
                self.assertEqual(path.read_bytes(), body)

            self.assertFalse(path.exists())

    def test_materialization_is_bounded_pinned_and_cleaned_after_success(self) -> None:
        body = b"0123456789abcdef"
        metadata = metadata_for(body=body)
        store = FakeArtifactStore(metadata, body)

        with materialize_accepted_demo(
            store,
            metadata.reference,
            owner_id=OWNER_ID,
            demo_id=DEMO_ID,
            snapshot=snapshot_for(metadata),
            max_bytes=1024,
        ) as path:
            self.assertEqual(path.suffix, ".dem")
            self.assertEqual(path.read_bytes(), body)

        self.assertTrue(store.cleanup_observed)
        self.assertFalse(path.exists())
        self.assertEqual(
            store.materialize_calls,
            [
                {
                    "reference": metadata.reference,
                    "expected_generation": metadata.generation,
                    "expected_size": len(body),
                    "expected_sha256": metadata.sha256,
                    "max_bytes": 1024,
                    "suffix": ".dem",
                }
            ],
        )

    def test_materialization_cleans_on_consumer_failure_and_keeps_error(self) -> None:
        body = b"0123456789abcdef"
        metadata = metadata_for(body=body)
        store = FakeArtifactStore(metadata, body)

        with self.assertRaisesRegex(ValueError, "parser failed"):
            with materialize_accepted_demo(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                snapshot=snapshot_for(metadata),
                max_bytes=1024,
            ):
                raise ValueError("parser failed")

        self.assertTrue(store.cleanup_observed)

    def test_materialization_wraps_store_failure_without_provider_details(self) -> None:
        metadata = metadata_for()
        store = FakeArtifactStore(metadata, b"0123456789abcdef")
        store.materialize_error = RuntimeError(
            "secret bucket https://provider.invalid/private/key"
        )

        with self.assertRaises(AcceptedArtifactError) as raised:
            with materialize_accepted_demo(
                store,
                metadata.reference,
                owner_id=OWNER_ID,
                demo_id=DEMO_ID,
                snapshot=snapshot_for(metadata),
                max_bytes=1024,
            ):
                self.fail("store failure must prevent yielding")

        self.assertEqual(raised.exception.code, "ARTIFACT_NOT_AVAILABLE")
        self.assertNotIn("secret", str(raised.exception).lower())
        self.assertNotIn("provider.invalid", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
