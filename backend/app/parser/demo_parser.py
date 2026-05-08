from __future__ import annotations

import math
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.services.upload_service import MAX_DEMO_UPLOAD_BYTES, safe_upload_filename

MAX_SAMPLE_FRAMES = 720


class DemoParserError(RuntimeError):
    pass


def parse_demo_file(source_path: Path) -> dict[str, Any]:
    demo_path = _resolve_demo_path(source_path)
    try:
        from demoparser2 import DemoParser
    except ImportError as exc:
        raise DemoParserError(
            "demoparser2 is not installed in this environment; rebuild the backend image "
            "after installing backend/requirements.txt"
        ) from exc

    parser = DemoParser(str(demo_path))
    header = _safe_records_value(lambda: parser.parse_header(), {})
    tick_rate = _derive_tick_rate(header)
    map_name = str(header.get("map_name") or header.get("mapName") or header.get("map") or "unknown")

    player_records = _safe_records(lambda: parser.parse_player_info())
    death_records = _parse_event_records(
        parser,
        "player_death",
        player=["X", "Y", "health", "team_num"],
        other=["total_rounds_played"],
    )
    round_start_records = _parse_event_records(
        parser,
        "round_start",
        other=["total_rounds_played"],
    )
    round_end_records = _parse_event_records(
        parser,
        "round_end",
        other=["total_rounds_played"],
    )

    rounds = _build_rounds(round_start_records, round_end_records, header, tick_rate)
    sample_ticks = _sample_ticks(rounds, death_records, header, tick_rate)
    tick_records = _parse_tick_records(parser, sample_ticks)
    if not tick_records:
        raise DemoParserError("demoparser2 returned no sampled player position ticks")

    players = _merge_players(player_records, tick_records)
    frames = _build_frames(tick_records, rounds, tick_rate)
    kills = _build_kills(death_records)

    return {
        "mapName": map_name,
        "tickRate": tick_rate,
        "rounds": rounds,
        "players": players,
        "frames": frames,
        "kills": kills,
        "deaths": kills,
    }


def _resolve_demo_path(source_path: Path) -> Path:
    if source_path.suffix.lower() == ".dem":
        return source_path
    if source_path.suffix.lower() != ".zip":
        raise DemoParserError(f"Unsupported parser input: {source_path.suffix}")

    extract_dir = source_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_path) as archive:
        members = [
            item
            for item in archive.infolist()
            if not item.is_dir() and Path(item.filename).suffix.lower() == ".dem"
        ]
        if not members:
            raise DemoParserError("Zip upload did not contain a .dem file")
        member = max(members, key=lambda item: item.file_size)
        if member.file_size > MAX_DEMO_UPLOAD_BYTES:
            raise DemoParserError("Zip-contained .dem exceeds the 1 GiB parser limit")
        target = extract_dir / safe_upload_filename(Path(member.filename).name)
        if not target.exists() or target.stat().st_size != member.file_size:
            with archive.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    return target


def _parse_event_records(parser: Any, event_name: str, **kwargs: Any) -> list[dict[str, Any]]:
    if hasattr(parser, "parse_event"):
        try:
            return _records(parser.parse_event(event_name, **kwargs))
        except TypeError:
            try:
                props = [*kwargs.get("player", []), *kwargs.get("other", [])]
                return _records(parser.parse_event(event_name, props=props))
            except Exception:
                return []
        except Exception:
            return []
    if hasattr(parser, "parse_events"):
        try:
            return _records(parser.parse_events(event_name))
        except Exception:
            return []
    return []


def _parse_tick_records(parser: Any, sample_ticks: list[int]) -> list[dict[str, Any]]:
    prop_sets = [
        ["X", "Y", "Z", "health", "is_alive", "team_num", "has_bomb"],
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
    ends: list[dict[str, Any]],
    header: dict[str, Any],
    tick_rate: int,
) -> list[dict[str, Any]]:
    start_ticks = sorted({int(item.get("tick", 0)) for item in starts if item.get("tick") is not None})
    end_records = sorted(
        [item for item in ends if item.get("tick") is not None],
        key=lambda item: int(item.get("tick", 0)),
    )
    end_ticks = [int(item.get("tick", 0)) for item in end_records]
    if not start_ticks and end_ticks:
        start_ticks = [0, *[tick + 1 for tick in end_ticks[:-1]]]
    if not start_ticks:
        playback_ticks = int(header.get("playback_ticks") or header.get("ticks") or 0)
        if playback_ticks <= 0:
            return []
        return [_round(1, 0, min(playback_ticks, 15 * tick_rate), playback_ticks, "CT")]

    rounds = []
    for index, start_tick in enumerate(start_ticks, start=1):
        next_start = start_ticks[index] if index < len(start_ticks) else None
        matching_end = next((tick for tick in end_ticks if tick >= start_tick), None)
        if matching_end is None and next_start is not None:
            matching_end = next_start - 1
        if matching_end is None:
            matching_end = int(header.get("playback_ticks") or start_tick)
        if matching_end <= start_tick:
            continue
        winner = "CT"
        for end_record in end_records:
            if int(end_record.get("tick", 0)) == matching_end:
                winner = _side_from_value(
                    end_record.get("winner")
                    or end_record.get("winner_side")
                    or end_record.get("winnerSide")
                ) or "CT"
                break
        rounds.append(
            _round(
                index,
                start_tick,
                min(matching_end, start_tick + 15 * tick_rate),
                matching_end,
                winner,
            )
        )
    return rounds


def _round(number: int, start_tick: int, freeze_end_tick: int, end_tick: int, winner: str) -> dict[str, Any]:
    return {
        "roundNumber": number,
        "startTick": start_tick,
        "freezeEndTick": freeze_end_tick,
        "endTick": end_tick,
        "winnerSide": winner,
    }


def _sample_ticks(
    rounds: list[dict[str, Any]],
    death_records: list[dict[str, Any]],
    header: dict[str, Any],
    tick_rate: int,
) -> list[int]:
    if rounds:
        total_span = sum(max(1, int(item["endTick"]) - int(item["startTick"])) for item in rounds)
        step = max(tick_rate * 4, math.ceil(total_span / MAX_SAMPLE_FRAMES))
        ticks = set()
        for item in rounds:
            start_tick = int(item["startTick"])
            end_tick = int(item["endTick"])
            ticks.update({start_tick, end_tick})
            ticks.update(range(start_tick, end_tick + 1, step))
    else:
        end_tick = int(header.get("playback_ticks") or 0)
        step = max(tick_rate * 4, math.ceil(max(1, end_tick) / MAX_SAMPLE_FRAMES))
        ticks = set(range(0, end_tick + 1, step))
        if end_tick:
            ticks.add(end_tick)

    for record in death_records:
        tick = record.get("tick")
        if tick is not None:
            ticks.add(int(tick))
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
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in tick_records:
        tick = record.get("tick")
        if tick is None or not _finite(record.get("X")) or not _finite(record.get("Y")):
            continue
        steamid = record.get("steamid") or record.get("player_steamid") or record.get("name")
        player_id = str(steamid)
        side = _side_from_value(record.get("team_num") or record.get("team_number") or record.get("team_name"))
        health = _int_or_default(record.get("health"), 100)
        alive_value = record.get("is_alive")
        alive = bool(alive_value) if alive_value is not None and _finite(alive_value) else health > 0
        grouped[int(tick)].append(
            {
                "id": player_id,
                "name": str(record.get("name") or player_id),
                "side": side or "T",
                "x": float(record["X"]),
                "y": float(record["Y"]),
                "alive": alive,
                "hp": health,
                "hasBomb": bool(record.get("has_bomb") or record.get("hasBomb") or False),
            }
        )

    return [
        {
            "tick": tick,
            "timeSeconds": round(tick / tick_rate, 2),
            "roundNumber": _round_for_tick(tick, rounds),
            "players": players,
            "bombState": _bomb_state(players),
        }
        for tick, players in sorted(grouped.items())
        if players
    ]


def _build_kills(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kills = []
    for record in records:
        tick = record.get("tick")
        if tick is None:
            continue
        kills.append(
            {
                "tick": int(tick),
                "roundNumber": int(record.get("total_rounds_played") or 0) + 1,
                "attackerId": _optional_str(record.get("attacker_steamid")),
                "attackerName": _optional_str(record.get("attacker_name")),
                "victimId": _optional_str(record.get("user_steamid")),
                "victimName": _optional_str(record.get("user_name")),
                "weapon": _optional_str(record.get("weapon")),
            }
        )
    return kills


def _round_for_tick(tick: int, rounds: list[dict[str, Any]]) -> int:
    for item in rounds:
        if int(item["startTick"]) <= tick <= int(item["endTick"]):
            return int(item["roundNumber"])
    return int(rounds[0]["roundNumber"]) if rounds else 1


def _bomb_state(players: list[dict[str, Any]]) -> dict[str, Any]:
    carrier = next((player for player in players if player.get("hasBomb")), None)
    if carrier:
        return {"status": "carried", "carrierPlayerId": carrier["id"]}
    return {"status": "carried"}


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
    except (TypeError, ValueError):
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
