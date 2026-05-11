from __future__ import annotations

from copy import deepcopy
from typing import Any


def legacy_replay_blob_without_events() -> dict[str, Any]:
    return deepcopy(
        {
            "demoId": "fixture-legacy",
            "mapName": "de_inferno",
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 100, "freezeEndTick": 164, "endTick": 500}],
            "players": [],
            "frames": [],
            "generatedAt": "2026-05-08T00:00:00Z",
        }
    )


def replay_contract_v1_blob_with_parser_events() -> dict[str, Any]:
    return deepcopy(
        {
            "contractVersion": "replay_contract_v1",
            "demoId": "fixture-events",
            "mapName": "de_mirage",
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 100, "freezeEndTick": 164, "endTick": 500}],
            "players": _players(),
            "frames": _frames(),
            "events": [
                {
                    "id": "kill-180",
                    "type": "kill",
                    "tick": 180,
                    "roundNumber": 1,
                    "playerIds": ["t-entry", "ct-anchor"],
                    "playerId": "t-entry",
                    "playerName": "T Entry",
                    "side": "T",
                    "label": "T Entry killed CT Anchor",
                    "metadata": {"victimId": "ct-anchor", "weapon": "ak47"},
                },
                {
                    "id": "plant-a",
                    "type": "bomb_planted",
                    "tick": 220,
                    "roundNumber": 1,
                    "playerIds": ["t-entry"],
                    "playerId": "t-entry",
                    "playerName": "T Entry",
                    "side": "T",
                    "label": "Bomb planted A",
                    "metadata": {"site": "A"},
                },
                {
                    "id": "smoke-execute",
                    "type": "smoke",
                    "tick": 260,
                    "roundNumber": 1,
                    "playerIds": ["t-support"],
                    "playerId": "t-support",
                    "playerName": "T Support",
                    "side": "T",
                    "label": "Smoke",
                    "metadata": {},
                },
                {
                    "id": "flash-entry",
                    "type": "flash",
                    "tick": 280,
                    "roundNumber": 1,
                    "playerIds": ["t-entry"],
                    "playerId": "t-entry",
                    "playerName": "T Entry",
                    "side": "T",
                    "label": "Flash",
                    "metadata": {},
                },
            ],
            "video": {
                "status": "ready",
                "url": None,
                "durationSeconds": 6.25,
                "tickStart": 100,
                "tickEnd": 500,
                "tickRate": 64,
                "source": "mock",
                "errorMessage": None,
                "timeOriginSeconds": 0,
            },
            "generatedAt": "2026-05-08T00:00:00Z",
        }
    )


def malformed_optional_parser_fields() -> dict[str, Any]:
    return deepcopy(
        {
            "mapName": "de_dust2",
            "tickRate": 64,
            "rounds": {"bad": "shape"},
            "players": {"bad": "shape"},
            "kills": {"bad": "shape"},
            "deaths": "not-a-list",
            "events": [
                "not-an-event",
                {"type": "bomb_planted", "tick": "bad", "playerId": "t-entry"},
                {"tick": 180},
                {"type": "smoke", "tick": 220, "roundNumber": 1, "playerIds": "bad"},
            ],
            "frames": [
                {
                    "tick": 128,
                    "roundNumber": 1,
                    "players": [
                        {"id": "t-entry", "name": "T Entry", "side": "T", "x": -100, "y": 40, "hp": 100},
                        {"id": "ct-1", "name": "CT One", "side": "CT", "x": 100, "y": -40, "hp": 100},
                    ],
                },
                {
                    "tick": 256,
                    "roundNumber": 1,
                    "players": [
                        {"id": "t-entry", "name": "T Entry", "side": "T", "x": -80, "y": 35, "hp": 90},
                        {"id": "ct-1", "name": "CT One", "side": "CT", "x": 90, "y": -35, "hp": 100},
                    ],
                },
            ],
        }
    )


def missing_event_families_replay_blob() -> dict[str, Any]:
    replay = replay_contract_v1_blob_with_parser_events()
    replay["demoId"] = "fixture-missing-families"
    replay["events"] = [
        event for event in replay["events"] if event["type"] in {"bomb_planted", "smoke"}
    ]
    return replay


def coaching_evidence_replay_blob() -> dict[str, Any]:
    return deepcopy(
        {
            "contractVersion": "replay_contract_v1",
            "demoId": "fixture-coaching-evidence",
            "mapName": "de_mirage",
            "tickRate": 64,
            "rounds": [{"roundNumber": 1, "startTick": 0, "freezeEndTick": 64, "endTick": 1400}],
            "players": _players(),
            "frames": [
                _frame(800, t_entry=(40, 50), t_support=(44, 52), ct_anchor=(70, 40)),
                _frame(1200, t_entry=(55, 52), t_support=(47, 53), ct_anchor=(72, 42)),
            ],
            "kills": [],
            "deaths": [],
            "events": [
                {
                    "id": "smoke-execute",
                    "type": "smoke",
                    "tick": 900,
                    "roundNumber": 1,
                    "source": "parser",
                    "playerIds": ["t-support"],
                    "playerId": "t-support",
                    "playerName": "T Support",
                    "side": "T",
                    "label": "Smoke",
                    "metadata": {},
                },
                {
                    "id": "plant-a",
                    "type": "bomb_planted",
                    "tick": 1200,
                    "roundNumber": 1,
                    "source": "parser",
                    "playerIds": ["t-entry"],
                    "playerId": "t-entry",
                    "playerName": "T Entry",
                    "side": "T",
                    "label": "Bomb planted A",
                    "metadata": {"site": "A"},
                },
            ],
            "video": {
                "status": "pending",
                "url": None,
                "durationSeconds": 21.88,
                "tickStart": 0,
                "tickEnd": 1400,
                "tickRate": 64,
                "source": "mock",
                "errorMessage": None,
                "timeOriginSeconds": 0,
            },
            "generatedAt": "2026-05-08T00:00:00Z",
        }
    )


def _players() -> list[dict[str, Any]]:
    return [
        {"id": "t-entry", "name": "T Entry", "side": "T", "color": "#f5b542"},
        {"id": "t-support", "name": "T Support", "side": "T", "color": "#f5b542"},
        {"id": "ct-anchor", "name": "CT Anchor", "side": "CT", "color": "#2ed3d0"},
    ]


def _frames() -> list[dict[str, Any]]:
    return [
        _frame(100, t_entry=(30, 60), t_support=(34, 62), ct_anchor=(70, 36)),
        _frame(320, t_entry=(52, 54), t_support=(42, 58), ct_anchor=(72, 38)),
    ]


def _frame(
    tick: int,
    *,
    t_entry: tuple[float, float],
    t_support: tuple[float, float],
    ct_anchor: tuple[float, float],
) -> dict[str, Any]:
    return {
        "tick": tick,
        "timeSeconds": round(tick / 64, 2),
        "roundNumber": 1,
        "players": [
            _frame_player("t-entry", "T Entry", "T", *t_entry),
            _frame_player("t-support", "T Support", "T", *t_support),
            _frame_player("ct-anchor", "CT Anchor", "CT", *ct_anchor),
        ],
        "bombState": {"status": "carried", "carrierPlayerId": "t-entry"},
    }


def _frame_player(
    player_id: str,
    name: str,
    side: str,
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "id": player_id,
        "name": name,
        "side": side,
        "x": x,
        "y": y,
        "alive": True,
        "hp": 100,
        "hasBomb": player_id == "t-entry",
    }
