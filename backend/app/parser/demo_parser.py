from __future__ import annotations

import math
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.parser.replay_contract import normalize_bomb_site
from app.services.upload_service import MAX_DEMO_UPLOAD_BYTES, safe_upload_filename

MAX_SAMPLE_FRAMES = 30000
SAMPLE_INTERVAL_SECONDS = 0.25
MIN_DEMO_BYTES = 16


class DemoParserError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PARSER_UNEXPECTED",
        user_message: str | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message or message


def parse_demo_file(source_path: Path) -> dict[str, Any]:
    demo_path = _resolve_demo_path(source_path)
    _validate_demo_file(demo_path)
    try:
        from demoparser2 import DemoParser
    except ImportError as exc:
        raise DemoParserError(
            "demoparser2 is not installed in this environment; rebuild the backend image "
            "after installing backend/requirements.txt",
            error_code="UNSUPPORTED_PARSER_FORMAT",
            user_message="Demo parser support is unavailable in this environment.",
        ) from exc

    try:
        parser = DemoParser(str(demo_path))
    except Exception as exc:
        raise DemoParserError(
            f"demoparser2 could not open the uploaded demo: {exc}",
            error_code="INVALID_DEMO",
            user_message="Invalid or unreadable demo file.",
        ) from exc
    header = _safe_records_value(lambda: parser.parse_header(), {})
    tick_rate = _derive_tick_rate(header)
    map_name = str(header.get("map_name") or header.get("mapName") or header.get("map") or "unknown")

    player_records = _safe_records(lambda: parser.parse_player_info())
    death_records = _parse_event_records(
        parser,
        "player_death",
        player=["X", "Y", "Z", "health", "team_num"],
        other=["total_rounds_played"],
    )
    damage_records = _parse_event_records(
        parser,
        "player_hurt",
        player=["X", "Y", "Z", "health", "team_num"],
        other=["total_rounds_played", "dmg_health", "dmg_armor", "armor", "weapon"],
    )
    round_start_records = _parse_event_records(
        parser,
        "round_start",
        other=["total_rounds_played"],
    )
    round_freeze_end_records = _parse_event_records(
        parser,
        "round_freeze_end",
        other=["total_rounds_played"],
    )
    round_end_records = _parse_event_records(
        parser,
        "round_end",
        other=["total_rounds_played"],
    )
    bomb_planted_records = _parse_event_records(
        parser,
        "bomb_planted",
        player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played", "site"],
    )
    bomb_defused_records = _parse_event_records(
        parser,
        "bomb_defused",
        player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played", "site"],
    )
    bomb_exploded_records = _parse_event_records(
        parser,
        "bomb_exploded",
        other=["total_rounds_played", "site"],
    )
    bomb_dropped_records = _parse_event_records(
        parser, "bomb_dropped", player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played"],
    )
    bomb_pickup_records = _parse_event_records(
        parser, "bomb_pickup", player=["X", "Y", "Z", "team_num"],
        other=["total_rounds_played"],
    )
    utility_records = {
        "smoke": _parse_first_event_records(
            parser,
            ["smokegrenade_detonate"],
            player=["X", "Y", "Z", "team_num"],
            other=["total_rounds_played"],
        ),
        "flash": _parse_first_event_records(
            parser,
            ["flashbang_detonate"],
            player=["X", "Y", "Z", "team_num"],
            other=["total_rounds_played"],
        ),
        "molotov": _parse_first_event_records(
            parser,
            ["molotov_detonate", "inferno_startburn"],
            player=["X", "Y", "Z", "team_num"],
            other=["total_rounds_played"],
        ),
        "he": _parse_first_event_records(
            parser,
            ["hegrenade_detonate"],
            player=["X", "Y", "Z", "team_num"],
            other=["total_rounds_played"],
        ),
    }

    rounds = _build_rounds(round_start_records, round_freeze_end_records, round_end_records, header, tick_rate)
    bomb_events = _align_round_numbers(_build_bomb_events(
        bomb_planted_records, bomb_defused_records, bomb_exploded_records,
        bomb_dropped_records, bomb_pickup_records,
    ), rounds)
    events = _align_round_numbers(sorted(
        [*_build_round_events(round_start_records, round_end_records, rounds),
         *_build_damage_events(damage_records), *bomb_events, *_build_utility_events(utility_records)],
        key=lambda item: int(item.get("tick", 0)),
    ), rounds)
    sample_ticks = _sample_ticks(rounds, [*death_records, *events], header, tick_rate)
    if not sample_ticks:
        raise DemoParserError(
            "demoparser2 returned no rounds, playback ticks, or event ticks to sample",
            error_code="MISSING_MATCH_METADATA",
            user_message="Demo is missing essential match metadata needed to sample replay ticks.",
        )
    tick_records = _parse_tick_records(parser, sample_ticks)
    if not tick_records:
        raise DemoParserError(
            "demoparser2 returned no sampled player position ticks",
            error_code="MISSING_FRAMES",
            user_message="Demo parsed without usable player position ticks.",
        )

    players = _merge_players(player_records, tick_records)
    frames = _build_frames(tick_records, rounds, tick_rate, bomb_events)
    kills = _align_round_numbers(_build_kills(death_records), rounds)

    return {
        "mapName": map_name,
        "tickRate": tick_rate,
        "rounds": rounds,
        "players": players,
        "frames": frames,
        "kills": kills,
        "deaths": kills,
        "events": events,
    }


def _resolve_demo_path(source_path: Path) -> Path:
    if source_path.suffix.lower() == ".dem":
        return source_path
    if source_path.suffix.lower() != ".zip":
        raise DemoParserError(
            f"Unsupported parser input: {source_path.suffix}",
            error_code="UNSUPPORTED_PARSER_FORMAT",
            user_message="Unsupported demo parser input format.",
        )

    extract_dir = source_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(source_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DemoParserError(
            "Zip upload is not a readable archive",
            error_code="INVALID_DEMO",
            user_message="Invalid or unreadable demo archive.",
        ) from exc

    with archive:
        members = [
            item
            for item in archive.infolist()
            if not item.is_dir() and Path(item.filename).suffix.lower() == ".dem"
        ]
        if not members:
            raise DemoParserError(
                "Zip upload did not contain a .dem file",
                error_code="INVALID_DEMO",
                user_message="Archive did not contain a .dem file.",
            )
        member = max(members, key=lambda item: item.file_size)
        if member.file_size > MAX_DEMO_UPLOAD_BYTES:
            raise DemoParserError(
                "Zip-contained .dem exceeds the 1 GiB parser limit",
                error_code="INVALID_DEMO",
                user_message="Demo file exceeds the parser size limit.",
            )
        target = extract_dir / safe_upload_filename(Path(member.filename).name)
        if not target.exists() or target.stat().st_size != member.file_size:
            with archive.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    return target


def _validate_demo_file(demo_path: Path) -> None:
    try:
        size = demo_path.stat().st_size
    except OSError as exc:
        raise DemoParserError(
            "Uploaded demo artifact could not be read",
            error_code="STORAGE_READ_FAILED",
            user_message="Uploaded demo artifact could not be read from storage.",
        ) from exc
    if size < MIN_DEMO_BYTES:
        raise DemoParserError(
            "Demo file is too small to be a readable CS2 demo",
            error_code="INVALID_DEMO",
            user_message="Invalid or unreadable demo file.",
        )


def _parse_event_records(parser: Any, event_name: str, **kwargs: Any) -> list[dict[str, Any]]:
    if hasattr(parser, "parse_event"):
        variants = [kwargs]
        if "Z" in kwargs.get("player", []):
            variants.append({**kwargs, "player": [prop for prop in kwargs["player"] if prop != "Z"]})
        variants.append({})
        for options in variants:
            try:
                return _records(parser.parse_event(event_name, **options))
            except TypeError:
                try:
                    props = [*options.get("player", []), *options.get("other", [])]
                    return _records(parser.parse_event(event_name, props=props))
                except Exception:
                    continue
            except Exception:
                continue
        return []
    if hasattr(parser, "parse_events"):
        try:
            return _records(parser.parse_events(event_name))
        except Exception:
            return []
    return []


def _parse_first_event_records(parser: Any, event_names: list[str], **kwargs: Any) -> list[dict[str, Any]]:
    for event_name in event_names:
        records = _parse_event_records(parser, event_name, **kwargs)
        if records:
            return records
    return []


def _parse_tick_records(parser: Any, sample_ticks: list[int]) -> list[dict[str, Any]]:
    prop_sets = [
        ["X", "Y", "Z", "health", "is_alive", "team_num", "inventory", "is_bomb_planted", "is_bomb_dropped"],
        ["X", "Y", "Z", "health", "is_alive", "team_num"],
        ["X", "Y", "Z", "health", "team_num"],
        ["X", "Y", "Z"],
        ["X", "Y", "health", "is_alive", "team_num"],
        ["X", "Y", "health", "team_num"],
        ["X", "Y"],
    ]
    for props in prop_sets:
        try:
            return _records(parser.parse_ticks(props, ticks=sample_ticks))
        except Exception:
            continue
    return []


def _build_rounds(
    starts: list[dict[str, Any]],
    freeze_ends: list[dict[str, Any]],
    ends: list[dict[str, Any]],
    header: dict[str, Any],
    tick_rate: int,
) -> list[dict[str, Any]]:
    start_records = sorted(
        {int(item["tick"]): item for item in starts if _optional_int(item.get("tick")) is not None}.values(),
        key=lambda item: int(item["tick"]),
    )
    end_records = sorted(
        [item for item in ends if _optional_int(item.get("tick")) is not None and int(item["tick"]) > 0],
        key=lambda item: int(item.get("tick", 0)),
    )
    freeze_records = sorted(
        [item for item in freeze_ends if item.get("tick") is not None],
        key=lambda item: int(item.get("tick", 0)),
    )
    if not start_records and end_records:
        start_records = [
            {
                "tick": int(end_records[index - 1]["tick"]) + 1 if index else 0,
                "roundNumber": _known_round_number(record, is_round_end=True) or index + 1,
            }
            for index, record in enumerate(end_records)
        ]
    if not start_records:
        playback_ticks = int(header.get("playback_ticks") or header.get("ticks") or 0)
        if playback_ticks <= 0:
            return []
        return [_round(1, 0, min(playback_ticks, 15 * tick_rate), playback_ticks, None)]

    rounds = []
    for index, start_record in enumerate(start_records):
        start_tick = int(start_record["tick"])
        next_record = start_records[index + 1] if index + 1 < len(start_records) else None
        next_start = int(next_record["tick"]) if next_record is not None else None
        known_number = _known_round_number(start_record)
        if known_number is not None and known_number <= 0:
            continue
        end_record = next(
            (
                item for item in end_records
                if start_tick < int(item["tick"])
                and (next_start is None or int(item["tick"]) < next_start)
            ),
            None,
        )
        # A repeated start for the same round supersedes its unplayed predecessor.
        if end_record is None and next_record is not None and known_number is not None:
            if _known_round_number(next_record) == known_number:
                continue
        end_tick = (
            int(end_record["tick"]) if end_record is not None
            else next_start - 1 if next_start is not None
            else int(header.get("playback_ticks") or header.get("ticks") or start_tick)
        )
        if end_tick <= start_tick:
            continue
        number = known_number or (
            _known_round_number(end_record, is_round_end=True) if end_record is not None else None
        ) or len(rounds) + 1
        winner = None
        winner_reason = None
        if end_record is not None:
            winner = _side_from_value(
                end_record.get("winner")
                or end_record.get("winner_side")
                or end_record.get("winnerSide")
            )
            winner_reason = _optional_str(
                end_record.get("reason")
                or end_record.get("round_end_reason")
                or end_record.get("winner_reason")
                or end_record.get("winnerReason")
            )
        freeze_end_tick = _freeze_end_tick_for_round(number, start_tick, end_tick, freeze_records)
        rounds.append(
            _round(
                number,
                start_tick,
                min(end_tick, freeze_end_tick if freeze_end_tick is not None else start_tick + 15 * tick_rate),
                end_tick,
                winner,
                winner_reason,
            )
        )
    return rounds


def _round(
    number: int,
    start_tick: int,
    freeze_end_tick: int,
    end_tick: int,
    winner: str | None,
    winner_reason: str | None = None,
) -> dict[str, Any]:
    round_info = {
        "roundNumber": number,
        "startTick": start_tick,
        "freezeEndTick": freeze_end_tick,
        "endTick": end_tick,
        "winnerSide": winner,
    }
    if winner_reason:
        round_info["winnerReason"] = winner_reason
    return round_info


def _freeze_end_tick_for_round(
    round_number: int,
    start_tick: int,
    end_tick: int,
    freeze_records: list[dict[str, Any]],
) -> int | None:
    candidates = [record for record in freeze_records if start_tick <= int(record["tick"]) <= end_tick]
    matching = next((record for record in candidates if _known_round_number(record) == round_number), None)
    record = matching or (candidates[0] if candidates else None)
    return int(record["tick"]) if record is not None else None


def _sample_ticks(
    rounds: list[dict[str, Any]],
    death_records: list[dict[str, Any]],
    header: dict[str, Any],
    tick_rate: int,
) -> list[int]:
    regular_step = max(1, round(tick_rate * SAMPLE_INTERVAL_SECONDS))
    if rounds:
        first_tick = min(int(item["startTick"]) for item in rounds)
        last_tick = max(int(item["endTick"]) for item in rounds)
        total_span = max(1, last_tick - first_tick)
        step = max(regular_step, math.ceil(total_span / MAX_SAMPLE_FRAMES))
        ticks = set(range(first_tick, last_tick + 1, step))
        for item in rounds:
            start_tick = int(item["startTick"])
            end_tick = int(item["endTick"])
            ticks.update({start_tick, end_tick, int(item.get("freezeEndTick", start_tick))})
    else:
        end_tick = int(header.get("playback_ticks") or 0)
        step = max(regular_step, math.ceil(max(1, end_tick) / MAX_SAMPLE_FRAMES))
        ticks = set(range(0, end_tick + 1, step)) if end_tick > 0 else set()
        if end_tick > 0:
            ticks.add(end_tick)

    lower = min(ticks) if ticks else 0
    upper = max(ticks) if ticks else None
    for record in death_records:
        tick = _optional_int(record.get("tick"))
        if tick is not None and tick >= lower and (upper is None or tick <= upper):
            # Keep both sides of discrete transitions for exact seek and playback.
            ticks.update({max(lower, tick - 1), tick})
    return sorted(tick for tick in ticks if tick >= 0)


def _merge_players(
    player_records: list[dict[str, Any]],
    tick_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for record in [*player_records, *tick_records]:
        steamid = record.get("steamid") or record.get("player_steamid")
        name = record.get("name") or record.get("player_name") or steamid
        if steamid is None and name is None:
            continue
        player_id = str(steamid or name)
        side = _side_from_value(
            record.get("side")
            or record.get("team")
            or record.get("team_num")
            or record.get("team_number")
            or record.get("team_name")
        )
        players.setdefault(
            player_id,
            {
                "id": player_id,
                "name": str(name or player_id),
                "side": side or "T",
            },
        )
        if side:
            players[player_id]["side"] = side
    return list(players.values())


def _build_frames(
    tick_records: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    tick_rate: int,
    bomb_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    bomb_flags: dict[int, dict[str, bool]] = defaultdict(dict)
    for record in tick_records:
        tick = _optional_int(record.get("tick"))
        if tick is None or not _finite(record.get("X")) or not _finite(record.get("Y")):
            continue
        steamid = record.get("steamid") or record.get("player_steamid") or record.get("name")
        if steamid is None:
            continue
        player_id = str(steamid)
        side = _side_from_value(record.get("team_num") or record.get("team_number") or record.get("team_name"))
        health = _int_or_default(record.get("health"), 100)
        alive_value = record.get("is_alive")
        alive = bool(alive_value) if alive_value is not None and _finite(alive_value) else health > 0
        for key in ("is_bomb_planted", "is_bomb_dropped"):
            flag = _safe_bool(record.get(key))
            if flag is not None:
                bomb_flags[tick][key] = flag
        has_bomb = _record_has_bomb(record)
        if has_bomb is not None:
            bomb_flags[tick]["inventoryKnown"] = True
        grouped[tick].append(
            {
                "id": player_id,
                "name": str(record.get("name") or player_id),
                "side": side or "T",
                "x": float(record["X"]),
                "y": float(record["Y"]),
                **({"z": float(record["Z"])} if _finite(record.get("Z")) else {}),
                "alive": alive,
                "hp": health,
                "hasBomb": has_bomb is True,
            }
        )

    frames = []
    ordered_rounds = sorted(rounds, key=lambda item: item["startTick"])
    transitions = sorted(bomb_events or [], key=lambda item: (
        item["tick"], {"bomb_dropped": 0, "bomb_pickup": 1, "bomb_planted": 2}.get(item["type"], 3),
    ))
    event_index = 0
    round_start = None
    state: dict[str, Any] = {"status": "unknown"}
    for tick, players in sorted(grouped.items()):
        active_round = next((item for item in reversed(ordered_rounds) if item["startTick"] <= tick), None)
        current_start = active_round["startTick"] if active_round else None
        if current_start != round_start:
            state = {"status": "unknown"}
            round_start = current_start
        while event_index < len(transitions) and transitions[event_index]["tick"] <= tick:
            event = transitions[event_index]
            if round_start is not None and event["tick"] >= round_start:
                state = _apply_bomb_event(state, event)
            event_index += 1
        state = _bomb_state(players, state, bomb_flags[tick])
        for player in players:
            player["hasBomb"] = state.get("carrierPlayerId") == player["id"] and state["status"] == "carried"
        frames.append({
            "tick": tick,
            "timeSeconds": round(tick / tick_rate, 4),
            "roundNumber": active_round["roundNumber"] if active_round else 1,
            "players": players,
            "bombState": dict(state),
        })
    return frames


def _build_kills(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kills = []
    for record in records:
        tick = record.get("tick")
        if tick is None:
            continue
        kills.append(
            {
                "tick": int(tick),
                "roundNumber": _round_number_from_record(record),
                "attackerId": _optional_str(record.get("attacker_steamid")),
                "attackerName": _optional_str(record.get("attacker_name")),
                "attackerSide": _side_from_value(
                    record.get("attacker_team")
                    or record.get("attacker_team_name")
                    or record.get("attacker_team_num")
                ),
                "victimId": _optional_str(record.get("user_steamid")),
                "victimName": _optional_str(record.get("user_name")),
                "victimSide": _side_from_value(
                    record.get("user_team")
                    or record.get("user_team_name")
                    or record.get("user_team_num")
                    or record.get("team_num")
                ),
                "assisterId": _optional_str(record.get("assister_steamid")),
                "assisterName": _optional_str(record.get("assister_name")),
                "weapon": _optional_str(record.get("weapon")),
                "headshot": _optional_bool(record.get("headshot")),
                **({"x": position[0], "y": position[1]} if (position := _event_position(record)) is not None else {}),
                **({"z": z} if (z := _event_z(record)) is not None else {}),
            }
        )
    return kills


def _build_damage_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        tick = _optional_int(record.get("tick"))
        if tick is None:
            continue
        attacker_id = _optional_str(record.get("attacker_steamid"))
        victim_id = _optional_str(record.get("user_steamid") or record.get("player_steamid"))
        event = _parser_event(record, "damage", "Damage")
        if event is None:
            continue
        attacker_name = _optional_str(record.get("attacker_name"))
        victim_name = _optional_str(record.get("user_name") or record.get("player_name"))
        event["id"] = f"damage-{tick}-{attacker_id or 'attacker'}-{victim_id or 'victim'}"
        event["playerId"] = attacker_id or event.get("playerId")
        event["playerName"] = attacker_name or event.get("playerName")
        attacker_side = _side_from_value(record.get("attacker_team_num") or record.get("attacker_team_name") or record.get("attacker_team"))
        if attacker_side is not None:
            event["side"] = attacker_side
        elif attacker_id:
            event.pop("side", None)
        event["playerIds"] = _compact_player_ids([attacker_id, victim_id])
        event["metadata"] = {
            key: value
            for key, value in {
                "attackerId": attacker_id,
                "attackerName": attacker_name,
                "victimId": victim_id,
                "victimName": victim_name,
                "damageHealth": _optional_int(record.get("dmg_health")),
                "damageArmor": _optional_int(record.get("dmg_armor")),
                "health": _optional_int(record.get("health")),
                "armor": _optional_int(record.get("armor")),
                "weapon": _optional_str(record.get("weapon")),
            }.items()
            if value is not None
        }
        events.append(event)
    return sorted(events, key=lambda item: int(item.get("tick", 0)))


def _build_round_events(
    starts: list[dict[str, Any]],
    ends: list[dict[str, Any]],
    rounds: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for records, event_type, label, boundary in (
        (starts, "round_start", "Round started", "startTick"),
        (ends, "round_end", "Round ended", "endTick"),
    ):
        for record in records:
            if rounds is not None:
                matching = next((item for item in rounds if item[boundary] == _optional_int(record.get("tick"))), None)
                if matching is None:
                    continue
                record = {**record, "roundNumber": matching["roundNumber"]}
            event = _round_event(record, event_type, label)
            if event is not None:
                events.append(event)
    return sorted(events, key=lambda item: int(item.get("tick", 0)))


def _round_event(record: dict[str, Any], event_type: str, label: str) -> dict[str, Any] | None:
    tick = _optional_int(record.get("tick"))
    round_number = _round_number_from_record(record, is_round_end=event_type == "round_end")
    if tick is None or round_number <= 0:
        return None
    metadata = _compact_metadata(record, ["reason", "round_end_reason", "winner_reason", "winnerReason"])
    winner_side = _side_from_value(
        record.get("winner")
        or record.get("winner_side")
        or record.get("winnerSide")
    )
    if winner_side:
        metadata["winnerSide"] = winner_side
    return {
        "id": f"{event_type}-{tick}-r{round_number}",
        "type": event_type,
        "tick": tick,
        "roundNumber": round_number,
        "source": "parser",
        "playerIds": [],
        "label": label,
        "metadata": metadata,
    }


def _build_bomb_events(
    planted: list[dict[str, Any]],
    defused: list[dict[str, Any]],
    exploded: list[dict[str, Any]],
    dropped: list[dict[str, Any]] | None = None,
    picked_up: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events = []
    for record in planted:
        event = _parser_event(
            record,
            "bomb_planted",
            f"Bomb planted {site}" if (site := normalize_bomb_site(record.get("site"))) else "Bomb planted",
        )
        if event is not None:
            events.append(event)
    for record in defused:
        event = _parser_event(record, "bomb_defused", "Bomb defused")
        if event is not None:
            events.append(event)
    for record in exploded:
        event = _parser_event(record, "bomb_exploded", "Bomb exploded")
        if event is not None:
            events.append(event)
    for records, event_type, label in (
        (dropped or [], "bomb_dropped", "Bomb dropped"),
        (picked_up or [], "bomb_pickup", "Bomb picked up"),
    ):
        for record in records:
            event = _parser_event(record, event_type, label)
            if event is not None:
                events.append(event)
    return sorted(events, key=lambda item: int(item.get("tick", 0)))


def _build_utility_events(records_by_type: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    labels = {
        "smoke": "Smoke",
        "flash": "Flash",
        "molotov": "Molotov",
        "he": "HE",
    }
    events: list[dict[str, Any]] = []
    for event_type, records in records_by_type.items():
        for record in records:
            event = _parser_event(record, event_type, labels[event_type])
            if event is not None:
                events.append(event)
    return sorted(events, key=lambda item: int(item.get("tick", 0)))


def _parser_event(record: dict[str, Any], event_type: str, label: str) -> dict[str, Any] | None:
    tick = _optional_int(record.get("tick"))
    if tick is None:
        return None
    player_id = _optional_str(record.get("user_steamid") or record.get("player_steamid"))
    event = {
        "id": f"{event_type}-{tick}-{_optional_str(record.get('user_steamid')) or _optional_str(record.get('player_steamid')) or 'event'}",
        "type": event_type,
        "tick": tick,
        "roundNumber": _round_number_from_record(record),
        "source": "parser",
        "playerIds": _compact_player_ids([player_id]),
        "playerId": player_id,
        "playerName": _optional_str(record.get("user_name") or record.get("player_name")),
        "side": _side_from_value(
            record.get("team")
            or record.get("team_name")
            or record.get("team_num")
            or record.get("team_number")
            or record.get("user_team_name")
            or record.get("user_team_num")
        ),
        "label": label,
        "metadata": {
            **_compact_metadata(record, ["weapon"]),
            **({"site": site} if (site := normalize_bomb_site(record.get("site"))) else {}),
        },
    }
    position = _event_position(record)
    if position is not None:
        event["x"] = position[0]
        event["y"] = position[1]
        if (z := _event_z(record)) is not None:
            event["z"] = z
    return {key: value for key, value in event.items() if value is not None}


def _compact_player_ids(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    player_ids: list[str] = []
    for value in values:
        if value is None:
            continue
        player_id = str(value).strip()
        if not player_id or player_id in seen:
            continue
        seen.add(player_id)
        player_ids.append(player_id)
    return player_ids


def _known_round_number(record: dict[str, Any], *, is_round_end: bool = False) -> int | None:
    for key in ("roundNumber", "round_number", "round"):
        value = _optional_int(record.get(key))
        if value is not None:
            return value
    completed = _optional_int(record.get("total_rounds_played"))
    if completed is not None and completed >= 0:
        # At round_end the counter already includes the round that just finished.
        return max(1, completed) if is_round_end else completed + 1
    return None


def _round_number_from_record(record: dict[str, Any], *, is_round_end: bool = False) -> int:
    number = _known_round_number(record, is_round_end=is_round_end)
    return number if number is not None else 1


def _align_round_numbers(records: list[dict[str, Any]], rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        matching = next((item for item in rounds if item["startTick"] <= record["tick"] <= item["endTick"]), None)
        if matching is not None:
            record["roundNumber"] = matching["roundNumber"]
    return records


def _event_position(record: dict[str, Any]) -> tuple[float, float] | None:
    x_value = _first_finite_value(record, ["x", "X", "grenade_x", "entity_x", "player_x", "user_X"])
    y_value = _first_finite_value(record, ["y", "Y", "grenade_y", "entity_y", "player_y", "user_Y"])
    if x_value is None or y_value is None:
        return None
    return x_value, y_value


def _event_z(record: dict[str, Any]) -> float | None:
    return _first_finite_value(record, ["z", "Z", "grenade_z", "entity_z", "player_z", "user_Z"])


def _first_finite_value(record: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = record.get(key)
        if _finite(value):
            return float(value)
    return None


def _compact_metadata(record: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            metadata[key] = value
    return metadata


def _round_for_tick(tick: int, rounds: list[dict[str, Any]]) -> int:
    for item in rounds:
        if int(item["startTick"]) <= tick <= int(item["endTick"]):
            return int(item["roundNumber"])
    return int(rounds[0]["roundNumber"]) if rounds else 1


def _apply_bomb_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    status = {"bomb_pickup": "carried", "bomb_dropped": "dropped", "bomb_planted": "planted",
              "bomb_defused": "defused", "bomb_exploded": "exploded"}.get(event["type"])
    if status is None:
        return state
    result: dict[str, Any] = {"status": status}
    if status == "carried":
        if event.get("playerId"):
            result["carrierPlayerId"] = event["playerId"]
        return result
    # Terminal events refer to a player who may have moved away from the bomb.
    position = state if status in {"defused", "exploded"} and state["status"] == "planted" else event
    if status == "exploded" and state["status"] != "planted":
        position = {}
    for key in ("x", "y", "z"):
        if _finite(position.get(key)):
            result[key] = float(position[key])
    return result


def _bomb_state(
    players: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
    flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    state = previous or {"status": "unknown"}
    flags = flags or {}
    if state["status"] in {"planted", "defused", "exploded"}:
        return state
    if flags.get("is_bomb_planted"):
        return {"status": "planted"}
    if flags.get("is_bomb_dropped"):
        return state if state["status"] == "dropped" else {"status": "dropped"}
    carrier = next((player for player in players if player.get("hasBomb") and player.get("alive")), None)
    if carrier is None and not flags.get("inventoryKnown") and state["status"] == "carried":
        carrier = next((player for player in players if player["id"] == state.get("carrierPlayerId") and player.get("alive")), None)
    if carrier is not None:
        return {"status": "carried", "carrierPlayerId": carrier["id"],
                **{key: carrier[key] for key in ("x", "y", "z") if key in carrier}}
    return {"status": "unknown"} if state["status"] == "carried" else state


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if _finite(value) and float(value) in {0, 1}:
        return bool(value)
    return None


def _record_has_bomb(record: dict[str, Any]) -> bool | None:
    for key in ("has_bomb", "hasBomb"):
        if (value := _safe_bool(record.get(key))) is not None:
            return value
    inventory = record.get("inventory")
    if hasattr(inventory, "tolist"):
        inventory = inventory.tolist()
    if isinstance(inventory, (list, tuple)):
        return any(str(item).lower() in {"c4 explosive", "c4", "weapon_c4"} for item in inventory)
    return None


def _derive_tick_rate(header: dict[str, Any]) -> int:
    for key in ("tick_rate", "tickRate"):
        value = header.get(key)
        if value:
            return int(round(float(value)))
    playback_ticks = header.get("playback_ticks")
    playback_time = header.get("playback_time")
    if playback_ticks and playback_time:
        tick_rate = int(round(float(playback_ticks) / float(playback_time)))
        if 16 <= tick_rate <= 256:
            return tick_rate
    return 64


def _int_or_default(value: Any, default: int) -> int:
    if value is None or not _finite(value):
        return default
    return int(value)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _side_from_value(value: Any) -> str | None:
    if value in {"T", "CT"}:
        return str(value)
    if value == 2:
        return "T"
    if value == 3:
        return "CT"
    if isinstance(value, str):
        upper = value.upper()
        if upper in {"2", "T", "TERRORIST", "TERRORISTS"} or "TERRORIST" in upper:
            return "T"
        if upper in {"3", "CT"} or "COUNTER" in upper:
            return "CT"
    return None


def _safe_records(callback: Any) -> list[dict[str, Any]]:
    try:
        return _records(callback())
    except Exception:
        return []


def _safe_records_value(callback: Any, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = callback()
    except Exception:
        return default
    return value if isinstance(value, dict) else default


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    if hasattr(value, "to_dict"):
        records = value.to_dict("records")
        return [dict(item) for item in records]
    return []


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
