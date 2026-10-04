"""The operator's `python -m app.cli.delete_data`: the site's own deletion, without a session."""

import io
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker
from test_data_deletion import (
    OTHER_OWNER,
    OWNER,
    FakeRedis,
    fk_engine,
    production_auth_settings,
    put_artifact,
    seed_account,
    seed_demo,
    seed_session,
    seed_upload_session,
)

from app.cli import delete_data
from app.core.config import Settings
from app.models import Account, CoachingFeedback, DeletionTask, Demo, ExternalIdentity, UploadSession
from app.services import deletion_service as deletion_module
from app.services.auth_service import AuthService
from app.services.storage import LocalArtifactStore, UploadStagingStore

STEAM_ID = "76561198000000042"
OTHER_STEAM_ID = "76561198000000043"


class DeleteDataCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = fk_engine()
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = LocalArtifactStore(Path(self.temp_dir.name))
        self.redis = FakeRedis()
        self.auth_service = AuthService(production_auth_settings(), self.redis)
        with self.Session() as db:
            seed_account(db, OWNER, steam_id=STEAM_ID)
            seed_account(db, OTHER_OWNER, steam_id=OTHER_STEAM_ID)
            self.demo_id = str(uuid.uuid4())
            self.reference = put_artifact(self.store, OWNER, self.demo_id)
            seed_demo(db, OWNER, demo_id=self.demo_id, source_key=self.reference)
            self.other_demo = str(uuid.uuid4())
            self.other_reference = put_artifact(self.store, OTHER_OWNER, self.other_demo)
            seed_demo(db, OTHER_OWNER, demo_id=self.other_demo, source_key=self.other_reference)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.engine.dispose()

    def run_cli(self, *argv: str, runtime_settings: Settings | None = None) -> tuple[int, str]:
        out = io.StringIO()
        code = delete_data.main(
            list(argv),
            session_factory=self.Session,
            revoke_sessions=self.auth_service.revoke_owner_sessions,
            artifact_store=self.store,
            runtime_settings=runtime_settings or production_auth_settings(),
            out=out,
        )
        return code, out.getvalue()

    def count(self, model: Any, **filters: Any) -> int:
        with self.Session() as db:
            return int(db.query(model).filter_by(**filters).count())

    def test_without_yes_it_only_shows_what_would_go(self) -> None:
        code, output = self.run_cli("demo", self.demo_id)
        self.assertEqual(code, delete_data.EXIT_INCOMPLETE)
        self.assertIn(f"demo {self.demo_id}: would delete (owner {OWNER}", output)
        self.assertIn("Nothing was deleted", output)
        code, output = self.run_cli("account", "--steam-id", STEAM_ID)
        self.assertEqual(code, delete_data.EXIT_INCOMPLETE)
        self.assertIn(f"would delete account {OWNER} and its 1 match(es)", output)
        self.assertEqual(self.count(Demo), 2)
        self.assertEqual(self.count(Account), 2)
        self.assertIsNotNone(self.store.head(self.reference))

    def test_a_demo_is_deleted_for_its_owner_whoever_that_is(self) -> None:
        code, output = self.run_cli("demo", self.demo_id, "missing-demo", "--yes")

        self.assertEqual(code, delete_data.EXIT_INCOMPLETE, "one id was not found")
        self.assertIn(f"demo {self.demo_id}: deleted", output)
        self.assertIn("demo missing-demo: not found", output)
        self.assertEqual(self.count(Demo, id=self.demo_id), 0)
        self.assertEqual(self.count(CoachingFeedback, demo_id=self.demo_id), 0)
        self.assertEqual(self.count(DeletionTask, demo_id=self.demo_id), 1)
        self.assertIsNone(self.store.head(self.reference))
        self.assertIsNotNone(self.store.head(self.other_reference))
        self.assertEqual(self.count(Account, owner_id=OWNER), 1, "a match delete leaves the account")

    def test_demo_deletion_also_works_for_the_dev_owner(self) -> None:
        code, output = self.run_cli("demo", self.demo_id, "--yes", runtime_settings=Settings(auth_mode="development"))
        self.assertEqual(code, delete_data.EXIT_OK, output)
        self.assertEqual(self.count(Demo, id=self.demo_id), 0)

    def test_an_account_is_deleted_by_steam_id_and_its_sessions_end(self) -> None:
        issued = int(time.time() * 1000) - 1000
        seed_session(self.redis, "owner-session", OWNER, issued_at_ms=issued)
        seed_session(self.redis, "other-session", OTHER_OWNER, issued_at_ms=issued)

        code, output = self.run_cli("account", "--steam-id", STEAM_ID, "--yes")

        self.assertEqual(code, delete_data.EXIT_OK, output)
        self.assertIn(f"steam {STEAM_ID}: deleted account {OWNER}", output)
        self.assertEqual(self.count(Account, owner_id=OWNER), 0)
        self.assertEqual(self.count(ExternalIdentity, owner_id=OWNER), 0)
        self.assertEqual(self.count(Demo, owner_id=OWNER), 0)
        self.assertIsNone(self.store.head(self.reference))
        self.assertIsNone(self.auth_service.resolve_session("owner-session"))
        self.assertEqual(self.auth_service.resolve_session("other-session"), OTHER_OWNER)
        self.assertEqual(self.count(Account, owner_id=OTHER_OWNER), 1)

        again_code, again = self.run_cli("account", OWNER, "--yes")
        self.assertEqual(again_code, delete_data.EXIT_INCOMPLETE)
        self.assertIn(f"account {OWNER}: not found", again)

    def test_an_account_deletion_takes_its_unfinished_uploads_too(self) -> None:
        staging = UploadStagingStore(Path(self.temp_dir.name) / "upload-staging")
        with self.Session() as db:
            owner_upload = seed_upload_session(db, staging, OWNER, parts={0: b"part-zero"})
            other_upload = seed_upload_session(db, staging, OTHER_OWNER, parts={0: b"part-zero"})

        # The CLI builds its DeletionService without a staging store, so it
        # resolves the configured one, as in the api container.
        with patch.object(deletion_module, "upload_staging_store_from_settings", return_value=staging):
            code, output = self.run_cli("account", "--steam-id", STEAM_ID, "--yes")

        self.assertEqual(code, delete_data.EXIT_OK, output)
        self.assertEqual(self.count(UploadSession, owner_id=OWNER), 0)
        self.assertFalse(staging.session_exists(OWNER, owner_upload))
        self.assertEqual(self.count(UploadSession, owner_id=OTHER_OWNER), 1)
        self.assertEqual(staging.list_parts(OTHER_OWNER, other_upload), {0: len(b"part-zero")})

    def test_account_deletion_is_refused_outside_production(self) -> None:
        code, output = self.run_cli("account", OWNER, "--yes", runtime_settings=Settings(auth_mode="development"))
        self.assertEqual(code, delete_data.EXIT_USAGE)
        self.assertIn("AUTH_MODE=production", output)
        self.assertEqual(self.count(Account, owner_id=OWNER), 1)
        self.assertEqual(self.count(Demo, owner_id=OWNER), 1)


if __name__ == "__main__":
    unittest.main()
