"""The match summary stored on a completed demo, and its backfill for older demos.

The summary is the library's scoreline: two teams with their clan name, starting
side and final score, plus the round count. It is computed from the replay
contract with the same definitions the review page uses in
`frontend/lib/match-stats.ts`; keep the two aligned:

* A team is the set of player ids that share a side in the first round that has
  player frames (round 1 in any demo recorded from the start). Team "A" started
  T, team "B" started CT.
* A team's side in a round is the side its members were on in that round's
  frames between its startTick and endTick (majority vote, the other team's
  members voting for the opposite side), never inferred from the round number:
  sides swap at half time and again in overtime, and the frames a round keeps
  through the half-time break already show the swapped sides. When no member
  appears in a round's frames, that round's kill events vote the same way. A
  round neither can place takes the side of the nearest earlier placed round
  (else the nearest later one).
* A team's score is the number of rounds whose `winnerSide` is that team's side
  in that round.
* A team's name is the most common clan name among its members, or None.

Deliberately tiny -- no per-player stats: the review page derives those from the
replay itself.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update

from app.models.demo import Demo
from app.parser.demo_parser import clean_team_name, team_name_sample_ticks
from app.schemas.demo import MatchSummary
from app.services.demo_service._component import ServiceComponent

MATCH_SUMMARY_VERSION = 1
SIDES = ("T", "CT")

TeamNamesReader = Callable[[Path, list[int]], Mapping[str, Any]]


def build_match_summary(
    replay: Mapping[str, Any],
    player_team_names: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The summary for one replay, or None when its teams cannot be told apart.

    `player_team_names` maps player id -> clan name (the parser's `teamNames`);
    anything else is ignored. Never raises on malformed replay data.
    """
    rounds = _rounds(replay.get("rounds"))
    frame_sides = _frame_sides_by_round(replay.get("frames"), _round_bounds(replay.get("rounds")))
    start_round = next((number for number in sorted(frame_sides) if frame_sides[number]), None)
    if start_round is None:
        return None
    start_sides = frame_sides[start_round]
    team_a = {player_id for player_id, side in start_sides.items() if side == "T"}
    team_b = {player_id for player_id, side in start_sides.items() if side == "CT"}
    kill_sides = _kill_sides_by_round(replay)

    placed: dict[int, str] = {}
    for round_number, _winner in rounds:
        side_a = _team_a_side(team_a, team_b, frame_sides.get(round_number))
        if side_a is None:
            side_a = _team_a_side(team_a, team_b, kill_sides.get(round_number))
        if side_a is not None:
            placed[round_number] = side_a

    score_a = score_b = 0
    for round_number, winner in rounds:
        side_a = placed.get(round_number) or _nearest_placed_side(placed, round_number)
        if side_a is None or winner not in SIDES:
            continue
        if winner == side_a:
            score_a += 1
        else:
            score_b += 1

    names = player_team_names if isinstance(player_team_names, Mapping) else {}
    return {
        "teams": [
            {"key": "A", "name": _team_name(team_a, names), "startSide": "T", "score": score_a},
            {"key": "B", "name": _team_name(team_b, names), "startSide": "CT", "score": score_b},
        ],
        "rounds": len(rounds),
        "version": MATCH_SUMMARY_VERSION,
    }


def public_match_summary(stored: Any) -> MatchSummary | None:
    """The stored JSON as the API shape; None for absent or unreadable values."""
    if not isinstance(stored, Mapping) or stored.get("version") != MATCH_SUMMARY_VERSION:
        return None
    try:
        return MatchSummary.model_validate(stored)
    except ValidationError:
        return None


def _rounds(value: Any) -> list[tuple[int, str | None]]:
    rounds: dict[int, str | None] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        number = _int(item.get("roundNumber"))
        if number is None or number in rounds:
            continue
        winner = item.get("winnerSide")
        rounds[number] = winner if winner in SIDES else None
    return sorted(rounds.items())


def _round_bounds(value: Any) -> dict[int, tuple[int, int]]:
    bounds: dict[int, tuple[int, int]] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        number = _int(item.get("roundNumber"))
        start = _int(item.get("startTick"))
        end = _int(item.get("endTick"))
        if number is None or start is None or end is None or end < start or number in bounds:
            continue
        bounds[number] = (start, end)
    return bounds


def _frame_sides_by_round(
    frames: Any,
    bounds: Mapping[int, tuple[int, int]] | None = None,
) -> dict[int, dict[str, str]]:
    """Round -> player id -> the side that player held in most of its frames.

    Only frames inside the round's [startTick, endTick] vote; a round whose frames
    all fall outside its bounds (or that has none) falls back to all of them.
    """
    in_bounds: dict[int, dict[str, Counter[str]]] = {}
    everywhere: dict[int, dict[str, Counter[str]]] = {}
    round_bounds = bounds or {}
    for frame in frames if isinstance(frames, list) else []:
        if not isinstance(frame, Mapping):
            continue
        number = _int(frame.get("roundNumber"))
        players = frame.get("players")
        if number is None or not isinstance(players, list):
            continue
        tick = _int(frame.get("tick"))
        limits = round_bounds.get(number)
        inside = limits is None or (tick is not None and limits[0] <= tick <= limits[1])
        targets = [everywhere.setdefault(number, {})]
        if inside:
            targets.append(in_bounds.setdefault(number, {}))
        _count_frame_sides(players, targets)
    counts = {
        number: in_bounds.get(number) or by_player for number, by_player in everywhere.items()
    }
    return {
        number: {player_id: _majority(counter) for player_id, counter in by_player.items()}
        for number, by_player in counts.items()
    }


def _count_frame_sides(players: list[Any], targets: list[dict[str, Counter[str]]]) -> None:
    for player in players:
        if not isinstance(player, Mapping):
            continue
        player_id = player.get("id")
        side = player.get("side")
        if player_id is None or side not in SIDES:
            continue
        for by_player in targets:
            by_player.setdefault(str(player_id), Counter())[side] += 1


def _kill_sides_by_round(replay: Mapping[str, Any]) -> dict[int, dict[str, str]]:
    """Round -> player id -> side, from kill records; the fallback for frameless rounds."""
    sides: dict[int, dict[str, str]] = {}
    records: list[tuple[Any, Mapping[str, Any]]] = []
    kills = replay.get("kills")
    for kill in kills if isinstance(kills, list) else []:
        if isinstance(kill, Mapping):
            records.append((kill.get("roundNumber"), kill))
    events = replay.get("events")
    for event in events if isinstance(events, list) else []:
        if isinstance(event, Mapping) and event.get("type") == "kill":
            metadata = event.get("metadata")
            if isinstance(metadata, Mapping):
                records.append((event.get("roundNumber"), metadata))
    for raw_number, record in records:
        number = _int(raw_number)
        if number is None:
            continue
        by_player = sides.setdefault(number, {})
        for id_key, side_key in (("attackerId", "attackerSide"), ("victimId", "victimSide")):
            player_id = record.get(id_key)
            side = record.get(side_key)
            if player_id is not None and side in SIDES:
                by_player.setdefault(str(player_id), side)
    return sides


def _team_a_side(
    team_a: set[str],
    team_b: set[str],
    observed: Mapping[str, str] | None,
) -> str | None:
    if not observed:
        return None
    votes: Counter[str] = Counter()
    for player_id, side in observed.items():
        if player_id in team_a:
            votes[side] += 1
        elif player_id in team_b:
            votes[_opposite(side)] += 1
    if votes["T"] == votes["CT"]:
        return None
    return "T" if votes["T"] > votes["CT"] else "CT"


def _nearest_placed_side(placed: Mapping[int, str], round_number: int) -> str | None:
    earlier = [number for number in placed if number < round_number]
    if earlier:
        return placed[max(earlier)]
    later = [number for number in placed if number > round_number]
    return placed[min(later)] if later else None


def _team_name(members: Iterable[str], names: Mapping[str, Any]) -> str | None:
    counter: Counter[str] = Counter()
    for player_id in members:
        name = clean_team_name(names.get(player_id))
        if name is not None:
            counter[name] += 1
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def _majority(counter: Counter[str]) -> str:
    return "T" if counter["T"] >= counter["CT"] else "CT"


def _opposite(side: str) -> str:
    return "CT" if side == "T" else "T"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class MatchSummaries(ServiceComponent):
    """Reached as ``DemoService.summary``."""

    def demo_ids_missing_summary(
        self,
        *,
        limit: int,
        exclude: Iterable[str] = (),
    ) -> list[str]:
        """Completed demos without a summary, newest first."""
        query = self.db.query(Demo.id).filter(
            Demo.status == "completed",
            Demo.match_summary.is_(None),
        )
        excluded = sorted(set(exclude))
        if excluded:
            query = query.filter(Demo.id.notin_(excluded))
        rows = query.order_by(Demo.created_at.desc(), Demo.id.asc()).limit(limit).all()
        return [str(row.id) for row in rows]

    def backfill_match_summary(self, demo_id: str, read_team_names: TeamNamesReader) -> bool:
        """Compute and store one completed demo's summary; True when stored.

        Reads the stored replay blob (never writes it) and, for an uploaded demo,
        the clan names from the verified source through `read_team_names`. Names
        are optional: a missing or unreadable source leaves them None. The write
        only lands on a row still completed, still without a summary and still on
        the replay it was computed from, so a re-parse or deletion in between wins.
        """
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        if demo is None or demo.status != "completed" or demo.match_summary is not None:
            return False
        replay_key = demo.replay_storage_key
        replay = self._service.replay.load_replay_blob(demo)
        if replay is None:
            return False
        team_names = self._source_team_names(demo, replay, read_team_names)
        summary = build_match_summary(replay, team_names)
        if summary is None:
            return False
        replay_key_matches = (
            Demo.replay_storage_key.is_(None)
            if replay_key is None
            else Demo.replay_storage_key == replay_key
        )
        result = self.db.execute(
            update(Demo)
            .where(
                Demo.id == demo_id,
                Demo.status == "completed",
                Demo.match_summary.is_(None),
                replay_key_matches,
            )
            # Derived data, not a user-visible change: keep updated_at as it was.
            .values(match_summary=summary, updated_at=Demo.updated_at)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return bool(getattr(result, "rowcount", 0) == 1)

    def _source_team_names(
        self,
        demo: Demo,
        replay: Mapping[str, Any],
        read_team_names: TeamNamesReader,
    ) -> Mapping[str, Any]:
        ticks = team_name_sample_ticks(replay.get("rounds"))
        job = self._service.parse.latest_parse_job(demo)
        if not ticks or job is None or job.job_type != "real_parse" or not demo.source_storage_key:
            return {}
        try:
            with self._service.ingest.materialized_source_demo(demo, job) as source_path:
                return read_team_names(source_path, ticks)
        except Exception:
            return {}
