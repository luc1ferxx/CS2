"""Render the tactical-map radar images from the operator's own CS2 install.

Reads each supported map's navigation mesh (``maps/<map>.nav`` inside
``game/csgo/maps/<map>.vpk``) and draws the walkable floor with exactly the
transform the app projects player positions with (``backend/app/parser/
map_config.py``), so replay dots land on the drawn floor by construction. No
Valve texture, radar image or font is read or shipped, and the game install is
only ever read.

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
import math
import struct
import sys
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import vpk
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import MultiPolygon, Polygon
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
SITE_RADIUS = 300.0  # world units around the site centre (default; a site may override it)
SITE_MAX_STEP = 90.0  # world units of height the tinted floor may climb from the centre
HULL_RADIUS = 16.0  # world units: nav areas stop this far short of walls
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
    neighbours: tuple[int, ...]

    @property
    def mean_z(self) -> float:
        return sum(corner[2] for corner in self.corners) / len(self.corners)

    @property
    def min_z(self) -> float:
        return min(corner[2] for corner in self.corners)

    @property
    def max_z(self) -> float:
        return max(corner[2] for corner in self.corners)

    @property
    def centre(self) -> tuple[float, float]:
        return (
            sum(corner[0] for corner in self.corners) / len(self.corners),
            sum(corner[1] for corner in self.corners) / len(self.corners),
        )

    def contains_xy(self, x: float, y: float) -> bool:
        inside = False
        for index, (x1, y1, _) in enumerate(self.corners):
            x2, y2, _ = self.corners[index - 1]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
        return inside


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
        neighbours: set[int] = set()
        for _ in polygon:
            for _ in range(reader.u32()):
                neighbour_id, _edge = reader.unpack("2I")
                neighbours.add(neighbour_id)
        reader.unpack("BI")  # legacy hiding spot / encounter counts
        for _ in range(2):  # ladders above, ladders below
            reader.unpack(f"{reader.u32()}I")
        if len(polygon) >= 3:
            areas.append(NavArea(area_id, polygon, tuple(sorted(neighbours))))
    if len({area.area_id for area in areas}) != len(areas):
        raise NavFormatError("duplicate nav area ids")
    return areas


def csgo_dir(install: Path) -> Path:
    for candidate in (install, install / "game" / "csgo"):
        if (candidate / "maps").is_dir():
            return candidate
    raise SystemExit(f"{install} is not a CS2 install (expected game/csgo/maps)")


def read_map_nav(game: Path, map_name: str) -> bytes:
    package_path = game / "maps" / f"{map_name}.vpk"
    if not package_path.is_file():
        raise SystemExit(f"{package_path} is missing; is {map_name} installed?")
    package = vpk.open(str(package_path))
    return bytes(package.get_file(f"maps/{map_name}.nav").read())


@dataclass(frozen=True)
class Bombsite:
    label: str
    x: float
    y: float
    z: float | None = None  # floor height at the centre, when known
    level: str | None = None  # floor of a multi-level map
    radius: float = SITE_RADIUS


# Bomb site centres in world units. The nav mesh has no place names, so these
# are measured:
# - Dust II, Mirage, Ancient, Nuke: the mean bomb_planted position of real demos
#   parsed by this app. Nuke B's plants spread about 550 units north-south, so its
#   tint is wider than the default.
# - Inferno (no demo yet): Valve's loading-screen icons, bombA_x/y and bombB_x/y in
#   game/csgo/pak01_dir.vpk: resource/overviews/de_inferno.txt.
# - Anubis (no demo yet; its overview file has no bomb icons): the centres of the
#   two site markers on the radar image this project shipped before 2026-09-26,
#   from rabume/cs2-dma-radar (whose README credits the art to Lexogrine and
#   Boltobserv). That image has no letters, so which marker is A and which is B
#   is an assumption (A north-east, B west).
# Replace any of these with the mean bomb_planted position once demos exist.
SITE_CENTRES: dict[str, tuple[Bombsite, ...]] = {
    "de_dust2": (Bombsite("A", 1110, 2530, 97), Bombsite("B", -1550, 2540, 6)),
    "de_mirage": (Bombsite("A", -410, -2100, -177), Bombsite("B", -1970, 340, -160)),
    "de_inferno": (Bombsite("A", 1980, 420), Bombsite("B", 375, 2770)),
    "de_ancient": (Bombsite("A", -1255, 775, 56), Bombsite("B", 920, 5, 132)),
    "de_nuke": (Bombsite("A", 670, -745, -410, "upper"), Bombsite("B", 535, -980, -765, "lower", radius=380.0)),
    "de_anubis": (Bombsite("A", 1250, 1940), Bombsite("B", -1060, 670)),
}


def bombsites(map_name: str) -> list[Bombsite]:
    return list(SITE_CENTRES.get(map_name, ()))


def _nothing(area: NavArea) -> bool:
    return False


@dataclass(frozen=True)
class Level:
    name: str
    image_path: str
    includes: Callable[[NavArea], bool]
    # Areas of a floor under this one: small holes over them are openings, not obstacles.
    below: Callable[[NavArea], bool] = _nothing
    # Areas of another floor, drawn as a flat faded silhouette under this one for orientation.
    context: Callable[[NavArea], bool] = _nothing


def levels(config: dict[str, Any]) -> list[Level]:
    secondary = config.get("secondaryRadarImagePath")
    threshold = config.get("lowerLevelMaxZ")
    if not secondary or threshold is None:
        return [Level("all", config["radarImagePath"], lambda area: True)]
    limit = float(threshold)
    # Same rule as the viewer (z <= lowerLevelMaxZ is the lower floor); ramps and
    # stairs that cross the threshold are drawn on both floors.
    return [
        Level("upper", config["radarImagePath"], lambda area: area.max_z > limit, below=lambda area: area.max_z <= limit),
        Level("lower", secondary, lambda area: area.min_z <= limit, context=lambda area: area.max_z > limit),
    ]


def site_areas(areas: list[NavArea], site: Bombsite) -> set[int]:
    """Ids of the areas around a site centre, grown over nav links from the area under it."""
    by_id = {area.area_id: area for area in areas}
    seed = _seed_area(areas, site)
    found = {seed.area_id}
    queue = deque([seed])
    reach = site.radius * 2  # the tint itself is clipped to the site radius per pixel
    while queue:
        for neighbour_id in queue.popleft().neighbours:
            neighbour = by_id.get(neighbour_id)
            if (
                neighbour is None
                or neighbour.area_id in found
                or abs(neighbour.mean_z - seed.mean_z) > SITE_MAX_STEP
                or math.dist(neighbour.centre, (site.x, site.y)) > reach
            ):
                continue
            found.add(neighbour.area_id)
            queue.append(neighbour)
    return found


def _seed_area(areas: list[NavArea], site: Bombsite) -> NavArea:
    """The site floor at the centre: closest in plan and height when the height is known."""
    if site.z is not None:
        height = site.z
        return min(areas, key=lambda area: (_distance_xy(area, site.x, site.y) + abs(area.mean_z - height), area.area_id))
    under = [area for area in areas if area.contains_xy(site.x, site.y)]
    if under:
        return max(under, key=lambda area: (_planar_area(area), area.area_id))
    return min(areas, key=lambda area: (_distance_xy(area, site.x, site.y), area.area_id))


def _distance_xy(area: NavArea, x: float, y: float) -> float:
    if area.contains_xy(x, y):
        return 0.0
    best = math.inf
    for index, (x1, y1, _) in enumerate(area.corners):
        x2, y2, _ = area.corners[index - 1]
        length = (x2 - x1) ** 2 + (y2 - y1) ** 2
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / length))
        best = min(best, math.dist((x, y), (x1 + t * (x2 - x1), y1 + t * (y2 - y1))))
    return best


def _planar_area(area: NavArea) -> float:
    points = area.corners
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1, _), (x2, y2, _) in zip(points, points[1:] + points[:1], strict=True))) / 2


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


def height_map(areas: list[NavArea], index: np.ndarray, transform: dict[str, Any]) -> np.ndarray:
    """World height per pixel from the best-fit plane of the pixel's area, NaN on void."""
    size = index.shape[0]
    planes = np.full((len(areas) + 1, 5), np.nan)
    for number, area in enumerate(areas, start=1):
        pixels = np.array(to_pixels(transform, ((x, y) for x, y, _ in area.corners), size))
        design = np.column_stack([pixels, np.ones(len(pixels))])
        planes[number, :3] = np.linalg.lstsq(design, np.array([corner[2] for corner in area.corners]), rcond=None)[0]
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


def render_level(
    areas: list[NavArea],
    transform: dict[str, Any],
    sites: list[Bombsite],
    below: list[NavArea] | None = None,
    context: list[NavArea] | None = None,
) -> Image.Image:
    size = IMAGE_SIZE * SUPERSAMPLE
    ordered = sorted(areas, key=lambda area: (area.mean_z, area.area_id))
    floors, obstacles, openings = floor_outline(ordered, below)
    floor = rasterize_polygons(floors, transform, size)
    obstacle = rasterize_polygons(obstacles, transform, size) & ~floor
    opening = rasterize_polygons(openings, transform, size) & ~floor & ~obstacle
    world_per_pixel = _world_units_per_pixel(transform, size)
    # Mitred corners reach up to twice HULL_RADIUS past the nav mesh.
    index = spread_index(rasterize(ordered, transform, size), floor, int(2 * HULL_RADIUS / world_per_pixel) + 2)
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

    # Bomb sites: floor linked to the site centre, and openings in it (Nuke's A
    # hatch, where the bomb is often planted), clipped to a disc around it.
    rows, cols = np.ogrid[0:size, 0:size]
    labels = []
    for site in sites:
        member_ids = site_areas(areas, site)
        members = np.array([False] + [area.area_id in member_ids for area in ordered])
        ((cx, cy),) = to_pixels(transform, [(site.x, site.y)], size)
        radius = site.radius / world_per_pixel
        tinted = (members[index] | opening) & ((cols + 0.5 - cx) ** 2 + (rows + 0.5 - cy) ** 2 <= radius**2)
        pixels[tinted] = pixels[tinted] * (1 - SITE_ALPHA) + np.asarray(SITE, np.float32) * SITE_ALPHA
        tinted_rows, tinted_cols = np.nonzero(tinted)
        if len(tinted_rows):
            labels.append((site.label, (tinted_cols.mean() + 0.5) / SUPERSAMPLE, (tinted_rows.mean() + 0.5) / SUPERSAMPLE))

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
    written = []
    for level in levels(config):
        sites = [site for site in bombsites(map_name) if site.level in (None, level.name)]
        image = render_level(
            [area for area in areas if level.includes(area)],
            config["transform"],
            sites,
            below=[area for area in areas if level.below(area)],
            context=[area for area in areas if level.context(area)],
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
