"""The match summary stored on a completed demo, and its backfill for older demos.

The summary is the library's scoreline: two teams with their clan name, starting
side and final score, plus the round count. Sides, teams and scores follow the
side rule below, which `frontend/lib/match-stats.ts` (the review page)
implements identically. The shared cases in `fixtures/match-rules/` pin it:
`backend/tests/test_match_side_rules.py` and
`frontend/lib/match-side-rules.test.mjs` both run every one of them. Change the
rule in both files and in the fixtures together.

Side rule. It reads the replay contract as GET /demos/{id}/replay serves it; a
stored blob goes through the same normalization first. A roundNumber or tick
counts only as a finite number of magnitude at most 2**53 - 1 (the browser's
JSON.parse rounds larger integers, so both sides read those as missing), and
player ids sort by code point.

1. Rounds are `rounds` without repeated roundNumbers (the first one wins).
   Frames and events filed under any other round number never decide a side.
2. A player's side in round R is the majority of that player's T/CT entries in
   R's in-bounds frames. A frame is in bounds when its tick is a finite number
   within R's [startTick, endTick]; when R has no valid bounds, every frame
   with a tick counts. The frames a round keeps through the half-time break
   already show the swapped sides and fall outside it. When nobody has an
   in-bounds entry in R, all of R's frames vote instead. A tie goes to the side
   of the player's earliest entry (by tick, frames without a tick last, then
   array order).
3. A player without a frame vote in R takes the side from R's kill events
   (metadata attackerSide / victimSide; by tick, then array order, attacker
   before victim). No other event assigns a side.
4. The start round is the first round in which anybody has a side. Its T
   players are team A (started T), its CT players team B (started CT). Round by
   round, team members vote for A's side: A members with their side, B members
   with the opposite one. The majority places the round; a tie leaves it
   unplaced. After a placed round, players without a team (a substitute) join
   the team playing their side there. An unplaced round takes the side of the
   nearest earlier placed round, else the nearest later one.
5. A team's score is the number of rounds whose `winnerSide` is that team's
   side in that round.

A team's name is the most common clan name among its members, or None.

Deliberately tiny -- no per-player stats: the review page derives those from the
replay itself.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import ColumnElement, func, or_, update

from app.models.demo import Demo
from app.parser.demo_parser import clean_team_name, team_name_sample_ticks
from app.parser.replay_contract import normalize_replay_contract
from app.schemas.demo import MatchSummary
from app.services.demo_service._component import ServiceComponent

# Version 2: the shared side rule above. The backfill recomputes older summaries.
MATCH_SUMMARY_VERSION = 2
SIDES = ("T", "CT")
TEAM_KEYS = ("A", "B")

TeamNamesReader = Callable[[Path, list[int]], Mapping[str, Any]]
# The largest integer the browser's JSON.parse reads exactly (Number.MAX_SAFE_INTEGER).
MAX_SAFE_INTEGER = 2**53 - 1
# (frame without a tick, tick, frame index, entry index): the order of a frame vote.
VoteOrder = tuple[bool, float, int, int]


@dataclass(frozen=True)
class MatchSideRules:
    """The side rule's result for one replay (see the module docstring)."""

    # (roundNumber, winnerSide or None), ascending, one entry per round number.
    rounds: list[tuple[float, str | None]]
    # Round -> player id -> side; every round of `rounds` has an entry.
    sides_by_round: dict[float, dict[str, str]]
    # Player id -> "A" or "B".
    team_of: dict[str, str]
    # Round -> team A's side in it; empty when nobody has a side anywhere.
    team_a_sides: dict[float, str]

    @property
    def has_teams(self) -> bool:
        return bool(self.team_a_sides)

    def team_side(self, key: str, round_number: float) -> str | None:
        side_a = self.team_a_sides.get(round_number)
        if side_a is None:
            return None
        return side_a if key == "A" else _opposite(side_a)

    def members(self, key: str) -> list[str]:
        return sorted(player_id for player_id, team in self.team_of.items() if team == key)

    def score(self, key: str) -> int:
        return sum(
            1
            for number, winner in self.rounds
            if winner is not None and winner == self.team_side(key, number)
        )

    def outcome(self) -> dict[str, Any]:
        """The shape of a `fixtures/match-rules` case's `expected`."""
        teams = [
            {
                "key": key,
                "playerIds": self.members(key),
                "startSide": _start_side(key),
                "score": self.score(key),
            }
            for key in TEAM_KEYS
        ]
        return {
            "sidesByRound": {
                _round_key(number): dict(sorted(self.sides_by_round.get(number, {}).items()))
                for number, _winner in self.rounds
            },
            "teams": teams if self.has_teams else [],
            "teamSidesByRound": {
                _round_key(number): {key: self.team_side(key, number) for key in TEAM_KEYS}
                for number, _winner in self.rounds
            },
        }


def match_side_rules(replay: Mapping[str, Any]) -> MatchSideRules:
    """The side rule applied to one replay; never raises on malformed data."""
    served = _served_contract(replay)
    rounds = _unique_rounds(served.get("rounds"))
    numbers = [number for number, _round in rounds]
    sides_by_round = _frame_sides_by_round(
        served.get("frames"),
        {number: _bounds(item) for number, item in rounds},
    )
    _fill_kill_sides(sides_by_round, served.get("events"))
    team_of, team_a_sides = _assign_teams(numbers, sides_by_round)
    return MatchSideRules(
        rounds=[(number, _winner(item)) for number, item in rounds],
        sides_by_round=sides_by_round,
        team_of=team_of,
        team_a_sides=team_a_sides,
    )


def build_match_summary(
    replay: Mapping[str, Any],
    player_team_names: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The summary for one replay, or None when nobody has a side in any round.

    `player_team_names` maps player id -> clan name (the parser's `teamNames`);
    anything else is ignored. Never raises on malformed replay data.
    """
    rules = match_side_rules(replay)
    if not rules.has_teams:
        return None
    names = player_team_names if isinstance(player_team_names, Mapping) else {}
    return {
        "teams": [
            {
                "key": key,
                "name": _team_name(rules.members(key), names),
                "startSide": _start_side(key),
                "score": rules.score(key),
            }
            for key in TEAM_KEYS
        ],
        "rounds": len(rules.rounds),
        "version": MATCH_SUMMARY_VERSION,
    }


def public_match_summary(stored: Any) -> MatchSummary | None:
    """The stored JSON as the API shape; None for absent or unreadable values.

    An older version stays visible until the backfill recomputes it.
    """
    if not isinstance(stored, Mapping) or not 1 <= _stored_version(stored) <= MATCH_SUMMARY_VERSION:
        return None
    try:
        return MatchSummary.model_validate(stored)
    except ValidationError:
        return None


def summary_is_stale(stored: Any) -> bool:
    """True for a missing summary or one stored before the current version."""
    return _stored_version(stored) < MATCH_SUMMARY_VERSION


def _stale_summary_clause() -> ColumnElement[bool]:
    # The SQL twin of summary_is_stale: SQL NULL, no version, or an older one.
    version = Demo.match_summary["version"].as_integer()
    return or_(
        Demo.match_summary.is_(None),
        func.coalesce(version, 0) < MATCH_SUMMARY_VERSION,
    )


def _stored_version(stored: Any) -> int:
    version = stored.get("version") if isinstance(stored, Mapping) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def _served_contract(replay: Mapping[str, Any]) -> dict[str, Any]:
    # What the review page reads: normalized rounds (a missing winner reads CT)
    # and events (a legacy `kills` list is not merged in), frames as stored.
    source = replay if isinstance(replay, Mapping) else {}
    return normalize_replay_contract(
        {key: source[key] for key in ("rounds", "frames", "events") if key in source}
    )


def _unique_rounds(value: Any) -> list[tuple[float, Mapping[str, Any]]]:
    rounds: dict[float, Mapping[str, Any]] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        number = _number(item.get("roundNumber"))
        if number is not None and number not in rounds:
            rounds[number] = item
    return sorted(rounds.items(), key=lambda entry: entry[0])


def _winner(item: Mapping[str, Any]) -> str | None:
    winner = item.get("winnerSide")
    return winner if isinstance(winner, str) and winner in SIDES else None


def _bounds(item: Mapping[str, Any]) -> tuple[float, float] | None:
    start = _number(item.get("startTick"))
    end = _number(item.get("endTick"))
    if start is None or end is None or end < start:
        return None
    return start, end


@dataclass
class _Tally:
    counts: Counter[str] = field(default_factory=Counter)
    first: tuple[VoteOrder, str] | None = None

    def vote(self, side: str, order: VoteOrder) -> None:
        self.counts[side] += 1
        if self.first is None or order < self.first[0]:
            self.first = (order, side)

    def side(self) -> str:
        if self.counts["T"] != self.counts["CT"]:
            return "T" if self.counts["T"] > self.counts["CT"] else "CT"
        return self.first[1] if self.first is not None else "T"


def _frame_sides_by_round(
    frames: Any,
    bounds: Mapping[float, tuple[float, float] | None],
) -> dict[float, dict[str, str]]:
    """Round -> player id -> side from the frames (steps 1 and 2 of the rule)."""
    in_bounds: dict[float, dict[str, _Tally]] = {}
    everywhere: dict[float, dict[str, _Tally]] = {}
    for frame_index, frame in enumerate(frames if isinstance(frames, list) else []):
        if not isinstance(frame, Mapping):
            continue
        number = _number(frame.get("roundNumber"))
        players = frame.get("players")
        if number is None or number not in bounds or not isinstance(players, list):
            continue
        tick = _number(frame.get("tick"))
        limits = bounds[number]
        inside = tick is not None and (limits is None or limits[0] <= tick <= limits[1])
        for position, player in enumerate(players):
            if not isinstance(player, Mapping):
                continue
            player_id = player.get("id")
            side = player.get("side")
            if not isinstance(player_id, str) or not player_id:
                continue
            if not isinstance(side, str) or side not in SIDES:
                continue
            order: VoteOrder = (tick is None, tick if tick is not None else 0, frame_index, position)
            everywhere.setdefault(number, {}).setdefault(player_id, _Tally()).vote(side, order)
            if inside:
                in_bounds.setdefault(number, {}).setdefault(player_id, _Tally()).vote(side, order)
    return {
        number: {
            player_id: tally.side()
            for player_id, tally in (in_bounds.get(number) or everywhere.get(number) or {}).items()
        }
        for number in bounds
    }


def _fill_kill_sides(sides_by_round: dict[float, dict[str, str]], events: Any) -> None:
    """Step 3 of the rule: kill events give a side to players without a frame vote."""
    kills: list[tuple[float, int, float, Mapping[str, Any]]] = []
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, Mapping) or event.get("type") != "kill":
            continue
        tick = _number(event.get("tick"))
        number = _number(event.get("roundNumber"))
        metadata = event.get("metadata")
        if tick is None or number is None or number not in sides_by_round:
            continue
        if isinstance(metadata, Mapping):
            kills.append((tick, index, number, metadata))
    kills.sort(key=lambda kill: (kill[0], kill[1]))
    for _tick, _index, number, metadata in kills:
        sides = sides_by_round[number]
        for id_key, side_key in (("attackerId", "attackerSide"), ("victimId", "victimSide")):
            player_id = metadata.get(id_key)
            side = metadata.get(side_key)
            if isinstance(player_id, str) and player_id and isinstance(side, str) and side in SIDES:
                sides.setdefault(player_id, side)


def _assign_teams(
    numbers: list[float],
    sides_by_round: Mapping[float, Mapping[str, str]],
) -> tuple[dict[str, str], dict[float, str]]:
    """Step 4 of the rule: team membership and team A's side in every round."""
    team_of: dict[str, str] = {}
    start = next((number for number in numbers if sides_by_round.get(number)), None)
    if start is None:
        return team_of, {}
    for player_id, side in sides_by_round[start].items():
        team_of[player_id] = "A" if side == "T" else "B"

    placed: dict[float, str] = {}
    for number in numbers:
        sides = sides_by_round.get(number, {})
        votes = 0
        for player_id, side in sides.items():
            team = team_of.get(player_id)
            if team is None:
                continue
            side_a = side if team == "A" else _opposite(side)
            votes += 1 if side_a == "T" else -1
        if votes == 0:
            continue
        side_a = "T" if votes > 0 else "CT"
        placed[number] = side_a
        for player_id, side in sides.items():
            team_of.setdefault(player_id, "A" if side == side_a else "B")

    team_a_sides: dict[float, str] = {}
    previous: str | None = None
    unplaced: list[float] = []
    for number in numbers:
        known = placed.get(number)
        if known is None:
            unplaced.append(number)
            continue
        for gap in unplaced:
            team_a_sides[gap] = previous or known
        unplaced.clear()
        team_a_sides[number] = previous = known
    for gap in unplaced:
        if previous is not None:
            team_a_sides[gap] = previous
    return team_of, team_a_sides


def _team_name(members: Iterable[str], names: Mapping[str, Any]) -> str | None:
    counter: Counter[str] = Counter()
    for player_id in members:
        name = clean_team_name(names.get(player_id))
        if name is not None:
            counter[name] += 1
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def _start_side(key: str) -> str:
    return "T" if key == "A" else "CT"


def _opposite(side: str) -> str:
    return "CT" if side == "T" else "T"


def _number(value: Any) -> float | None:
    """A finite JSON number the review page reads exactly (its isRuleNumber)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not isfinite(value):
        return None
    return value if abs(value) <= MAX_SAFE_INTEGER else None


def _round_key(number: float) -> str:
    # JSON object keys as the review page writes them: 3, not 3.0.
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


class MatchSummaries(ServiceComponent):
    """Reached as ``DemoService.summary``."""

    def demo_ids_missing_summary(
        self,
        *,
        limit: int,
        exclude: Iterable[str] = (),
    ) -> list[str]:
        """Completed demos without a current-version summary, newest first."""
        query = self.db.query(Demo.id).filter(
            Demo.status == "completed",
            _stale_summary_clause(),
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
        only lands on a row still completed, still without a current-version
        summary and still on the replay it was computed from, so a re-parse or
        deletion in between wins.
        """
        demo = self.db.query(Demo).filter(Demo.id == demo_id).one_or_none()
        if demo is None or demo.status != "completed" or not summary_is_stale(demo.match_summary):
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
                _stale_summary_clause(),
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
