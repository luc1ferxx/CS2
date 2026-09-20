"""The parser's view of a linked SteamMatch row: the summary fields derived
from a parsed replay and the state transitions the parse path writes."""

import json
import math
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.demo import Demo
from app.models.job import DemoJob
from app.models.steam import SteamMatch
from app.services.demo_service._helpers import utc_now
from app.services.demo_service.constants import (
    STEAM_MATCH_PARSE_FAILED_MESSAGE,
    STEAM_MATCH_PLAYER_ID_LIMIT,
    STEAM_MATCH_PLAYER_LIMIT,
    STEAM_MATCH_PLAYER_NAME_LIMIT,
)


def _steam_match_parser_summary(replay: dict[str, Any]) -> dict[str, Any]:
    ct_round_wins, t_round_wins = _steam_match_round_wins(
        replay.get("rounds"),
        replay.get("events"),
    )
    return {
        "map_name": _steam_match_map_name(replay.get("mapName")),
        "duration_seconds": _steam_match_duration_seconds(replay.get("video")),
        "ct_round_wins": ct_round_wins,
        "t_round_wins": t_round_wins,
        "players_json": _steam_match_players_json(replay.get("players")),
    }


def _steam_match_map_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or normalized.lower() == "unknown"
        or len(normalized) > 64
        or not normalized.isprintable()
    ):
        return None
    return normalized


def _steam_match_duration_seconds(video: Any) -> int | None:
    if not isinstance(video, dict):
        return None
    value = video.get("durationSeconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    if not math.isfinite(duration) or duration < 0 or duration > 24 * 60 * 60:
        return None
    return round(duration)


def _steam_match_round_wins(
    rounds: Any,
    events: Any,
) -> tuple[int | None, int | None]:
    if (
        not isinstance(rounds, list)
        or not rounds
        or len(rounds) > 100
        or not isinstance(events, list)
    ):
        return None, None
    round_numbers: set[int] = set()
    for round_info in rounds:
        if not isinstance(round_info, dict):
            return None, None
        round_number = round_info.get("roundNumber")
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or round_number <= 0
            or round_number in round_numbers
        ):
            return None, None
        round_numbers.add(round_number)

    winner_by_round: dict[int, str] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "round_end":
            continue
        round_number = event.get("roundNumber")
        metadata = event.get("metadata")
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or not isinstance(metadata, dict)
        ):
            continue
        side = str(metadata.get("winnerSide") or "").strip().upper()
        if side not in {"CT", "T"} or round_number in winner_by_round:
            return None, None
        winner_by_round[round_number] = side
    if set(winner_by_round) != round_numbers:
        return None, None
    sides = list(winner_by_round.values())
    return sides.count("CT"), sides.count("T")


def _steam_match_players_json(players: Any) -> str | None:
    if not isinstance(players, list):
        return None
    compact_players: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for player in players:
        if not isinstance(player, dict):
            continue
        player_id = _steam_match_player_id(player.get("id"))
        player_name = _steam_match_player_name(player.get("name"))
        side = str(player.get("side") or "").strip().upper()
        if (
            player_id is None
            or player_name is None
            or side not in {"CT", "T"}
            or player_id in seen_ids
        ):
            continue
        seen_ids.add(player_id)
        compact_players.append(
            {
                "id": player_id,
                "name": player_name,
                "side": side,
            }
        )
        if len(compact_players) >= STEAM_MATCH_PLAYER_LIMIT:
            break
    return json.dumps(compact_players, ensure_ascii=False, separators=(",", ":"))


def _steam_match_player_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if (
        not normalized
        or normalized.lower() == "unknown"
        or len(normalized) > STEAM_MATCH_PLAYER_ID_LIMIT
        or not normalized.isprintable()
    ):
        return None
    return normalized


def _steam_match_player_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = "".join(
        character for character in value if character.isprintable()
    ).strip()
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    return normalized[:STEAM_MATCH_PLAYER_NAME_LIMIT]


class SteamMatchParseState:
    """The parser-side transitions of the SteamMatch row linked to a demo.

    A demo imported from Steam match history keeps its SteamMatch row in step
    with the parse job: reset for retry, dispatched, parsing, ready, or
    unavailable with the reason. These are the only writes the parser path
    makes to that table, kept together so the field set each transition
    touches can be compared side by side. Every method issues one UPDATE and
    leaves the commit to the caller.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def linked_match_id(self, demo: Demo, job: DemoJob) -> str | None:
        if job.job_type != "real_parse":
            return None
        return (
            self._db.query(SteamMatch.id)
            .filter(
                SteamMatch.demo_id == demo.id,
                SteamMatch.owner_id == demo.owner_id,
            )
            .scalar()
        )

    def reset_for_retry(self, match_id: str, demo: Demo) -> None:
        (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == demo.owner_id,
                SteamMatch.demo_id == demo.id,
            )
            .update(
                {
                    SteamMatch.status: "parsing",
                    SteamMatch.parser_dispatched_at: None,
                    SteamMatch.parser_dispatched_job_id: None,
                    SteamMatch.last_import_error_code: None,
                    SteamMatch.last_import_error_message: None,
                    SteamMatch.updated_at: utc_now(),
                },
                synchronize_session=False,
            )
        )

    def mark_dispatch_unavailable(self, match_id: str, demo: Demo, job_id: str, failed_at: datetime) -> int:
        """Returns the number of rows updated: 0 means the job already moved on."""
        return (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == demo.owner_id,
                SteamMatch.demo_id == demo.id,
                SteamMatch.status != "ready",
                SteamMatch.parser_dispatched_job_id.is_(None),
                self._db.query(DemoJob.id)
                .filter(
                    DemoJob.id == job_id,
                    DemoJob.demo_id == demo.id,
                    DemoJob.job_type == "real_parse",
                    DemoJob.status == "queued",
                )
                .exists(),
            )
            .update(
                {
                    SteamMatch.status: "unavailable",
                    SteamMatch.last_import_error_code: (
                        "parser_dispatch_unavailable"
                    ),
                    SteamMatch.last_import_error_message: (
                        "Parser dispatch is temporarily unavailable."
                    ),
                    SteamMatch.updated_at: failed_at,
                },
                synchronize_session=False,
            )
        )

    def mark_dispatched(self, match_id: str, demo: Demo, job_id: str, dispatched_at: datetime) -> None:
        (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.id == match_id,
                SteamMatch.owner_id == demo.owner_id,
                SteamMatch.demo_id == demo.id,
                or_(
                    SteamMatch.parser_dispatched_job_id.is_(None),
                    SteamMatch.parser_dispatched_job_id == job_id,
                ),
                self._db.query(DemoJob.id)
                .filter(
                    DemoJob.id == job_id,
                    DemoJob.demo_id == demo.id,
                    DemoJob.job_type == "real_parse",
                    DemoJob.status != "failed",
                )
                .exists(),
            )
            .update(
                {
                    SteamMatch.parser_dispatched_at: dispatched_at,
                    SteamMatch.parser_dispatched_job_id: job_id,
                    SteamMatch.updated_at: dispatched_at,
                },
                synchronize_session=False,
            )
        )

    def mark_parsing(self, demo: Demo, job: DemoJob, started_at: datetime) -> None:
        (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.demo_id == demo.id,
                SteamMatch.owner_id == demo.owner_id,
            )
            .update(
                {
                    SteamMatch.status: "parsing",
                    SteamMatch.parser_dispatched_at: started_at,
                    SteamMatch.parser_dispatched_job_id: job.id,
                    SteamMatch.map_name: None,
                    SteamMatch.duration_seconds: None,
                    SteamMatch.ct_round_wins: None,
                    SteamMatch.t_round_wins: None,
                    SteamMatch.players_json: None,
                    SteamMatch.last_import_error_code: None,
                    SteamMatch.last_import_error_message: None,
                    SteamMatch.import_completed_at: None,
                    SteamMatch.updated_at: started_at,
                },
                synchronize_session=False,
            )
        )

    def mark_ready(self, demo: Demo, replay: dict[str, Any], completed_at: datetime) -> None:
        summary = _steam_match_parser_summary(replay)
        (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.demo_id == demo.id,
                SteamMatch.owner_id == demo.owner_id,
            )
            .update(
                {
                    SteamMatch.status: "ready",
                    SteamMatch.map_name: summary["map_name"],
                    SteamMatch.duration_seconds: summary["duration_seconds"],
                    SteamMatch.ct_round_wins: summary["ct_round_wins"],
                    SteamMatch.t_round_wins: summary["t_round_wins"],
                    SteamMatch.players_json: summary["players_json"],
                    SteamMatch.import_run_id: None,
                    SteamMatch.import_lease_expires_at: None,
                    SteamMatch.import_completed_at: completed_at,
                    SteamMatch.last_import_error_code: None,
                    SteamMatch.last_import_error_message: None,
                    SteamMatch.updated_at: completed_at,
                },
                synchronize_session=False,
            )
        )

    def mark_parse_failed(self, demo: Demo, failed_at: datetime) -> None:
        (
            self._db.query(SteamMatch)
            .filter(
                SteamMatch.demo_id == demo.id,
                SteamMatch.owner_id == demo.owner_id,
            )
            .update(
                {
                    SteamMatch.status: "unavailable",
                    SteamMatch.map_name: None,
                    SteamMatch.duration_seconds: None,
                    SteamMatch.ct_round_wins: None,
                    SteamMatch.t_round_wins: None,
                    SteamMatch.players_json: None,
                    SteamMatch.import_run_id: None,
                    SteamMatch.import_lease_expires_at: None,
                    SteamMatch.import_completed_at: failed_at,
                    SteamMatch.last_import_error_code: "parser_failed",
                    SteamMatch.last_import_error_message: (
                        STEAM_MATCH_PARSE_FAILED_MESSAGE
                    ),
                    SteamMatch.updated_at: failed_at,
                },
                synchronize_session=False,
            )
        )
