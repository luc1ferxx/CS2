"""Out-of-process entry point for demo parsing.

demoparser2 is a native extension. A segfault inside it takes down whatever
process it is running in, and before this module that process was the worker
itself -- one malformed demo killed the loop along with any message it was
holding. Running the parse here means the worker survives, sees the exit code,
and can classify the failure like any other.

Nothing in this module may import the database or Redis: it is spawned fresh for
every parse and must stay cheap to start. The import chain
(app.parser.demo_parser -> replay_contract / upload_service -> storage -> config)
holds to that.

Usage: python -m app.workers.parse_child --source <demo path> --output <json path>

With `--team-names-ticks 1,2,3` it only reads each player's clan name at those
ticks (the match summary backfill) and writes {"ok": true, "teamNames": {...}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.parser.demo_parser import DemoParserError, parse_demo_file, parse_team_names_file

# Distinct from any signal-derived code so the parent can tell "the parser
# reported a problem" apart from "the process died".
EXIT_PARSE_ERROR = 3


def apply_memory_limit(limit_bytes: int) -> None:
    """Cap how much memory the parse may actually hold.

    RLIMIT_DATA, not RLIMIT_AS. Address space is the wrong meter for this
    parser: parsing a 386 MB demo in the worker image peaks at 0.95 GiB
    resident against 10.2 GiB of reserved address space, because the Rust
    allocator behind demoparser2/polars reserves arenas it never touches. An
    RLIMIT_AS of 4 GiB therefore does not bound a 4 GiB parse -- it makes
    polars' native module fail to load partway through, and the parser panics
    with a `NameError: PySeries is not defined` that reads like a broken image.
    RLIMIT_DATA counts the anonymous memory actually charged to the process
    (Linux 4.7+), which is the number the limit is meant to be about.

    POSIX only -- there is no `resource` module on Windows. Local Windows
    development just runs without the cap; the worker container is Linux, which
    is where an unbounded parse would actually matter.
    """
    if sys.platform == "win32":
        return
    if limit_bytes <= 0:
        return

    import resource

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    except (OSError, ValueError):
        return
    ceiling = limit_bytes if hard in (resource.RLIM_INFINITY, -1) else min(limit_bytes, hard)
    if soft not in (resource.RLIM_INFINITY, -1) and soft <= ceiling:
        return
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (ceiling, hard))
    except (OSError, ValueError):
        return


def _write(output_path: Path, body: dict[str, Any]) -> None:
    output_path.write_text(json.dumps(body), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse one CS2 demo out of process")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--memory-limit-bytes", type=int, default=0)
    parser.add_argument("--team-names-ticks", default=None)
    args = parser.parse_args(argv)

    apply_memory_limit(args.memory_limit_bytes)
    output_path = Path(args.output)

    if args.team_names_ticks is not None:
        return _team_names_only(Path(args.source), args.team_names_ticks, output_path)

    try:
        parsed = parse_demo_file(Path(args.source))
    except DemoParserError as exc:
        # Carry the classification across the process boundary so the parent can
        # reuse the failure mapping it already has rather than guessing from an
        # exit code.
        _write(
            output_path,
            {
                "ok": False,
                "errorCode": exc.error_code,
                "message": str(exc),
                "userMessage": exc.user_message,
            },
        )
        return EXIT_PARSE_ERROR
    except MemoryError:
        # RLIMIT_DATA refused an allocation Python itself made, which surfaces
        # as a clean MemoryError rather than an OOM kill. Name it as such so the
        # parent does not report a memory ceiling as a mystery parser bug. An
        # allocation refused inside demoparser2/polars never gets here: the Rust
        # allocator prints "memory allocation of N bytes failed" and aborts, and
        # the parent classifies that SIGABRT from this process's stderr.
        _write(
            output_path,
            {
                "ok": False,
                "errorCode": "PARSE_OUT_OF_MEMORY",
                "message": "Parser exceeded its memory limit",
                "userMessage": "This demo needed more memory than the parser is allowed to use.",
            },
        )
        return EXIT_PARSE_ERROR
    except Exception as exc:
        _write(
            output_path,
            {
                "ok": False,
                "errorCode": "PARSER_UNEXPECTED",
                "message": f"{type(exc).__name__}: {exc}",
                "userMessage": None,
            },
        )
        return EXIT_PARSE_ERROR

    _write(output_path, {"ok": True, "parsed": parsed})
    return 0


def _team_names_only(source: Path, raw_ticks: str, output_path: Path) -> int:
    try:
        ticks = sorted({int(value) for value in raw_ticks.split(",") if value.strip()})
        team_names = parse_team_names_file(source, ticks)
    except Exception as exc:
        # Names are optional: the parent just stores the summary without them.
        _write(output_path, {"ok": False, "errorCode": type(exc).__name__})
        return EXIT_PARSE_ERROR
    _write(output_path, {"ok": True, "teamNames": team_names})
    return 0


if __name__ == "__main__":
    sys.exit(main())
