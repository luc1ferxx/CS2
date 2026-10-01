"""Render the tactical-map radar images from the operator's own CS2 install.

Reads each supported map's navigation mesh (``maps/<map>.nav`` inside
``game/csgo/maps/<map>.vpk``) and draws the walkable floor with exactly the
transform the app projects player positions with (``backend/app/parser/
map_config.py``), so replay dots land on the drawn floor by construction. The
bomb sites are the map's own ``func_bomb_target`` triggers: their entries in the
compiled entity lump (``maps/<map>/entities/*.vents_c``) and the collision hulls
of the brush models they reference (the PHYS block of
``maps/<map>/entities/*.vmdl_c``); the height a player stands at inside them
comes from the map's world collision (the PHYS block of
``maps/<map>/world_physics.vmdl_c``). All three are binary KeyValues3, decoded
with the ``keyvalues3`` package. No Valve texture, radar image or font is read
or shipped, and the game install is only ever read.

    python scripts/maps/build_radars.py --cs2 "C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive"

Output is deterministic for a given install and the pinned requirements.

The nav mesh layout follows awpy's ``awpy/nav.py`` (itself a port of
ValveResourceFormat's NavMesh reader), extended here for the version 36 files
current CS2 ships, which put a KV3 blob before the polygon and the area sections:

    MIT License

    Copyright (c) 2020-2025 Peter Xenopoulos

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import re
import struct
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyvalues3
import numpy as np
import vpk
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import MultiPoint, MultiPolygon, Polygon
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.parser.map_config import (  # noqa: E402  (needs the backend on sys.path)
    SUPPORTED_MAP_NAMES,
    get_map_config,
    transform_world_to_percent,
)

OUTPUT_DIR = REPO_ROOT / "frontend" / "public" / "maps"
IMAGE_SIZE = 1024
SUPERSAMPLE = 4

# Palette: the app's radar canvas (--radar-bg in globals.css), slate floor
# lightening with height, --radar-text-2 outlines, and the site red.
BACKGROUND = (0x1D, 0x27, 0x32)
FLOOR_LOW = (0x34, 0x41, 0x4F)
FLOOR_HIGH = (0x5E, 0x6E, 0x80)
OUTLINE = (0xA9, 0xB4, 0xC0)
OBSTACLE = (0x6F, 0x7E, 0x8E)
OPENING = (0x28, 0x33, 0x40)  # darker than any floor: a hatch or drop to the floor below
SILHOUETTE = (0x25, 0x30, 0x3C)  # FLOOR_LOW at 35% over BACKGROUND: the other floor of a multi-level map
LEDGE = (0x1D, 0x27, 0x32)
LEDGE_ALPHA = 0.5
LEDGE_MIN_STEP = 40.0  # world units of height between neighbouring pixels
SITE = (0xC9, 0x3A, 0x32)
SITE_ALPHA = 0.35
SITE_LABEL = (0xF6, 0xDD, 0xDA)
SITE_LABEL_STROKE = (0x33, 0x1A, 0x1C)  # dark site red, so the letter never punches a background-coloured hole in the floor
SITE_LABEL_SIZE = 36  # px on the 1024 image, so A / B stay legible when the radar is phone-sized
PLAYER_HEIGHT = 72.0  # world units: a standing player's box reaches a trigger whose bottom is this far above their feet
HULL_RADIUS = 16.0  # world units: a player's half-width; nav areas stop this far short of walls
STANDABLE_NORMAL_Z = 0.7  # a collision face this flat or flatter can be stood on (CS2's steepest walkable slope)
STEP_HEIGHT = 18.0  # world units: CS2 lifts a player's box onto anything up to this much higher (sv_stepsize)
# How far a nav area may lie from the collision face its players stand on. Anubis A
# needs at least 22; areas on Inferno's B fountain rim reach out over the floor 32
# below, so it must stay under that (22, 24 and 30 draw the same images; 20 and 32 change the tint).
# A nav area with no face this close stands on an entity with its own collision (a
# func_brush box), which is not read, and its own height is used there.
NAV_FLOOR_TOLERANCE = 24.0
OBSTACLE_MAX_AREA = 256.0 * 256.0  # world units squared: enclosed holes up to this are drawn as obstacles
OPENING_MIN_SHARE = 0.25  # a small hole this much over a lower floor is an opening (hatch, drop), not an obstacle

NAV_MAGIC = 0xFEEDFACE
KV3_V5_MAGIC = b"\x053VK"
KV3_V5_HEADER_SIZE = 120
KV3_TRAILER = b"\x00\xdd\xee\xff"


class NavFormatError(ValueError):
    pass


@dataclass(frozen=True)
class NavArea:
    area_id: int
    corners: tuple[tuple[float, float, float], ...]

    @property
    def mean_z(self) -> float:
        return sum(corner[2] for corner in self.corners) / len(self.corners)

    @property
    def min_z(self) -> float:
        return min(corner[2] for corner in self.corners)

    @property
    def max_z(self) -> float:
        return max(corner[2] for corner in self.corners)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        values = struct.unpack_from("<" + fmt, self.data, self.offset)
        self.offset += struct.calcsize("<" + fmt)
        return values

    def u8(self) -> int:
        return int(self.unpack("B")[0])

    def u32(self) -> int:
        return int(self.unpack("I")[0])

    def skip_kv3(self) -> None:
        start = self.offset
        if self.data[start : start + 4] != KV3_V5_MAGIC:
            raise NavFormatError(f"expected a KV3 v5 blob at byte {start}")
        (total_size,) = struct.unpack_from("<I", self.data, start + 52)
        end = start + KV3_V5_HEADER_SIZE + total_size
        if self.data[end - 4 : end] != KV3_TRAILER:
            raise NavFormatError(f"KV3 blob at byte {start} has no trailer")
        self.offset = end

    def skip_zero_padding_to_kv3(self, limit: int = 64) -> None:
        found = self.data.find(KV3_V5_MAGIC, self.offset, self.offset + limit)
        if found < 0 or any(self.data[self.offset : found]):
            raise NavFormatError(f"unexpected data before the area section at byte {self.offset}")
        self.offset = found


def read_nav(data: bytes) -> list[NavArea]:
    """Walkable areas of a CS2 nav mesh (versions 35 and 36)."""
    reader = _Reader(data)
    magic, version, _sub_version, _flags = reader.unpack("4I")
    if magic != NAV_MAGIC:
        raise NavFormatError(f"not a nav mesh (magic {magic:#x})")
    if version not in (35, 36):
        raise NavFormatError(f"unsupported nav version {version}")
    if version >= 36:
        reader.skip_kv3()

    corners = [reader.unpack("3f") for _ in range(reader.u32())]
    polygons: list[tuple[tuple[float, float, float], ...]] = []
    for _ in range(reader.u32()):
        count = reader.u8()
        polygons.append(tuple(corners[reader.u32()] for _ in range(count)))
        reader.u32()  # per-polygon value, unused
    if version >= 36:
        reader.skip_zero_padding_to_kv3()
        reader.skip_kv3()
    else:
        reader.unpack("2I")

    areas: list[NavArea] = []
    for _ in range(reader.u32()):
        area_id = reader.u32()
        reader.unpack("qB")  # dynamic attribute flags, hull index
        polygon = polygons[reader.u32()]
        reader.u32()
        for _ in polygon:  # per edge: connections as (neighbour id, edge) pairs
            reader.unpack(f"{2 * reader.u32()}I")
        reader.unpack("BI")  # legacy hiding spot / encounter counts
        for _ in range(2):  # ladders above, ladders below
            reader.unpack(f"{reader.u32()}I")
        if len(polygon) >= 3:
            areas.append(NavArea(area_id, polygon))
    if len({area.area_id for area in areas}) != len(areas):
        raise NavFormatError("duplicate nav area ids")
    return areas


def csgo_dir(install: Path) -> Path:
    for candidate in (install, install / "game" / "csgo"):
        if (candidate / "maps").is_dir():
            return candidate
    raise SystemExit(f"{install} is not a CS2 install (expected game/csgo/maps)")


def open_map_package(game: Path, map_name: str) -> vpk.VPK:
    package_path = game / "maps" / f"{map_name}.vpk"
    if not package_path.is_file():
        raise SystemExit(f"{package_path} is missing; is {map_name} installed?")
    return vpk.open(str(package_path))


def read_map_nav(game: Path, map_name: str) -> bytes:
    return bytes(open_map_package(game, map_name).get_file(f"maps/{map_name}.nav").read())


class ResourceFormatError(ValueError):
    pass


def resource_blocks(data: bytes) -> dict[str, bytes]:
    """The blocks of a compiled Source 2 resource (``*_c``) by type, such as DATA or PHYS."""
    if len(data) < 16:
        raise ResourceFormatError("too short for a resource header")
    _file_size, _header_version, _version, block_offset, block_count = struct.unpack_from("<IHHII", data)
    blocks: dict[str, bytes] = {}
    entry = 8 + block_offset  # offsets count from the field that holds them
    for _ in range(block_count):
        kind = data[entry : entry + 4].decode("ascii", "replace")
        offset, size = struct.unpack_from("<II", data, entry + 4)
        start = entry + 4 + offset
        if start + size > len(data):
            raise ResourceFormatError(f"{kind} block runs past the end of the resource")
        blocks[kind] = data[start : start + size]
        entry += 12
    return blocks


def read_kv3_block(data: bytes, kind: str) -> Any:
    """The binary KeyValues3 document in one block of a compiled resource."""
    block = resource_blocks(data).get(kind)
    if block is None:
        raise ResourceFormatError(f"resource has no {kind} block")
    return keyvalues3.read(io.BytesIO(block)).value


def _plain(value: Any) -> Any:
    """A KV3 value without its flag (resource names and the like come wrapped)."""
    return getattr(value, "value", value)


@dataclass(frozen=True)
class BombZone:
    """One convex piece of a ``func_bomb_target`` trigger, in world units.

    A player is in the bomb zone while their box (HULL_RADIUS each side of their
    position, PLAYER_HEIGHT tall) touches the trigger, so the places a player
    can plant from are ``reach`` in plan, on floors from ``floor_low`` up to
    ``max_z``.
    """

    footprint: Polygon  # the trigger's outline in plan
    min_z: float
    max_z: float

    @property
    def reach(self) -> Polygon:
        """The footprint grown by the player's box: exact, since the footprint is convex."""
        corners = [
            (x + dx, y + dy)
            for x, y in self.footprint.exterior.coords
            for dx in (-HULL_RADIUS, HULL_RADIUS)
            for dy in (-HULL_RADIUS, HULL_RADIUS)
        ]
        return MultiPoint(corners).convex_hull

    @property
    def floor_low(self) -> float:
        """The lowest floor a standing player still reaches the trigger from."""
        return self.min_z - PLAYER_HEIGHT


@dataclass(frozen=True)
class Bombsite:
    label: str
    zones: tuple[BombZone, ...]


# func_bomb_target's bomb_site_designation choices.
SITE_DESIGNATIONS = {"0": "A", "1": "B"}
OVERVIEW_IMAGE_SIZE = 1024  # Valve's overview icon positions are fractions of a 1024 px image


def overview_icons(game: Path, map_name: str) -> dict[str, tuple[float, float]]:
    """World positions of the A / B icons on Valve's loading-screen overview, when it has them."""
    package = vpk.open(str(game / "pak01_dir.vpk"))
    try:
        text = package.get_file(f"resource/overviews/{map_name}.txt").read().decode("utf-8", "replace")
    except KeyError:
        return {}
    values: dict[str, float] = {}
    for key, value in re.findall(r'"(\w+)"\s+"([-+0-9.eE]+)"', text):
        values.setdefault(key.lower(), float(value))
    if not {"pos_x", "pos_y", "scale"} <= values.keys():
        return {}
    span = OVERVIEW_IMAGE_SIZE * values["scale"]
    return {
        label: (values["pos_x"] + values[f"bomb{label.lower()}_x"] * span, values["pos_y"] - values[f"bomb{label.lower()}_y"] * span)
        for label in ("A", "B")
        if f"bomb{label.lower()}_x" in values and f"bomb{label.lower()}_y" in values
    }


def _entities(package: vpk.VPK, lump_path: str) -> Iterator[dict[str, Any]]:
    """Key values of every entity in a compiled entity lump and its child lumps."""
    lump = read_kv3_block(bytes(package.get_file(lump_path).read()), "DATA")
    for entity in lump.get("m_entityKeyValues", []):
        data = entity.get("keyValues3Data")
        if not isinstance(data, dict):
            raise ResourceFormatError(f"{lump_path}: entity without KV3 key values (older lump format)")
        yield data["values"]
    for child in lump.get("m_childLumps", []):
        yield from _entities(package, f"{_plain(child)}_c")


def _trigger_zones(package: vpk.VPK, values: dict[str, Any]) -> Iterator[BombZone]:
    """The convex collision hulls of a brush trigger's model, placed in the world."""
    model = str(_plain(values.get("model", "")))
    if not model:
        raise ResourceFormatError(f"func_bomb_target {values.get('hammerUniqueId')} has no model")
    if _plain(values.get("parentname")) or any(float(angle) for angle in values.get("angles", (0, 0, 0))):
        raise ResourceFormatError(f"{model}: parented or rotated bomb targets are not supported")
    origin = np.asarray(values.get("origin", (0, 0, 0)), np.float64)
    scales = np.asarray(values.get("scales", (1, 1, 1)), np.float64)
    physics = read_kv3_block(bytes(package.get_file(f"{model}_c").read()), "PHYS")
    if physics.get("m_bindPose"):
        raise ResourceFormatError(f"{model}: physics with bones is not supported")
    for part in physics["m_parts"]:
        shape = part["m_rnShape"]
        for kind in ("m_spheres", "m_capsules", "m_meshes"):
            if shape.get(kind):
                raise ResourceFormatError(f"{model}: {kind[2:]} collision shapes are not supported")
        for hull in shape["m_hulls"]:
            positions = hull["m_Hull"]["m_VertexPositions"]
            if not isinstance(positions, bytes | bytearray) or not positions or len(positions) % 12:
                raise ResourceFormatError(f"{model}: unexpected hull vertex data")
            points = np.frombuffer(positions, "<f4").reshape(-1, 3) * scales + origin
            footprint = MultiPoint([(float(x), float(y)) for x, y in points[:, :2]]).convex_hull
            if not isinstance(footprint, Polygon) or footprint.area <= 0:
                raise ResourceFormatError(f"{model}: hull has no footprint")
            yield BombZone(footprint, float(points[:, 2].min()), float(points[:, 2].max()))


def read_bombsites(game: Path, map_name: str) -> list[Bombsite]:
    """The map's bomb sites: every func_bomb_target trigger, grouped by its site letter.

    The letter is the entity's ``bomb_site_designation``; when Valve's overview
    has A / B icons, each site must lie nearest its own letter's icon, which
    guards against reading the designation wrongly.
    """
    package = open_map_package(game, map_name)
    zones: dict[str, list[BombZone]] = {}
    for values in _entities(package, f"maps/{map_name}/entities/default_ents.vents_c"):
        if values.get("classname") != "func_bomb_target":
            continue
        designation = str(_plain(values.get("bomb_site_designation", "0")))  # absent: the entity default, 0
        if designation not in SITE_DESIGNATIONS:
            raise ResourceFormatError(f"func_bomb_target {values.get('hammerUniqueId')}: designation {designation!r}")
        zones.setdefault(SITE_DESIGNATIONS[designation], []).extend(_trigger_zones(package, values))
    if not zones:
        raise SystemExit(f"{map_name} has no func_bomb_target")
    sites = [Bombsite(label, tuple(found)) for label, found in sorted(zones.items())]
    icons = overview_icons(game, map_name)
    for site in sites:
        centre = unary_union([zone.footprint for zone in site.zones]).centroid
        nearest = _nearest_icon(icons, centre.x, centre.y)
        if nearest not in (None, site.label):
            raise SystemExit(f"{map_name}: bomb site {site.label} lies nearest Valve's {nearest} overview icon")
    return sites


def _nearest_icon(icons: dict[str, tuple[float, float]], x: float, y: float) -> str | None:
    return min(icons, key=lambda label: (math.dist(icons[label], (x, y)), label)) if icons else None


# Collision layers (a collision attribute's m_InteractAsStrings). A shape with no layer
# is plain solid world; these layers are solid to players too.
PLAYER_SOLID_LAYERS = frozenset({"solid", "playerclip", "passbullets", "window"})
# These only clip NPCs, the nav generator, grenades or the sky, mark ladders, or block
# light, sound or line of sight; on their own they do not stop a player.
PLAYER_PASSABLE_LAYERS = frozenset({"npcclip", "navclip", "csgo_grenadeclip", "sky", "ladder", "blocklight", "blocksound", "blocklos"})


def _names(values: Iterable[Any]) -> set[str]:
    return {str(_plain(value)).lower() for value in values}


def _player_solid(attribute: dict[str, Any]) -> bool:
    layers = _names(attribute.get("m_InteractAsStrings", []))
    unknown = layers - PLAYER_SOLID_LAYERS - PLAYER_PASSABLE_LAYERS
    if unknown:
        raise ResourceFormatError(f"collision layer(s) {sorted(unknown)}: not known to be solid or passable for players")
    return "player" not in _names(attribute.get("m_InteractExcludeStrings", [])) and (not layers or bool(layers & PLAYER_SOLID_LAYERS))


def _hull_faces(hull: dict[str, Any]) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Each face of a convex collision hull as (vertex loop, outward unit normal)."""
    positions = np.frombuffer(hull["m_VertexPositions"], "<f4").reshape(-1, 3).astype(np.float64)
    edges = np.frombuffer(hull["m_Edges"], np.uint8).reshape(-1, 4)  # half-edges: next, twin, origin vertex, face
    faces = np.frombuffer(hull["m_Faces"], np.uint8)  # each face's first half-edge
    planes = np.frombuffer(hull["m_Planes"], "<f4").reshape(-1, 4).astype(np.float64)  # each face's normal and offset
    if len(planes) != len(faces):
        raise ResourceFormatError("hull planes do not match its faces")
    for face, first in enumerate(faces):
        loop: list[int] = []
        edge = int(first)
        while True:
            if edge >= len(edges) or edges[edge, 3] != face or len(loop) > len(edges):
                raise ResourceFormatError("malformed hull face")
            loop.append(int(edges[edge, 2]))
            edge = int(edges[edge, 0])
            if edge == first:
                break
        vertices = positions[loop]
        if np.abs(vertices @ planes[face, :3] - planes[face, 3]).max() > 0.5:
            raise ResourceFormatError("hull face does not lie on its plane")
        yield vertices, planes[face, :3]


@dataclass(frozen=True)
class WalkableSurfaces:
    """The faces of a map's world collision a player can stand on.

    ``triangles`` are (n, 3, 3) world positions; ``rounds`` are the bounds
    (min x, min y, max x, max y, lowest, highest) of the standable tops of
    player-solid spheres and capsules, which are not turned into faces: a site
    near one stops the build (zone_stand_mask).
    """

    triangles: np.ndarray
    rounds: tuple[tuple[float, float, float, float, float, float], ...]


def _round_top(kind: str, shape: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    """Bounds of the part of a sphere or capsule whose surface is flat enough to stand on."""
    body = shape["m_Sphere"] if kind == "m_spheres" else shape["m_Capsule"]
    centres = np.asarray([body["m_vCenter"]] if kind == "m_spheres" else body["m_vCenter"], np.float64)
    radius = float(body["m_flRadius"])
    axis = centres[-1] - centres[0]
    steep = np.linalg.norm(axis) > 0 and abs(axis[2]) / np.linalg.norm(axis) > math.sqrt(1 - STANDABLE_NORMAL_Z**2)
    top, bottom = float(centres[:, 2].max()), float(centres[:, 2].min())
    lowest = (top if steep or kind == "m_spheres" else bottom) + STANDABLE_NORMAL_Z * radius
    (min_x, min_y), (max_x, max_y) = centres[:, :2].min(axis=0) - radius, centres[:, :2].max(axis=0) + radius
    return float(min_x), float(min_y), float(max_x), float(max_y), lowest, top + radius


def read_walkable_surfaces(game: Path, map_name: str) -> WalkableSurfaces:
    """Every face of the map's world collision (``maps/<map>/world_physics.vmdl_c``, PHYS) a player stands on.

    Triangle meshes and convex hulls whose collision attribute is solid to players,
    keeping the faces that point up at most as steep as STANDABLE_NORMAL_Z allows.
    Entities with their own collision (func_brush and the like) are not read.
    """
    physics = read_kv3_block(bytes(open_map_package(game, map_name).get_file(f"maps/{map_name}/world_physics.vmdl_c").read()), "PHYS")
    if physics.get("m_bindPose"):
        raise ResourceFormatError(f"{map_name}: world physics with bones is not supported")
    solid = [_player_solid(attribute) for attribute in physics["m_collisionAttributes"]]
    pieces: list[np.ndarray] = []
    rounds = []
    for part in physics["m_parts"]:
        shape = part["m_rnShape"]
        if shape.get("m_compounds"):
            raise ResourceFormatError(f"{map_name}: compound collision shapes are not supported")
        for mesh in shape["m_meshes"]:
            if solid[mesh["m_nCollisionAttributeIndex"]]:
                vertices = np.frombuffer(mesh["m_Mesh"]["m_Vertices"], "<f4").reshape(-1, 3).astype(np.float64)
                pieces.append(vertices[np.frombuffer(mesh["m_Mesh"]["m_Triangles"], "<i4").reshape(-1, 3)])
        for hull in shape["m_hulls"]:
            if solid[hull["m_nCollisionAttributeIndex"]]:
                for loop, normal in _hull_faces(hull["m_Hull"]):
                    if normal[2] >= STANDABLE_NORMAL_Z:  # fan out the (convex) face
                        pieces.append(np.stack([np.repeat(loop[:1], len(loop) - 2, axis=0), loop[1:-1], loop[2:]], axis=1))
        for kind in ("m_spheres", "m_capsules"):
            rounds.extend(_round_top(kind, item) for item in shape.get(kind, []) if solid[item["m_nCollisionAttributeIndex"]])
    triangles = np.concatenate(pieces)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    return WalkableSurfaces(triangles[(lengths > 0) & (normals[:, 2] >= STANDABLE_NORMAL_Z * lengths)], tuple(rounds))


def _nothing(area: NavArea) -> bool:
    return False


def _everywhere(zone: BombZone) -> bool:
    return True


@dataclass(frozen=True)
class Level:
    name: str
    image_path: str
    includes: Callable[[NavArea], bool]
    # Areas of a floor under this one: small holes over them are openings, not obstacles.
    below: Callable[[NavArea], bool] = _nothing
    # Areas of another floor, drawn as a flat faded silhouette under this one for orientation.
    context: Callable[[NavArea], bool] = _nothing
    # Bomb trigger pieces a player standing on this floor can reach.
    holds: Callable[[BombZone], bool] = _everywhere


def levels(config: dict[str, Any]) -> list[Level]:
    secondary = config.get("secondaryRadarImagePath")
    threshold = config.get("lowerLevelMaxZ")
    if not secondary or threshold is None:
        return [Level("all", config["radarImagePath"], lambda area: True)]
    limit = float(threshold)
    # Same rule as the viewer (z <= lowerLevelMaxZ is the lower floor); ramps and
    # stairs that cross the threshold are drawn on both floors.
    return [
        Level(
            "upper",
            config["radarImagePath"],
            lambda area: area.max_z > limit,
            below=lambda area: area.max_z <= limit,
            holds=lambda zone: zone.max_z > limit,
        ),
        Level(
            "lower",
            secondary,
            lambda area: area.min_z <= limit,
            context=lambda area: area.max_z > limit,
            holds=lambda zone: zone.floor_low <= limit,
        ),
    ]


def to_pixels(transform: dict[str, Any], points: Iterable[tuple[float, float]], size: int) -> list[tuple[float, float]]:
    pixels = []
    for x, y in points:
        percent = transform_world_to_percent(transform, x, y)
        if percent is None:
            raise ValueError("map transform is not usable")
        pixels.append((percent[0] / 100 * size, percent[1] / 100 * size))
    return pixels


def rasterize(areas: list[NavArea], transform: dict[str, Any], size: int) -> np.ndarray:
    """Index image: 0 for void, else 1 + the position of the topmost area in ``areas``.

    Areas are painted in list order, so pass them sorted by height to get a
    top-down view where upper floors cover what is under them.
    """
    canvas = Image.new("I", (size, size), 0)
    draw = ImageDraw.Draw(canvas)
    for index, area in enumerate(areas, start=1):
        draw.polygon(to_pixels(transform, ((x, y) for x, y, _ in area.corners), size), fill=index)
    return np.asarray(canvas, dtype=np.int32)


def _plan(area: NavArea) -> Polygon:
    return Polygon([(x, y) for x, y, _ in area.corners]).buffer(0)


def floor_outline(
    areas: list[NavArea], below: list[NavArea] | None = None
) -> tuple[list[Polygon], list[Polygon], list[Polygon]]:
    """The floor in world units with every hole cut out, and the small holes to fill.

    Nav areas stop HULL_RADIUS short of walls (they hold where a player's origin
    can be), so the floor is grown back by that much. Enclosed holes up to
    OBSTACLE_MAX_AREA are either obstacles (boxes, crates, pillars: no nav area
    exists there at any height), drawn solid, or, when they lie mostly over
    ``below`` (a lower floor of the same map), openings such as Nuke's A hatch,
    drawn darker than any floor. Larger holes stay empty.
    """
    grown = unary_union([_plan(area) for area in areas]).buffer(HULL_RADIUS, join_style="mitre", mitre_limit=2.0)
    under = unary_union([_plan(area) for area in below]) if below else None
    parts = list(grown.geoms) if isinstance(grown, MultiPolygon) else [grown]
    floors: list[Polygon] = []
    obstacles: list[Polygon] = []
    openings: list[Polygon] = []
    for part in sorted(parts, key=lambda polygon: (-polygon.area, polygon.bounds)):
        holes = [Polygon(ring) for ring in part.interiors]
        for hole in holes:
            if hole.area > OBSTACLE_MAX_AREA:
                continue
            is_opening = under is not None and hole.intersection(under).area >= OPENING_MIN_SHARE * hole.area
            (openings if is_opening else obstacles).append(hole)
        floors.append(Polygon(part.exterior, [hole.exterior for hole in holes]))
    return floors, obstacles, openings


def rasterize_polygons(polygons: list[Polygon], transform: dict[str, Any], size: int) -> np.ndarray:
    """Mask of the polygons with their holes; pass larger polygons first so islands inside holes survive."""
    canvas = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        draw.polygon(to_pixels(transform, polygon.exterior.coords, size), fill=255)
        for ring in polygon.interiors:
            draw.polygon(to_pixels(transform, ring.coords, size), fill=0)
    return np.asarray(canvas) > 0


_NEIGHBOURS_4 = ((0, 1), (0, -1), (1, 0), (-1, 0))
_NEIGHBOURS_8 = (*_NEIGHBOURS_4, (1, 1), (1, -1), (-1, 1), (-1, -1))


def spread_index(index: np.ndarray, target: np.ndarray, max_steps: int) -> np.ndarray:
    """Give pixels of ``target`` that no area covers the index of a nearby area, growing outwards."""
    spread = index.copy()
    height, width = spread.shape
    for step in range(max_steps):
        missing = target & (spread == 0)
        if not missing.any():
            break
        grown = spread.copy()
        for dy, dx in _NEIGHBOURS_4 if step % 2 else _NEIGHBOURS_8:
            shifted = np.zeros_like(spread)
            shifted[max(dy, 0) : height + min(dy, 0), max(dx, 0) : width + min(dx, 0)] = spread[
                max(-dy, 0) : height + min(-dy, 0), max(-dx, 0) : width + min(-dx, 0)
            ]
            take = missing & (grown == 0) & (shifted > 0)
            grown[take] = shifted[take]
        spread = grown
    return np.where(target, spread, 0)


def area_plane(area: NavArea, transform: dict[str, Any], size: int) -> np.ndarray:
    """Best-fit plane of an area's corners in pixel space: height = p[0] * (column + 0.5) + p[1] * (row + 0.5) + p[2]."""
    pixels = np.array(to_pixels(transform, ((x, y) for x, y, _ in area.corners), size))
    design = np.column_stack([pixels, np.ones(len(pixels))])
    return np.linalg.lstsq(design, np.array([corner[2] for corner in area.corners]), rcond=None)[0]


def height_map(areas: list[NavArea], index: np.ndarray, transform: dict[str, Any]) -> np.ndarray:
    """World height per pixel from the best-fit plane of the pixel's area, NaN on void."""
    size = index.shape[0]
    planes = np.full((len(areas) + 1, 5), np.nan)
    for number, area in enumerate(areas, start=1):
        planes[number, :3] = area_plane(area, transform, size)
        planes[number, 3:] = (area.min_z, area.max_z)
    rows, cols = np.ogrid[0:size, 0:size]
    plane = planes[index]
    heights = plane[..., 0] * (cols + 0.5) + plane[..., 1] * (rows + 0.5) + plane[..., 2]
    return np.clip(heights, plane[..., 3], plane[..., 4])


def _filtered(mask: np.ndarray, image_filter: ImageFilter.Filter) -> np.ndarray:
    return np.asarray(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).filter(image_filter)) > 0


def _world_units_per_pixel(transform: dict[str, Any], size: int) -> float:
    (x0, _), (x1, _) = to_pixels(transform, [(0.0, 0.0), (1000.0, 0.0)], size)
    return 1000.0 / abs(x1 - x0)


def visual_centre(mask: np.ndarray, clearance: int) -> tuple[float, float]:
    """Pixel position for a label on ``mask`` with its enclosed holes (a pillar, a raised
    fountain) filled in: nearest the centroid among the pixels at least ``clearance``
    pixels inside, or among the deepest pixels if none is that deep."""
    rows, cols = np.nonzero(mask)
    top, left = int(rows.min()) - 1, int(cols.min()) - 1
    crop = np.zeros((int(rows.max()) - top + 2, int(cols.max()) - left + 2), np.uint8)  # one empty pixel all round
    crop[rows - top, cols - left] = 255
    # frombytes, not fromarray: floodfill silently does nothing on a read-only image.
    outside = Image.frombytes("L", (crop.shape[1], crop.shape[0]), crop.tobytes())
    ImageDraw.floodfill(outside, (0, 0), 128)
    inside = np.asarray(outside) != 128
    inside[[0, -1], :] = inside[:, [0, -1]] = False  # so the erosion below always ends
    filled_rows, filled_cols = np.nonzero(inside)
    centre_row, centre_col = filled_rows.mean(), filled_cols.mean()
    depth = np.zeros(inside.shape, np.int32)
    step = 0
    while inside.any():
        depth += inside
        eroded = inside.copy()
        for dy, dx in _NEIGHBOURS_4 if step % 2 else _NEIGHBOURS_8:
            eroded &= np.roll(inside, (dy, dx), axis=(0, 1))
        inside = eroded
        step += 1
    deep_rows, deep_cols = np.nonzero(depth >= min(clearance, int(depth.max())))
    best = int(np.argmin((deep_rows - centre_row) ** 2 + (deep_cols - centre_col) ** 2))
    return float(deep_cols[best] + left) + 0.5, float(deep_rows[best] + top) + 0.5


def _pixel_affine(transform: dict[str, Any], size: int) -> tuple[float, float, float, float]:
    """(a, b, c, d) with pixel column = a * x + b and pixel row = c * y + d; the transforms are axis-aligned and linear."""
    (col0, row0), (col1, row1) = to_pixels(transform, [(0.0, 0.0), (1024.0, 1024.0)], size)
    return (col1 - col0) / 1024.0, col0, (row1 - row0) / 1024.0, row0


class SurfaceStack:
    """The walkable faces over a window of the (supersampled) image: each face's height at each pixel centre it covers,
    kept when within ``z_range``."""

    def __init__(
        self, surfaces: WalkableSurfaces, transform: dict[str, Any], size: int, window: tuple[int, int, int, int], z_range: tuple[float, float]
    ) -> None:
        top, left, bottom, right = window
        self.top, self.left, self.width = top, left, right - left
        low, high = z_range
        a, b, c, d = _pixel_affine(transform, size)
        cols = surfaces.triangles[:, :, 0] * a + b
        rows = surfaces.triangles[:, :, 1] * c + d
        heights = surfaces.triangles[:, :, 2]
        near = (
            (cols.max(axis=1) >= left) & (cols.min(axis=1) <= right) & (rows.max(axis=1) >= top) & (rows.min(axis=1) <= bottom)
            & (heights.max(axis=1) >= low) & (heights.min(axis=1) <= high)
        )
        pixels: list[np.ndarray] = []
        values: list[np.ndarray] = []
        for (xa, xb, xc), (ya, yb, yc), (za, zb, zc) in zip(cols[near], rows[near], heights[near], strict=True):
            det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
            col_lo, col_hi = max(math.ceil(min(xa, xb, xc) - 0.5), left), min(math.floor(max(xa, xb, xc) - 0.5), right - 1)
            row_lo, row_hi = max(math.ceil(min(ya, yb, yc) - 0.5), top), min(math.floor(max(ya, yb, yc) - 0.5), bottom - 1)
            if det == 0 or col_lo > col_hi or row_lo > row_hi:
                continue
            grid_rows, grid_cols = np.mgrid[row_lo : row_hi + 1, col_lo : col_hi + 1]
            px, py = grid_cols + 0.5, grid_rows + 0.5
            w1 = ((yb - yc) * (px - xc) + (xc - xb) * (py - yc)) / det
            w2 = ((yc - ya) * (px - xc) + (xa - xc) * (py - yc)) / det
            w3 = 1.0 - w1 - w2
            height = w1 * za + w2 * zb + w3 * zc
            keep = (w1 >= -1e-9) & (w2 >= -1e-9) & (w3 >= -1e-9) & (height >= low) & (height <= high)
            pixels.append((grid_rows[keep] - top) * self.width + (grid_cols[keep] - left))
            values.append(height[keep])
        pixel = np.concatenate([np.zeros(0, np.int64), *pixels])
        value = np.concatenate([np.zeros(0), *values])
        order = np.lexsort((value, pixel))
        self.pixel, self.value = pixel[order], value[order]
        # One sorted key per face and pixel: the pixel number, then the height (offset into 1 .. span - 1).
        self.base, self.span = low - 1.0, high - low + 2.0
        self.keys = self.pixel * self.span + (self.value - self.base)

    def highest_below(self, rows: np.ndarray, cols: np.ndarray, limit: Any) -> np.ndarray:
        """Height of the highest face at or below ``limit`` at each pixel, -inf where there is none in the z range."""
        pixel = (rows - self.top) * self.width + (cols - self.left)
        if not len(self.keys):
            return np.full(pixel.shape, -np.inf)
        found = np.searchsorted(self.keys, pixel * self.span + np.clip(limit - self.base, 0.0, self.span - 0.5), side="right") - 1
        safe = np.maximum(found, 0)
        return np.where((found >= 0) & (self.pixel[safe] == pixel), self.value[safe], -np.inf)

    def lowest_above(self, rows: np.ndarray, cols: np.ndarray, limit: Any, strict: bool = False) -> np.ndarray:
        """Height of the lowest face at or above ``limit`` (above it, if ``strict``) at each pixel, +inf where there is none in the z range."""
        pixel = (rows - self.top) * self.width + (cols - self.left)
        if not len(self.keys):
            return np.full(pixel.shape, np.inf)
        side = "right" if strict else "left"
        found = np.searchsorted(self.keys, pixel * self.span + np.clip(limit - self.base, 0.5, self.span - 0.5), side=side)
        safe = np.minimum(found, len(self.keys) - 1)
        return np.where((found < len(self.keys)) & (self.pixel[safe] == pixel), self.value[safe], np.inf)


def _box_max(values: np.ndarray, half_rows: int, half_cols: int) -> np.ndarray:
    """Maximum over the (2 * half_rows + 1) x (2 * half_cols + 1) box around each pixel, -inf outside."""
    padded = np.pad(values, ((half_rows, half_rows), (half_cols, half_cols)), constant_values=-np.inf)
    rows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half_rows + 1, axis=0).max(axis=-1)
    return np.lib.stride_tricks.sliding_window_view(rows, 2 * half_cols + 1, axis=1).max(axis=-1)


def _box_min(values: np.ndarray, half_rows: int, half_cols: int) -> np.ndarray:
    """Minimum over the box around each pixel, +inf outside."""
    return -_box_max(-values, half_rows, half_cols)


def _nearest(lower: np.ndarray, upper: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Per pixel, whichever of the two heights lies nearer ``target`` (the lower one on a tie)."""
    return np.where(target - lower <= upper - target, lower, upper)


def zone_stand_mask(
    zone: BombZone,
    areas: list[NavArea],
    index: np.ndarray,
    spread: np.ndarray,
    surfaces: WalkableSurfaces,
    transform: dict[str, Any],
) -> np.ndarray:
    """Pixels of the zone's reach where a player standing on a floor of ``areas`` is in the zone.

    ``index`` and ``spread`` are the area images (0 for none, else 1 + position in
    ``areas``) before and after the floor margin is filled in. Each pixel stands
    for every area covering it, not only the topmost one drawn, so a site floor
    shows through a walkway above it; a margin pixel stands for the area spread to
    it.

    The nav mesh says which floor a player is on, the map's collision how high it
    is: the floor is the walkable face nearest the area's height under the pixel
    (or, at the edge of a raised area, under the player's 32 x 32 box), within
    NAV_FLOOR_TOLERANCE; with no face that close (an entity's own collision, such
    as a func_brush box) the area's height is used. CS2 lifts the player's box onto
    the highest face under it up to STEP_HEIGHT above that floor (a kerb, the rim
    of Inferno's B fountain), and their feet are there. Higher faces under the box
    are walls the player stands against; the margin pixels next to them count as
    the floor in front, as the floor itself is drawn up to the wall.
    """
    size = index.shape[0]
    reach = rasterize_polygons([zone.reach], transform, size)
    # Area heights whose floor can hold feet in the zone's band.
    lowest, highest = zone.floor_low - STEP_HEIGHT - NAV_FLOOR_TOLERANCE, zone.max_z + NAV_FLOOR_TOLERANCE
    a, b, c, d = _pixel_affine(transform, size)
    half_rows, half_cols = int(HULL_RADIUS * abs(c)), int(HULL_RADIUS * abs(a))
    rows, cols = np.nonzero(reach)
    top, bottom = max(int(rows.min()) - half_rows, 0), min(int(rows.max()) + half_rows + 1, size)
    left, right = max(int(cols.min()) - half_cols, 0), min(int(cols.max()) + half_cols + 1, size)
    window = (slice(top, bottom), slice(left, right))
    in_reach, own, spread_to = reach[window], index[window], spread[window]
    margin = in_reach & (own == 0) & (spread_to > 0)
    corners = {
        number: to_pixels(transform, ((x, y) for x, y, _ in area.corners), size)
        for number, area in enumerate(areas, start=1)
        if area.max_z >= lowest and area.min_z <= highest
    }
    numbers = sorted(
        {number for number in np.unique(spread_to[margin]).tolist() if number in corners}
        | {number for number, points in corners.items() if _box_meets(points, in_reach, top, left)}
    )
    result = np.zeros_like(reach)
    if not numbers:
        return result
    z_range = (
        min(areas[n - 1].min_z for n in numbers) - NAV_FLOOR_TOLERANCE,
        max(areas[n - 1].max_z for n in numbers) + NAV_FLOOR_TOLERANCE + STEP_HEIGHT,
    )
    min_x, max_x = sorted(((left - b) / a, (right - b) / a))
    min_y, max_y = sorted(((top - d) / c, (bottom - d) / c))
    for x0, y0, x1, y1, low, high in surfaces.rounds:
        if x0 <= max_x and x1 >= min_x and y0 <= max_y and y1 >= min_y and low <= z_range[1] and high >= z_range[0]:
            raise ResourceFormatError("a sphere or capsule collision shape near a bomb site could be stood on: not supported")
    stack = SurfaceStack(surfaces, transform, size, (top, left, bottom, right), z_range)
    # Under the box around each pixel: the lowest face above the zone's top, and the lowest one its feet can be on.
    window_rows, window_cols = np.mgrid[top:bottom, left:right]
    over_top = _box_min(stack.lowest_above(window_rows, window_cols, zone.max_z, strict=True), half_rows, half_cols)
    from_low = _box_min(stack.lowest_above(window_rows, window_cols, zone.floor_low), half_rows, half_cols)
    stands = np.zeros_like(in_reach)
    for number in numbers:
        area = areas[number - 1]
        canvas = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(canvas).polygon([(u - left, v - top) for u, v in corners[number]], fill=255)
        mine = in_reach & ((np.asarray(canvas) > 0) | (margin & (spread_to == number)))
        if not mine.any():
            continue
        mine_rows, mine_cols = np.nonzero(mine)
        # This area's pixels and the box around each, inside the window.
        r0, r1 = max(int(mine_rows.min()) - half_rows, 0), min(int(mine_rows.max()) + half_rows + 1, bottom - top)
        c0, c1 = max(int(mine_cols.min()) - half_cols, 0), min(int(mine_cols.max()) + half_cols + 1, right - left)
        part = (slice(r0, r1), slice(c0, c1))
        grid_rows, grid_cols = window_rows[part], window_cols[part]
        plane = area_plane(area, transform, size)
        nav = np.clip(plane[0] * (grid_cols + 0.5) + plane[1] * (grid_rows + 0.5) + plane[2], area.min_z, area.max_z)
        below, above = stack.highest_below(grid_rows, grid_cols, nav), stack.lowest_above(grid_rows, grid_cols, nav)
        under_pixel = _nearest(below, above, nav)
        under_box = _nearest(_box_max(below, half_rows, half_cols), _box_min(above, half_rows, half_cols), nav)
        floor = np.where(
            np.abs(under_pixel - nav) <= NAV_FLOOR_TOLERANCE,
            under_pixel,
            np.where(np.abs(under_box - nav) <= NAV_FLOOR_TOLERANCE, under_box, nav),
        )
        # The feet are on the highest face under the box up to a step above the floor (or on the floor).
        lift = floor + STEP_HEIGHT
        feet_in_band = (floor <= zone.max_z) & (over_top[part] > lift) & ((floor >= zone.floor_low) | (from_low[part] <= lift))
        stands[part] |= mine[part] & feet_in_band
    result[window] = stands
    return result


def _box_meets(points: list[tuple[float, float]], mask: np.ndarray, top: int, left: int) -> bool:
    """Whether the pixel bounding box of ``points`` meets a set pixel of ``mask`` (a window starting at top, left)."""
    cols = [u - left for u, _ in points]
    rows = [v - top for _, v in points]
    c0, c1 = max(math.floor(min(cols)), 0), min(math.ceil(max(cols)) + 1, mask.shape[1])
    r0, r1 = max(math.floor(min(rows)), 0), min(math.ceil(max(rows)) + 1, mask.shape[0])
    return c0 < c1 and r0 < r1 and bool(mask[r0:r1, c0:c1].any())


def render_level(
    areas: list[NavArea],
    transform: dict[str, Any],
    sites: list[Bombsite],
    below: list[NavArea] | None = None,
    context: list[NavArea] | None = None,
    surfaces: WalkableSurfaces | None = None,
) -> Image.Image:
    size = IMAGE_SIZE * SUPERSAMPLE
    ordered = sorted(areas, key=lambda area: (area.mean_z, area.area_id))
    floors, obstacles, openings = floor_outline(ordered, below)
    floor = rasterize_polygons(floors, transform, size)
    obstacle = rasterize_polygons(obstacles, transform, size) & ~floor
    opening = rasterize_polygons(openings, transform, size) & ~floor & ~obstacle
    world_per_pixel = _world_units_per_pixel(transform, size)
    # Mitred corners reach up to twice HULL_RADIUS past the nav mesh.
    covered = rasterize(ordered, transform, size)
    index = spread_index(covered, floor, int(2 * HULL_RADIUS / world_per_pixel) + 2)
    heights = height_map(ordered, index, transform)

    low, high = np.percentile(heights[index > 0], [3, 97])
    shade = np.clip((heights - low) / max(float(high - low), 1.0), 0.0, 1.0).astype(np.float32)[..., None]
    low_colour, high_colour = np.asarray(FLOOR_LOW, np.float32), np.asarray(FLOOR_HIGH, np.float32)
    pixels = np.where(index[..., None] > 0, low_colour + shade * (high_colour - low_colour), np.asarray(BACKGROUND, np.float32))
    if context:
        context_floors, context_obstacles, _ = floor_outline(context)
        silhouette = rasterize_polygons(context_floors, transform, size) | rasterize_polygons(context_obstacles, transform, size)
        pixels[silhouette & ~floor & ~obstacle & ~opening] = SILHOUETTE
    pixels[floor & (index == 0)] = FLOOR_LOW
    pixels[obstacle] = OBSTACLE
    pixels[opening] = OPENING

    # Bomb sites: where a player stands in each func_bomb_target hull's zone (its
    # footprint grown by the player's box) on a floor of this level from which
    # their box touches it (zone_stand_mask), drawn on floor only (not on
    # obstacles, walls or void), plus openings inside it (Nuke's A hatch).
    labels = []
    if any(site.zones for site in sites) and surfaces is None:
        raise ValueError("bomb sites need the map's walkable surfaces")
    for site in sites:
        tinted = np.zeros_like(floor)
        for zone in site.zones:
            stands = zone_stand_mask(zone, ordered, covered, index, surfaces, transform)
            tinted |= (stands & floor) | (rasterize_polygons([zone.reach], transform, size) & opening)
        pixels[tinted] = pixels[tinted] * (1 - SITE_ALPHA) + np.asarray(SITE, np.float32) * SITE_ALPHA
        if tinted.any():
            x, y = visual_centre(tinted, SITE_LABEL_SIZE // 2 * SUPERSAMPLE)
            labels.append((site.label, x / SUPERSAMPLE, y / SUPERSAMPLE))

    # Ledges: height jumps between neighbouring floor pixels, about one output pixel wide.
    with np.errstate(invalid="ignore"):
        step = np.zeros_like(floor)
        step[:, 1:] |= np.abs(heights[:, 1:] - heights[:, :-1]) > LEDGE_MIN_STEP
        step[1:, :] |= np.abs(heights[1:, :] - heights[:-1, :]) > LEDGE_MIN_STEP
    ledge = _filtered(step & floor, ImageFilter.MaxFilter(SUPERSAMPLE - 1)) & floor
    pixels[ledge] = pixels[ledge] * (1 - LEDGE_ALPHA) + np.asarray(LEDGE, np.float32) * LEDGE_ALPHA

    # Outline the floor (outer edge, and around holes, obstacles and openings) just inside its boundary.
    interior = _filtered(floor, ImageFilter.MinFilter(2 * SUPERSAMPLE - 1))
    pixels[floor & ~interior] = OUTLINE

    image = Image.fromarray(np.rint(pixels).astype(np.uint8), "RGB").reduce(SUPERSAMPLE)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=SITE_LABEL_SIZE)
    for label, x, y in labels:
        draw.text((x, y), label, font=font, fill=SITE_LABEL, anchor="mm", stroke_width=4, stroke_fill=SITE_LABEL_STROKE)
    return image


def build_map(game: Path, map_name: str, output_dir: Path) -> list[Path]:
    config = get_map_config(map_name)
    if config is None:
        raise SystemExit(f"{map_name} has no map config")
    areas = read_nav(read_map_nav(game, map_name))
    sites = read_bombsites(game, map_name)
    surfaces = read_walkable_surfaces(game, map_name)
    for site in sites:
        heights = ", ".join(f"{zone.min_z:g}..{zone.max_z:g}" for zone in site.zones)
        print(f"{map_name}\tsite {site.label}\t{len(site.zones)} trigger hull(s), z {heights}")
    written = []
    for level in levels(config):
        image = render_level(
            [area for area in areas if level.includes(area)],
            config["transform"],
            [Bombsite(site.label, tuple(filter(level.holds, site.zones))) for site in sites],
            below=[area for area in areas if level.below(area)],
            context=[area for area in areas if level.context(area)],
            surfaces=surfaces,
        )
        path = output_dir / Path(level.image_path).name
        save_png(image, path)
        written.append(path)
    return written


def save_png(image: Image.Image, path: Path) -> None:
    quantized = image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    quantized.save(path, format="PNG", optimize=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the tactical-map radars from a CS2 install's nav meshes.")
    parser.add_argument("--cs2", required=True, type=Path, help="CS2 install folder (Counter-Strike Global Offensive) or its game/csgo")
    parser.add_argument("--maps", nargs="*", default=list(SUPPORTED_MAP_NAMES), choices=SUPPORTED_MAP_NAMES)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR, help="output folder (default: frontend/public/maps)")
    args = parser.parse_args(argv)

    game = csgo_dir(args.cs2)
    args.out.mkdir(parents=True, exist_ok=True)
    for map_name in args.maps:
        for path in build_map(game, map_name, args.out):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            print(f"{path.name}\t{path.stat().st_size} bytes\t{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
