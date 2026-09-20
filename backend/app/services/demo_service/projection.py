"""Projection of stored replay/render data onto the user-facing contract: strips internal fields and reduces failures to safe codes and copy."""

from typing import Any

from app.services.demo_service.constants import (
    RENDER_CLIP_NOT_CONNECTED_ERROR,
    RENDER_FAILED_ERROR_CODE,
    RENDER_FAILED_PUBLIC_MESSAGE,
    RENDER_QUEUE_TIMED_OUT_ERROR_CODE,
    RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE,
    RENDER_TIMED_OUT_ERROR_CODE,
    RENDER_TIMED_OUT_PUBLIC_MESSAGE,
    RENDER_WORKER_UNAVAILABLE_ERROR_CODE,
)


def _public_render_failure(
    status: str | None,
    error_code: str | None,
    error_message: str | None,
) -> tuple[str | None, str | None]:
    if status != "failed":
        return None, None
    if (
        error_code == RENDER_WORKER_UNAVAILABLE_ERROR_CODE
        or (error_message or "").startswith("GPU worker not connected for render_clip")
    ):
        return RENDER_WORKER_UNAVAILABLE_ERROR_CODE, RENDER_CLIP_NOT_CONNECTED_ERROR
    if (
        error_code == RENDER_TIMED_OUT_ERROR_CODE
        # demo_jobs has no error_code column, so render_job_status can only pass
        # None and the stored message is the sole surviving evidence. It is set
        # from this very constant by fail_render_clip_job, so it round-trips.
        or error_message == RENDER_TIMED_OUT_PUBLIC_MESSAGE
    ):
        return RENDER_TIMED_OUT_ERROR_CODE, RENDER_TIMED_OUT_PUBLIC_MESSAGE
    if (
        error_code == RENDER_QUEUE_TIMED_OUT_ERROR_CODE
        # Same round-trip through the stored message as RENDER_TIMED_OUT above.
        or error_message == RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE
    ):
        return RENDER_QUEUE_TIMED_OUT_ERROR_CODE, RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE
    return RENDER_FAILED_ERROR_CODE, RENDER_FAILED_PUBLIC_MESSAGE


def _public_replay_contract(
    replay: dict[str, Any],
    video: dict[str, Any],
) -> dict[str, object]:
    public: dict[str, object] = {
        "demoId": replay["demoId"],
        "mapName": replay["mapName"],
        "tickRate": replay["tickRate"],
        "video": video,
        "rounds": [
            _project_fields(
                item,
                (
                    "roundNumber",
                    "startTick",
                    "freezeEndTick",
                    "endTick",
                    "winnerSide",
                    "winnerReason",
                ),
            )
            for item in replay.get("rounds", [])
            if isinstance(item, dict)
        ],
        "players": [
            _project_fields(item, ("id", "name", "side", "color"))
            for item in replay.get("players", [])
            if isinstance(item, dict)
        ],
        "frames": [
            _public_replay_frame(item)
            for item in replay.get("frames", [])
            if isinstance(item, dict)
        ],
        "kills": [
            _public_kill(item)
            for item in replay.get("kills", [])
            if isinstance(item, dict)
        ],
        "deaths": [
            _public_kill(item)
            for item in replay.get("deaths", [])
            if isinstance(item, dict)
        ],
        "events": [
            _public_replay_event(item)
            for item in replay.get("events", [])
            if isinstance(item, dict)
        ],
        "generatedAt": replay["generatedAt"],
        "contractVersion": replay["contractVersion"],
        "diagnostics": _public_replay_diagnostics(replay.get("diagnostics")),
    }
    map_metadata = replay.get("mapMetadata")
    if isinstance(map_metadata, dict):
        public["mapMetadata"] = _public_map_metadata(map_metadata)
    return public


def _public_map_metadata(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(
        value,
        (
            "mapName",
            "displayName",
            "radarImagePath",
            "secondaryRadarImagePath",
            "lowerLevelMaxZ",
            "calibrationSource",
            "calibrated",
            "confidence",
            "attribution",
            "source",
        ),
    )
    transform = value.get("transform")
    if isinstance(transform, dict):
        projected["transform"] = _project_fields(
            transform,
            (
                "type",
                "posX",
                "posY",
                "scale",
                "imageSize",
                "minX",
                "maxX",
                "minY",
                "maxY",
            ),
        )
    return projected


def _public_replay_frame(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(value, ("tick", "timeSeconds", "roundNumber"))
    players = value.get("players")
    projected["players"] = [
        _project_fields(
            player,
            ("id", "name", "side", "x", "y", "z", "alive", "hp", "hasBomb"),
        )
        for player in players
        if isinstance(player, dict)
    ] if isinstance(players, list) else []
    bomb_state = value.get("bombState")
    projected["bombState"] = (
        _project_fields(bomb_state, ("status", "carrierPlayerId", "x", "y", "z", "site"))
        if isinstance(bomb_state, dict)
        else {"status": "unknown"}
    )
    return projected


def _public_kill(value: dict[str, Any]) -> dict[str, Any]:
    return _project_fields(
        value,
        (
            "tick",
            "roundNumber",
            "attackerId",
            "attackerName",
            "attackerSide",
            "victimId",
            "victimName",
            "victimSide",
            "assisterId",
            "assisterName",
            "weapon",
            "headshot",
        ),
    )


def _public_replay_event(value: dict[str, Any]) -> dict[str, Any]:
    projected = _project_fields(
        value,
        (
            "id",
            "type",
            "tick",
            "roundNumber",
            "source",
            "playerIds",
            "playerId",
            "playerName",
            "side",
            "x",
            "y",
            "z",
            "label",
        ),
    )
    metadata = value.get("metadata")
    projected["metadata"] = (
        _project_fields(
            metadata,
            (
                "attackerId",
                "attackerName",
                "attackerSide",
                "victimId",
                "victimName",
                "victimSide",
                "assisterId",
                "assisterName",
                "weapon",
                "headshot",
                "damageHealth",
                "damageArmor",
                "health",
                "armor",
                "site",
                "reason",
                "round_end_reason",
                "winner_reason",
                "winnerReason",
                "winnerSide",
            ),
        )
        if isinstance(metadata, dict)
        else {}
    )
    return projected


def _public_replay_diagnostics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected = _project_fields(
        value,
        (
            "contractVersion",
            "normalizedLegacy",
            "parserEventCount",
            "roundCount",
            "playerCount",
            "frameCount",
            "missingFields",
            "degradedFields",
            "missingEventFamilies",
        ),
    )
    family_counts = value.get("eventFamilyCounts")
    projected["eventFamilyCounts"] = (
        {
            key: count
            for key, count in family_counts.items()
            if key in {"combat", "damage", "objective", "utility"}
        }
        if isinstance(family_counts, dict)
        else {}
    )
    return projected


def _project_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: value[field] for field in fields if field in value}
