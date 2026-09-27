"""Backstop that gives demos completed before match summaries existed their summary.

A new parse stores its summary at completion (ParseLifecycle.complete_parse_job).
Older completed demos have none, so the library would show "—" for them forever.
This pass runs off the worker's idle tick, a few demos at a time: the score comes
from the stored replay blob (read, never written) and the clan names from the
stored source demo through a names-only read in the parse child process, under
the same kind of wall-clock ceiling as a real parse. Names are optional; when
the source is gone or the read fails, the summary is stored without them.

Idempotent: a demo that has a summary is never selected again. A demo that
cannot get one (replay blob missing, no player frames) is remembered for the life
of this process so it does not block the demos behind it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.demo_service import DemoService

PARSE_CHILD_MODULE = "app.workers.parse_child"
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
MATCH_SUMMARY_BACKFILL_INTERVAL_SECONDS = 30
MATCH_SUMMARY_BACKFILL_BATCH = 3
# A names-only read of a 400 MB demo takes about a second; anything near this
# ceiling is a stuck child, and the summary is worth storing without names.
TEAM_NAMES_TIMEOUT_SECONDS = 90
TEAM_NAMES_POLL_SECONDS = 5

_last_backfill = 0.0
_unavailable_demo_ids: set[str] = set()


def run_team_names_subprocess(
    source_path: Path,
    ticks: list[int],
    *,
    timeout_seconds: float = TEAM_NAMES_TIMEOUT_SECONDS,
    on_tick: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Clan names (player id -> name) read in the parse child; {} on any failure."""
    if not ticks:
        return {}
    with tempfile.TemporaryDirectory(prefix="cs2-names-") as workspace:
        output_path = Path(workspace) / "names.json"
        environment = dict(os.environ)
        existing_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{PACKAGE_ROOT}{os.pathsep}{existing_path}" if existing_path else PACKAGE_ROOT
        )
        # Fixed argv, no shell; the only variable parts are a path this process
        # materialized and integers.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                PARSE_CHILD_MODULE,
                "--source",
                str(source_path),
                "--output",
                str(output_path),
                "--memory-limit-bytes",
                str(settings.parse_memory_limit_bytes),
                "--team-names-ticks",
                ",".join(str(int(tick)) for tick in ticks),
            ],
            env=environment,
        )
        started = time.monotonic()
        try:
            while True:
                try:
                    process.wait(timeout=TEAM_NAMES_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    if on_tick is not None:
                        on_tick()
                    if time.monotonic() - started >= timeout_seconds:
                        return {}
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
        if process.returncode != 0:
            return {}
        try:
            body = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    names = body.get("teamNames") if isinstance(body, dict) and body.get("ok") is True else None
    if not isinstance(names, Mapping):
        return {}
    return {str(key): value for key, value in names.items() if isinstance(value, str)}


def backfill_match_summaries(
    *,
    force: bool = False,
    on_tick: Callable[[], None] | None = None,
    session_factory: Callable[[], Any] = SessionLocal,
    read_team_names: Callable[[Path, list[int]], Mapping[str, Any]] | None = None,
    limit: int = MATCH_SUMMARY_BACKFILL_BATCH,
) -> list[str]:
    """One bounded pass; returns the ids of the demos that got a summary.

    Rate-limited like the other idle-tick backstops; `force` skips the limiter
    for tests. `on_tick` keeps the worker's lease and heartbeat fresh while a
    names read runs.
    """
    global _last_backfill

    now = time.monotonic()
    if not force and now - _last_backfill < MATCH_SUMMARY_BACKFILL_INTERVAL_SECONDS:
        return []
    _last_backfill = now

    def default_reader(source_path: Path, ticks: list[int]) -> Mapping[str, Any]:
        return run_team_names_subprocess(source_path, ticks, on_tick=on_tick)

    reader = read_team_names or default_reader
    stored: list[str] = []
    with session_factory() as db:
        service = DemoService.for_internal(db)
        demo_ids = service.demo_ids_missing_match_summary(
            limit=limit,
            exclude=_unavailable_demo_ids,
        )
        for demo_id in demo_ids:
            try:
                done = service.backfill_match_summary(demo_id, reader)
            except Exception as exc:
                done = False
                try:
                    db.rollback()
                except Exception:
                    pass
                print(
                    f"Match summary backfill for demo {demo_id} failed: {type(exc).__name__}",
                    flush=True,
                )
            if done:
                stored.append(demo_id)
            else:
                _unavailable_demo_ids.add(demo_id)
            if on_tick is not None:
                on_tick()
    if stored:
        print(
            f"Stored match summaries for {len(stored)} demo(s): {', '.join(stored)}",
            flush=True,
        )
    return stored


def reset_backfill_state() -> None:
    """Forget the rate limit and the skipped demos (tests)."""
    global _last_backfill
    _last_backfill = 0.0
    _unavailable_demo_ids.clear()
