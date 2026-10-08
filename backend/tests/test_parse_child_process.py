"""The worker -> parse child boundary, exercised with real child processes.

Every other worker test patches run_parse_subprocess or Popen, so nothing else
checks the argv contract between worker.py / match_summary_backfill.py and
parse_child.main, the child's import chain, its environment allow-list, the
unprivileged account it is switched to, or how its exit is classified and
measured. A round trip costs about 0.1 s.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fixtures.demo_jobs import add_demo_with_job, bind_source_artifact
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import DemoJob
from app.parser.demo_parser import DemoParserError
from app.services.demo_service import DemoService
from app.services.storage import LocalArtifactStore
from app.workers import child_process, worker
from app.workers.child_process import (
    CHILD_ENV_ALLOWLIST,
    WINDOWS_ENV_ALLOWLIST,
    ChildIdentity,
    ChildUsage,
    child_environment,
    resolve_child_identity,
    share_private_source,
    wait_for_exit,
)
from app.workers.match_summary_backfill import run_team_names_subprocess
from app.workers.parse_child import EXIT_PARSE_ERROR
from app.workers.worker import (
    JOB_DONE_FIELDS,
    PACKAGE_ROOT,
    _parse_child_result,
    run_parse_subprocess,
)

POSIX = sys.platform != "win32"
SECRET_ENVIRONMENT = {
    "DATABASE_URL": "postgresql+psycopg2://cs2coach:db-secret@postgres:5432/cs2coach",
    "REDIS_URL": "redis://:redis-secret@redis:6379/0",
    "OBJECT_STORAGE_ACCESS_KEY_ID": "access-key",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY": "storage-secret",
    "RENDER_WORKER_TOKEN": "render-secret",
    "STEAM_WEB_API_KEY": "steam-secret",
    "STEAM_CREDENTIAL_ENCRYPTION_KEY": "steam-key",
    "AUTH_SESSION_COOKIE_NAME": "__Host-cs2_session",
    "OIDC_CLIENT_SECRET": "oidc-secret",
}
SECRET_PREFIXES = ("OBJECT_STORAGE_", "DATABASE_", "RENDER_", "STEAM_", "REDIS_", "AUTH_", "OIDC_")


def scratch_file(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def use_child_user(test: unittest.TestCase, name: str) -> None:
    original = settings.parse_child_user
    test.addCleanup(object.__setattr__, settings, "parse_child_user", original)
    object.__setattr__(settings, "parse_child_user", name)


def private_copy(root: Path, content: bytes) -> Path:
    """A source shaped like storage materialize(): a 0600 file in its own 0700 directory."""
    directory = Path(tempfile.mkdtemp(prefix="cs2-artifact-", dir=root))
    os.chmod(directory, 0o700)
    source = scratch_file(directory, "artifact.dem", content)
    os.chmod(source, 0o600)
    return source


def parser_account_exists() -> bool:
    if not POSIX:
        return False
    import pwd

    try:
        pwd.getpwnam("parser")
    except KeyError:
        return False
    return True


class RealParseChildTest(unittest.TestCase):
    """run_parse_subprocess against the real parse child, Popen unpatched."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        # Same user as the test run; RootWorkerDropTest covers the switch.
        use_child_user(self, "")

    def test_garbage_gets_the_childs_own_classification_not_parser_unexpected(self) -> None:
        source = scratch_file(self.directory, "garbage.dem", b"0123456789abcdef")  # 16 bytes
        stats: dict[str, Any] = {}

        with self.assertRaises(DemoParserError) as raised:
            run_parse_subprocess(source, stats=stats)

        # demoparser2 opens 16 bytes of noise but finds nothing to sample: the
        # child's classification crosses the process boundary intact.
        self.assertEqual(raised.exception.error_code, "MISSING_MATCH_METADATA")
        self.assertIsInstance(stats["parseS"], float)
        if POSIX:
            # os.wait4 reaped the child: an interpreter plus demoparser2 is tens of MiB.
            self.assertGreater(stats["peakRssMiB"], 10)
        else:
            self.assertIsNone(stats["peakRssMiB"])

    def test_a_four_byte_file_is_an_invalid_demo(self) -> None:
        source = scratch_file(self.directory, "tiny.dem", b"abcd")

        with self.assertRaises(DemoParserError) as raised:
            run_parse_subprocess(source)

        self.assertEqual(raised.exception.error_code, "INVALID_DEMO")
        self.assertEqual(raised.exception.user_message, "Invalid or unreadable demo file.")

    def test_team_names_mode_takes_the_exit_3_path_and_yields_no_names(self) -> None:
        source = scratch_file(self.directory, "tiny.dem", b"abcd")
        started: list[Any] = []

        def spawn(argv: list[str], **kwargs: Any) -> Any:
            started.append((argv, child_process.spawn_child(argv, **kwargs)))
            return started[-1][1]

        with patch("app.workers.match_summary_backfill.spawn_child", side_effect=spawn):
            names = run_team_names_subprocess(source, [64, 1])

        self.assertEqual(names, {})
        argv, process = started[0]
        self.assertEqual(argv[argv.index("--team-names-ticks") + 1], "64,1")
        self.assertEqual(process.returncode, EXIT_PARSE_ERROR)

    def test_team_names_mode_reads_names_when_the_demo_opens(self) -> None:
        source = scratch_file(self.directory, "garbage.dem", b"0123456789abcdef")
        self.assertEqual(run_team_names_subprocess(source, [1]), {})

    def test_the_child_import_chain_has_no_database_or_redis(self) -> None:
        # The child is started fresh for every parse and gets no credentials;
        # importing the database or Redis layer would be both slow and a sign
        # that something on the parser side started reading settings.
        probe = (
            "import sys, app.workers.parse_child; "
            "print(','.join(m for m in ('sqlalchemy', 'redis', 'app.core.database', 'app.core.redis') "
            "if m in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            env=child_environment(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    @unittest.skipUnless(POSIX, "SIGABRT and os.wait4 are POSIX")
    def test_an_abort_after_a_rust_allocation_failure_is_out_of_memory(self) -> None:
        # What RLIMIT_DATA does to demoparser2: the Rust allocator prints this
        # line and aborts. The tail of the child's stderr carries it to the parent.
        module = scratch_file(
            self.directory,
            "abort_child.py",
            textwrap.dedent(
                """
                import os, sys
                sys.stderr.write("memory allocation of 4294967296 bytes failed\\n")
                sys.stderr.flush()
                os.abort()
                """
            ).encode(),
        )
        environment = {**os.environ, "PYTHONPATH": str(module.parent)}
        log = io.StringIO()
        stats: dict[str, Any] = {}
        with patch.object(worker, "PARSE_CHILD_MODULE", "abort_child"), patch.dict(
            os.environ, environment, clear=True,
        ), contextlib.redirect_stdout(log):
            with self.assertRaises(DemoParserError) as raised:
                run_parse_subprocess(self.directory / "source.dem", stats=stats)

        self.assertEqual(raised.exception.error_code, "PARSE_OUT_OF_MEMORY")
        self.assertNotIn("4294967296", raised.exception.user_message)
        # The tail is replayed into the worker log, and only there.
        self.assertIn("memory allocation of 4294967296 bytes failed", log.getvalue())
        self.assertIsNotNone(stats["peakRssMiB"])

    @unittest.skipUnless(POSIX, "os.wait4 is POSIX")
    def test_wait4_keeps_popens_return_code_for_a_signal_death(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"]
        )
        usage = ChildUsage()
        try:
            self.assertTrue(wait_for_exit(process, 30, usage))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        # Popen would read the already-reaped child as a clean exit 0.
        self.assertEqual(process.returncode, -signal.SIGSEGV)
        self.assertEqual(process.poll(), -signal.SIGSEGV)
        self.assertIsNotNone(usage.peak_rss_kib)

    def test_wait_for_exit_times_out_on_a_child_that_keeps_running(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertFalse(wait_for_exit(process, 0.2, ChildUsage()))
            self.assertIsNone(process.poll())
        finally:
            process.kill()
            wait_for_exit(process, 30, ChildUsage())



@unittest.skipUnless(
    POSIX and os.geteuid() == 0 and parser_account_exists(),
    "needs a root worker and the image's parser account",
)
class RootWorkerDropTest(unittest.TestCase):
    """The real switch, as the worker container runs it (root worker, `parser` account)."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        os.chmod(self.root, 0o755)
        use_child_user(self, "parser")

    def test_the_child_runs_unprivileged_with_the_allow_list_only(self) -> None:
        probe = scratch_file(
            self.root,
            "probe_child.py",
            textwrap.dedent(
                """
                import argparse, json, os
                parser = argparse.ArgumentParser()
                for name in ("--source", "--output", "--memory-limit-bytes"):
                    parser.add_argument(name)
                args = parser.parse_args()
                size = len(open(args.source, "rb").read())
                parsed = {"uid": os.getuid(), "groups": os.getgroups(), "env": sorted(os.environ), "size": size}
                json.dump({"ok": True, "parsed": parsed}, open(args.output, "w"))
                """
            ).encode(),
        )
        os.chmod(probe, 0o644)
        with patch.object(worker, "PARSE_CHILD_MODULE", "probe_child"), patch.dict(
            os.environ, {**SECRET_ENVIRONMENT, "PYTHONPATH": str(self.root)},
        ):
            seen = run_parse_subprocess(private_copy(self.root, b"x" * 32))

        self.assertNotEqual(seen["uid"], 0)
        self.assertEqual(seen["groups"], [])
        self.assertLessEqual(set(seen["env"]), set(CHILD_ENV_ALLOWLIST))
        self.assertEqual(seen["size"], 32)

    def test_the_real_parse_child_reads_a_private_copy_as_the_account(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            run_parse_subprocess(private_copy(self.root, b"0123456789abcdef"))
        # Not STORAGE_READ_FAILED: the account could open the source and load demoparser2.
        self.assertEqual(raised.exception.error_code, "MISSING_MATCH_METADATA")


class ExitClassificationTest(unittest.TestCase):
    def test_sigabrt_with_the_rust_allocation_message_is_out_of_memory(self) -> None:
        tail = "thread '<unnamed>' panicked\nmemory allocation of 1073741824 bytes failed\n"
        with self.assertRaises(DemoParserError) as raised:
            _parse_child_result(-6, Path("missing.json"), tail)
        self.assertEqual(raised.exception.error_code, "PARSE_OUT_OF_MEMORY")

    def test_any_other_sigabrt_is_still_a_crash(self) -> None:
        for tail in ("", "fatal runtime error: something else\n"):
            with self.subTest(tail=tail), self.assertRaises(DemoParserError) as raised:
                _parse_child_result(-6, Path("missing.json"), tail)
            self.assertEqual(raised.exception.error_code, "PARSER_CRASHED")

    def test_the_allocation_message_alone_does_not_make_a_clean_exit_a_memory_failure(self) -> None:
        with self.assertRaises(DemoParserError) as raised:
            _parse_child_result(7, Path("missing.json"), "memory allocation of 1 bytes failed")
        self.assertEqual(raised.exception.error_code, "PARSER_UNEXPECTED")

    @unittest.skipUnless(POSIX, "symlink creation needs privileges on Windows")
    def test_output_planted_as_a_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "elsewhere.json"
            target.write_text(json.dumps({"ok": True, "parsed": {"leaked": True}}), encoding="utf-8")
            planted = Path(directory) / "parsed.json"
            planted.symlink_to(target)

            with self.assertRaises(DemoParserError) as raised:
                _parse_child_result(0, planted)

        self.assertEqual(raised.exception.error_code, "PARSER_UNEXPECTED")


class FakeChild:
    """Records how it was started and exits at once with EXIT_PARSE_ERROR."""

    def __init__(self) -> None:
        self.argv: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.returncode: int | None = None
        self.pid = 4242

    def open(self, argv: list[str], **kwargs: Any) -> FakeChild:
        self.argv = list(argv)
        self.kwargs = kwargs
        return self

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = EXIT_PARSE_ERROR
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class ChildEnvironmentTest(unittest.TestCase):
    """No credential the worker holds may reach the process reading untrusted bytes."""

    def setUp(self) -> None:
        use_child_user(self, "")

    def start_both_children(self) -> list[FakeChild]:
        children: list[FakeChild] = []

        def popen(argv: list[str], **kwargs: Any) -> FakeChild:
            child = FakeChild()
            children.append(child)
            return child.open(argv, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            source = scratch_file(Path(directory), "source.dem", b"0123456789abcdef")
            with patch.dict(os.environ, SECRET_ENVIRONMENT), patch(
                "app.workers.child_process.subprocess.Popen", side_effect=popen,
            ):
                with self.assertRaises(DemoParserError):
                    run_parse_subprocess(source)
                self.assertEqual(run_team_names_subprocess(source, [1]), {})
        self.assertEqual(len(children), 2)
        return children

    def test_only_the_allow_list_reaches_popen(self) -> None:
        allowed = set(CHILD_ENV_ALLOWLIST) | (set(WINDOWS_ENV_ALLOWLIST) if not POSIX else set())
        for child in self.start_both_children():
            environment = child.kwargs["env"]
            with self.subTest(argv=child.argv[3:4]):
                self.assertLessEqual(set(environment), allowed)
                for name in environment:
                    self.assertFalse(name.startswith(SECRET_PREFIXES), name)
                for secret in SECRET_ENVIRONMENT.values():
                    self.assertNotIn(secret, json.dumps(environment))
                self.assertTrue(environment["PYTHONPATH"].startswith(PACKAGE_ROOT))
                self.assertEqual(environment["OPENBLAS_NUM_THREADS"], "1")

    def test_an_operator_openblas_setting_is_kept(self) -> None:
        environment = child_environment(PACKAGE_ROOT, base={"OPENBLAS_NUM_THREADS": "4", "PATH": "/usr/bin"})
        self.assertEqual(environment["OPENBLAS_NUM_THREADS"], "4")
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_without_a_child_account_nothing_switches_user(self) -> None:
        for child in self.start_both_children():
            self.assertNotIn("user", child.kwargs)
            self.assertNotIn("group", child.kwargs)


class ChildIdentityTest(unittest.TestCase):
    def test_empty_or_non_root_runs_the_child_as_the_worker(self) -> None:
        self.assertIsNone(resolve_child_identity(""))
        if not POSIX or os.geteuid() != 0:
            self.assertIsNone(resolve_child_identity("parser"))

    @unittest.skipUnless(POSIX, "users and os.geteuid are POSIX")
    def test_a_root_worker_resolves_the_account_or_refuses_to_start(self) -> None:
        import pwd

        entry = SimpleNamespace(pw_uid=4321, pw_gid=4322)
        with patch.object(child_process.os, "geteuid", return_value=0):
            with patch.object(pwd, "getpwnam", return_value=entry):
                self.assertEqual(
                    resolve_child_identity("parser"),
                    ChildIdentity(name="parser", uid=4321, gid=4322),
                )
            with patch.object(pwd, "getpwnam", side_effect=KeyError("parser")):
                with self.assertRaisesRegex(RuntimeError, "PARSE_CHILD_USER"):
                    resolve_child_identity("parser")
            with patch.object(pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0, pw_gid=0)):
                with self.assertRaisesRegex(RuntimeError, "not root"):
                    resolve_child_identity("root")

    @unittest.skipUnless(POSIX, "users and os.geteuid are POSIX")
    def test_a_root_worker_starts_the_child_as_the_account_without_extra_groups(self) -> None:
        identity = ChildIdentity(name="parser", uid=4321, gid=4322)
        child = FakeChild()
        chown = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            source = scratch_file(Path(directory), "source.dem", b"0123456789abcdef")
            with patch.object(worker, "resolve_child_identity", return_value=identity), patch.object(
                worker, "share_private_source",
            ) as share, patch.object(child_process.os, "chown", chown), patch(
                "app.workers.child_process.subprocess.Popen", side_effect=child.open,
            ):
                with self.assertRaises(DemoParserError):
                    run_parse_subprocess(source)

        self.assertEqual(child.kwargs["user"], 4321)
        self.assertEqual(child.kwargs["group"], 4322)
        self.assertEqual(child.kwargs["extra_groups"], [])
        workspace = Path(child.argv[child.argv.index("--output") + 1]).parent
        chown.assert_any_call(workspace, 4321, 4322)
        # HOME points somewhere the account can actually use, not /root.
        self.assertEqual(child.kwargs["env"]["HOME"], str(workspace))
        share.assert_called_once_with(source, identity)

    @unittest.skipUnless(POSIX, "file modes are POSIX")
    def test_a_private_materialized_copy_is_opened_to_the_childs_group_only(self) -> None:
        identity = ChildIdentity(name="parser", uid=os.getuid(), gid=os.getgid())
        with tempfile.TemporaryDirectory() as scratch:
            private = Path(scratch) / "cs2-artifact-x"
            private.mkdir(mode=0o700)
            source = scratch_file(private, "artifact.dem", b"0123456789abcdef")
            os.chmod(source, 0o600)

            share_private_source(source, identity)

            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o710)
            self.assertEqual(source.stat().st_gid, identity.gid)

    @unittest.skipUnless(POSIX, "file modes are POSIX")
    def test_anything_but_a_private_copy_is_left_alone(self) -> None:
        identity = ChildIdentity(name="parser", uid=os.getuid(), gid=os.getgid())
        with tempfile.TemporaryDirectory() as scratch:
            shared = Path(scratch) / "shared"
            shared.mkdir(mode=0o755)
            os.chmod(shared, 0o755)
            source = scratch_file(shared, "sample.dem", b"0123456789abcdef")
            os.chmod(source, 0o644)

            share_private_source(source, identity)

            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o755)


def fk_engine(path: Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


def parsed_match() -> dict[str, Any]:
    return {
        "mapName": "de_nuke",
        "tickRate": 64,
        "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 0, "endTick": 128}],
        "players": [{"id": "p1", "name": "p1", "side": "T"}],
        "frames": [
            {
                "tick": 0,
                "players": [
                    {"id": "p1", "name": "p1", "side": "T", "x": 100, "y": 200, "z": 0, "hp": 100, "alive": True}
                ],
            }
        ],
        "events": [],
        "kills": [],
        "deaths": [],
    }


class _StopWorker(BaseException):
    """Ends run_worker's loop once the scripted messages are used up."""


class OneMessageQueue:
    consumer_id = "job-done-test"

    def __init__(self, payload: str) -> None:
        self.payloads = [payload]

    def register(self) -> None: ...

    def unregister(self) -> None: ...

    def renew_lease(self) -> None: ...

    def reap(self, *, on_orphan) -> int:
        return 0

    def reserve(self, timeout: int) -> str:
        if not self.payloads:
            raise _StopWorker
        return self.payloads.pop(0)

    def release(self, payload: str) -> None: ...


class JobDoneLineTest(unittest.TestCase):
    """One JSON line per finished job: numbers and opaque ids, never a name or path."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        root = Path(self.scratch.name)
        self.engine = fk_engine(root / "worker.db")
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.store = LocalArtifactStore(root / "artifacts")
        for patcher in (
            patch(
                "app.workers.worker.DemoService.for_internal",
                side_effect=lambda db: DemoService(db, artifact_store=self.store, internal=True),
            ),
            patch("app.services.demo_service.get_redis_client"),
            patch("app.workers.worker.SessionLocal", self.Session),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_one_job(self, parse: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        with self.Session() as db:
            demo, job = add_demo_with_job(db, "demo-secret-name", "real_parse")
            bind_source_artifact(db, demo, job)
            job_id = job.id
        log = io.StringIO()
        with patch.object(worker, "ParseQueue", return_value=OneMessageQueue(
            json.dumps({"job_id": job_id, "demo_id": "demo-secret-name"}),
        )), patch.object(worker, "init_db"), patch.object(
            worker, "get_redis_client", return_value=MagicMock(),
        ), patch("app.core.config.Settings.validate_worker_runtime_configuration"), patch.object(
            worker, "run_parse_subprocess", side_effect=parse,
        ), patch.object(worker, "_run_backstop"), contextlib.redirect_stdout(log):
            with self.assertRaises(_StopWorker):
                worker.run_worker()
        lines = [json.loads(line) for line in log.getvalue().splitlines() if line.startswith('{"event":"job_done"')]
        self.assertEqual(len(lines), 1, log.getvalue())
        with self.Session() as db:
            metadata = json.loads(db.get(DemoJob, job_id).metadata_json or "{}")
        return lines[0], metadata, log.getvalue()

    def test_a_completed_parse_reports_timings_size_and_peak_memory(self) -> None:
        def parse(_source: Path, *, on_tick: Any = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
            assert stats is not None
            stats.update(parseS=7.8, peakRssMiB=912)
            return parsed_match()

        line, metadata, _ = self.run_one_job(parse)

        self.assertEqual(list(line), list(JOB_DONE_FIELDS))
        self.assertEqual(line["type"], "real_parse")
        self.assertEqual(line["outcome"], "completed")
        self.assertIsNone(line["errorCode"])
        self.assertEqual(line["attempt"], 1)
        self.assertGreater(line["sourceBytes"], 0)
        self.assertIsInstance(line["downloadS"], float)
        self.assertEqual((line["parseS"], line["peakRssMiB"]), (7.8, 912))
        self.assertIsInstance(line["normalizeS"], float)
        self.assertIsInstance(line["analyzeS"], float)
        self.assertIsInstance(line["coachingEvents"], int)
        self.assertNotIn("secret-name.dem", json.dumps(line))
        self.assertEqual(metadata["parseStats"], {"parseS": 7.8, "peakRssMiB": 912})

    def test_a_failed_parse_reports_its_classification(self) -> None:
        def parse(_source: Path, *, on_tick: Any = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
            assert stats is not None
            stats.update(parseS=3.1, peakRssMiB=4096)
            raise DemoParserError(
                "Parser aborted on a failed memory allocation",
                error_code="PARSE_OUT_OF_MEMORY",
                user_message="This demo needed more memory than the parser is allowed to use.",
            )

        line, metadata, _ = self.run_one_job(parse)

        self.assertEqual(line["outcome"], "failed")
        self.assertEqual(line["errorCode"], "PARSE_OUT_OF_MEMORY")
        self.assertEqual(line["peakRssMiB"], 4096)
        self.assertIsNone(line["normalizeS"])
        self.assertEqual(metadata["failure"]["errorCode"], "PARSE_OUT_OF_MEMORY")
        self.assertEqual(metadata["parseStats"], {"parseS": 3.1, "peakRssMiB": 4096})


if __name__ == "__main__":
    unittest.main()
