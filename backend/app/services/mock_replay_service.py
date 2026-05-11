from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any


def build_mock_replay(demo_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tick_rate = 64
    rounds = [
        {
            "roundNumber": 1,
            "startTick": 0,
            "freezeEndTick": 96,
            "endTick": 704,
            "winnerSide": "CT",
        },
        {
            "roundNumber": 2,
            "startTick": 768,
            "freezeEndTick": 864,
            "endTick": 1472,
            "winnerSide": "T",
        },
        {
            "roundNumber": 3,
            "startTick": 1536,
            "freezeEndTick": 1632,
            "endTick": 2240,
            "winnerSide": "CT",
        },
    ]

    players = [
        {"id": "t-entry", "name": "aimclub.entry", "side": "T", "color": "#f5b542"},
        {"id": "t-trade", "name": "aimclub.trade", "side": "T", "color": "#f5b542"},
        {"id": "t-support", "name": "aimclub.flash", "side": "T", "color": "#f5b542"},
        {"id": "t-lurk", "name": "aimclub.lurk", "side": "T", "color": "#f5b542"},
        {"id": "t-awp", "name": "aimclub.awp", "side": "T", "color": "#f5b542"},
        {"id": "ct-anchor", "name": "ct.anchor", "side": "CT", "color": "#2ed3d0"},
        {"id": "ct-rotate", "name": "ct.rotate", "side": "CT", "color": "#2ed3d0"},
        {"id": "ct-rifler", "name": "ct.rifler", "side": "CT", "color": "#2ed3d0"},
        {"id": "ct-awp", "name": "ct.awp", "side": "CT", "color": "#2ed3d0"},
        {"id": "ct-support", "name": "ct.support", "side": "CT", "color": "#2ed3d0"},
    ]

    frames: list[dict[str, Any]] = []
    for round_info in rounds:
        for tick in range(round_info["startTick"], round_info["endTick"] + 1, 16):
            progress = (tick - round_info["startTick"]) / (
                round_info["endTick"] - round_info["startTick"]
            )
            frames.append(
                {
                    "tick": tick,
                    "timeSeconds": round(tick / tick_rate, 2),
                    "roundNumber": round_info["roundNumber"],
                    "players": [
                        _player_frame(player, tick, progress, round_info["roundNumber"])
                        for player in players
                    ],
                    "bombState": _bomb_state(tick, round_info["roundNumber"]),
                }
            )

    replay = {
        "contractVersion": "replay_contract_v1",
        "demoId": demo_id,
        "mapName": "de_inferno",
        "tickRate": tick_rate,
        "video": {
            "status": "ready",
            "url": None,
            "durationSeconds": round((rounds[-1]["endTick"] - rounds[0]["startTick"]) / tick_rate, 2),
            "tickStart": rounds[0]["startTick"],
            "tickEnd": rounds[-1]["endTick"],
            "tickRate": tick_rate,
            "source": "mock",
            "errorMessage": None,
            "timeOriginSeconds": 0,
        },
        "rounds": rounds,
        "players": players,
        "frames": frames,
        "kills": _mock_kills(),
        "deaths": _mock_kills(),
        "events": _mock_replay_events(),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    return replay, _coaching_events(demo_id)


def _player_frame(
    player: dict[str, str],
    tick: int,
    progress: float,
    round_number: int,
) -> dict[str, Any]:
    route = _route_for_player(player["id"], round_number)
    x = _lerp(route[0], route[2], progress)
    y = _lerp(route[1], route[3], progress)

    wobble = math.sin(progress * math.pi * 2 + len(player["id"])) * 1.8
    x = max(5, min(95, x + wobble))
    y = max(5, min(95, y - wobble))

    alive = True
    hp = 100
    if round_number == 1 and player["id"] == "t-entry" and tick >= 416:
        alive = False
        hp = 0
    if round_number == 2 and player["id"] == "ct-anchor" and tick >= 1088:
        alive = False
        hp = 0
    if round_number == 3 and player["id"] == "t-lurk" and tick >= 1984:
        alive = False
        hp = 0

    return {
        "id": player["id"],
        "name": player["name"],
        "side": player["side"],
        "x": round(x, 2),
        "y": round(y, 2),
        "alive": alive,
        "hp": hp,
        "hasBomb": player["id"] == "t-support" and round_number != 3,
    }


def _route_for_player(player_id: str, round_number: int) -> tuple[float, float, float, float]:
    routes: dict[int, dict[str, tuple[float, float, float, float]]] = {
        1: {
            "t-entry": (18, 72, 61, 48),
            "t-trade": (16, 78, 47, 57),
            "t-support": (12, 83, 42, 70),
            "t-lurk": (22, 88, 29, 42),
            "t-awp": (9, 68, 35, 61),
            "ct-anchor": (82, 31, 67, 43),
            "ct-rotate": (72, 24, 59, 36),
            "ct-rifler": (81, 57, 62, 56),
            "ct-awp": (69, 17, 52, 28),
            "ct-support": (88, 42, 72, 51),
        },
        2: {
            "t-entry": (15, 72, 79, 56),
            "t-trade": (13, 79, 74, 62),
            "t-support": (11, 85, 66, 71),
            "t-lurk": (24, 89, 33, 51),
            "t-awp": (8, 67, 49, 68),
            "ct-anchor": (82, 33, 68, 61),
            "ct-rotate": (74, 23, 62, 48),
            "ct-rifler": (83, 56, 69, 59),
            "ct-awp": (68, 18, 59, 34),
            "ct-support": (88, 43, 73, 54),
        },
        3: {
            "t-entry": (18, 73, 55, 34),
            "t-trade": (16, 80, 49, 42),
            "t-support": (13, 85, 43, 58),
            "t-lurk": (24, 90, 39, 39),
            "t-awp": (8, 67, 31, 60),
            "ct-anchor": (82, 31, 62, 38),
            "ct-rotate": (73, 24, 58, 33),
            "ct-rifler": (82, 57, 66, 49),
            "ct-awp": (69, 17, 56, 28),
            "ct-support": (88, 43, 72, 52),
        },
    }
    return routes[round_number][player_id]


def _bomb_state(tick: int, round_number: int) -> dict[str, object]:
    if round_number == 2 and tick > 1260:
        return {"status": "planted", "x": 76, "y": 57, "site": "A"}
    return {"status": "carried", "carrierPlayerId": "t-support"}


def _coaching_events(demo_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": str(uuid.uuid4()),
            "demo_id": demo_id,
            "round_number": 1,
            "player_id": "t-entry",
            "player_name": "aimclub.entry",
            "tick_start": 352,
            "tick_end": 432,
            "category": "positioning",
            "severity": "high",
            "title": "Isolated peek without trade spacing",
            "message": (
                "The entry player crossed into contact while the closest teammate was "
                "too far behind to trade. Wait half a beat or call the second player "
                "closer before exposing to the angle."
            ),
            "structured_context_json": {
                "mistake": "Teammate distance was too large to secure a trade.",
                "impact": "The opening death created a 4v5 and surrendered lane control.",
                "recommendation": "Hold the line until the second player can trade or use a flash first.",
            },
            "confidence": 0.86,
        },
        {
            "id": str(uuid.uuid4()),
            "demo_id": demo_id,
            "round_number": 2,
            "player_id": "t-trade",
            "player_name": "aimclub.trade",
            "tick_start": 1064,
            "tick_end": 1168,
            "category": "trading",
            "severity": "medium",
            "title": "Overextended after gaining numbers",
            "message": (
                "After the first kill, the T side had a clear numbers advantage but "
                "kept pushing into isolated fights. Reset spacing, group around the "
                "bomb, and force the CTs to retake into you."
            ),
            "structured_context_json": {
                "mistake": "The team pushed forward after gaining a 5v4 advantage.",
                "impact": "The advantage became tradeable instead of forcing a controlled retake.",
                "recommendation": "Slow the tempo, keep crossfires, and make defenders spend utility.",
            },
            "confidence": 0.78,
        },
        {
            "id": str(uuid.uuid4()),
            "demo_id": demo_id,
            "round_number": 3,
            "player_id": "t-lurk",
            "player_name": "aimclub.lurk",
            "tick_start": 1856,
            "tick_end": 1984,
            "category": "timing",
            "severity": "critical",
            "title": "Risk window 7 seconds before death",
            "message": (
                "The lurk stayed exposed for several seconds after contact timing had "
                "expired. The safer call is to clear one more angle with support or "
                "fall back before the rotate arrives."
            ),
            "structured_context_json": {
                "mistake": "Stayed in a high-risk timing window before the death.",
                "impact": "The death removed late-round map control before the execute.",
                "recommendation": "Reposition 5-10 seconds earlier when the rotate timing becomes dangerous.",
            },
            "confidence": 0.9,
        },
    ]


def _mock_kills() -> list[dict[str, Any]]:
    return [
        {
            "tick": 416,
            "roundNumber": 1,
            "attackerId": "ct-anchor",
            "attackerName": "ct.anchor",
            "attackerSide": "CT",
            "victimId": "t-entry",
            "victimName": "aimclub.entry",
            "victimSide": "T",
            "weapon": "m4a1",
        },
        {
            "tick": 1088,
            "roundNumber": 2,
            "attackerId": "t-trade",
            "attackerName": "aimclub.trade",
            "attackerSide": "T",
            "victimId": "ct-anchor",
            "victimName": "ct.anchor",
            "victimSide": "CT",
            "weapon": "ak47",
        },
        {
            "tick": 1984,
            "roundNumber": 3,
            "attackerId": "ct-rifler",
            "attackerName": "ct.rifler",
            "attackerSide": "CT",
            "victimId": "t-lurk",
            "victimName": "aimclub.lurk",
            "victimSide": "T",
            "weapon": "m4a1",
        },
    ]


def _mock_replay_events() -> list[dict[str, Any]]:
    return [
        {
            "id": "mock-kill-r1",
            "type": "kill",
            "tick": 416,
            "roundNumber": 1,
            "source": "mock",
            "playerIds": ["ct-anchor", "t-entry"],
            "playerId": "ct-anchor",
            "playerName": "ct.anchor",
            "side": "CT",
            "label": "ct.anchor killed aimclub.entry",
            "metadata": {
                "victimId": "t-entry",
                "victimName": "aimclub.entry",
                "weapon": "m4a1",
            },
        },
        {
            "id": "mock-smoke-r1",
            "type": "smoke",
            "tick": 512,
            "roundNumber": 1,
            "source": "mock",
            "playerIds": ["t-support"],
            "playerId": "t-support",
            "playerName": "aimclub.flash",
            "side": "T",
            "x": 42,
            "y": 56,
            "label": "Smoke",
            "metadata": {},
        },
        {
            "id": "mock-bomb-planted-r2",
            "type": "bomb_planted",
            "tick": 1264,
            "roundNumber": 2,
            "source": "mock",
            "playerIds": ["t-support"],
            "playerId": "t-support",
            "playerName": "aimclub.flash",
            "side": "T",
            "x": 76,
            "y": 57,
            "label": "Bomb planted A",
            "metadata": {"site": "A"},
        },
        {
            "id": "mock-kill-r3",
            "type": "kill",
            "tick": 1984,
            "roundNumber": 3,
            "source": "mock",
            "playerIds": ["ct-rifler", "t-lurk"],
            "playerId": "ct-rifler",
            "playerName": "ct.rifler",
            "side": "CT",
            "label": "ct.rifler killed aimclub.lurk",
            "metadata": {
                "victimId": "t-lurk",
                "victimName": "aimclub.lurk",
                "weapon": "m4a1",
            },
        },
    ]


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress
