# Map Asset Attribution

Every radar image in this folder is rendered by this project with
[`scripts/maps/build_radars.py`](../../../scripts/maps/build_radars.py) from the
navigation meshes (`maps/<map>.nav` inside `game/csgo/maps/<map>.vpk`) of the
operator's own CS2 install. No third-party radar image remains, and no Valve
texture, radar image, logo or font is copied: the pictures are drawn from the
walkable-floor geometry in the game's navigation data (slate floor shaded by
height, outlines, solid obstacles where the nav mesh has small enclosed holes,
darker openings such as Nuke's A hatch, tinted bomb sites; the Nuke lower-floor
image also shows the upper floor as a flat faded silhouette). The bomb sites
come from each map's own `func_bomb_target` entities, and the heights players
stand at inside them from the map's own collision (see below). The `A` / `B`
labels use Pillow's bundled Aileron font (CC0). The game install is only read.

The images are derived from Valve's game data. Whether that data may be
redistributed in this form has not been separately confirmed; this file only
records where the images come from.

The nav mesh reader follows awpy's `awpy/nav.py` (MIT, Copyright (c) 2020-2025
Peter Xenopoulos; its notice is kept in the script), extended for the version 36
nav files current CS2 ships. Binary KeyValues3 (the entity lump, the brush
models' physics and the world collision) is decoded with the [`keyvalues3`](https://pypi.org/project/keyvalues3/)
package (MIT), a pinned tooling dependency; none of its code is copied.

## Coordinate sources

Each image is rendered with exactly the transform the app projects player
positions with (`backend/app/parser/map_config.py`, mirrored in
`frontend/lib/map-config.ts`), so replay positions line up with the drawing by
construction and every supported map is `calibrated`.

| Map | Transform | Source |
| --- | --- | --- |
| Dust II | overview `posX=-2476`, `posY=3239`, `scale=4.4` | CS2 overview values already used by this app |
| Nuke | overview `posX=-3453`, `posY=2887`, `scale=7`; lower floor `z <= -495` | [CS Demo Manager](https://github.com/akiver/cs-demo-manager/blob/main/src/node/database/maps/default-maps.ts), checked 2026-09-07 |
| Inferno | overview `posX=-2087`, `posY=3870`, `scale=4.9` | `game/csgo/pak01_dir.vpk`: `resource/overviews/de_inferno.txt` |
| Anubis | overview `posX=-2796`, `posY=3328`, `scale=5.22` | `game/csgo/pak01_dir.vpk`: `resource/overviews/de_anubis.txt` |
| Mirage | bounds x `-3400..1720`, y `-3220..1880` | unchanged app bounds (near-square, cover the whole nav mesh) |
| Ancient | bounds x `-2940..2170`, y `-2890..2130` | unchanged app bounds (near-square, cover the whole nav mesh) |

Inferno and Anubis used approximate bounds before 2026-09-26 that cut off part
of the map. Replays keep the transform they were normalized with in
`mapMetadata.transform`, and loading one normalized with an older transform
re-projects its positions onto the current one
(`legacy_radar_reprojection` in `map_config.py`). Positions the old bounds had
clamped to the image edge cannot be recovered: they lose their x/y (the viewer
hides the dot), are counted in `mapMetadata.legacyEdgePositionsHidden` and
flagged as `legacyRadarEdgePositions` in the replay diagnostics; only a fresh
parse of the demo restores them. Nuke's upper and lower images share one XY
transform; the lower image holds nav areas reaching `z <= -495`, the upper one
those above it (ramps that cross appear on both).

## Bomb sites

Since 2026-09-30 the red site areas are the maps' own plant zones, with no
measured or hand-placed values:

- Every `func_bomb_target` entity in the compiled entity lump
  (`maps/<map>/entities/default_ents.vents_c` in `game/csgo/maps/<map>.vpk`, and
  its child lumps), with the collision hulls of the brush model it references
  (the PHYS block of `maps/<map>/entities/*.vmdl_c`) placed by the entity's
  `origin` and `scales`.
- The letter is the entity's `bomb_site_designation` (`0` = A, `1` = B). Where
  Valve's loading-screen overview has site icons (`bombA_x/y`, `bombB_x/y` in
  `resource/overviews/<map>.txt` inside `game/csgo/pak01_dir.vpk`; Anubis has
  none), each site must lie nearest its own letter's icon or the script stops.
- CS2 counts a player as in the bomb zone while their box (32 x 32 units in plan,
  72 tall) touches the trigger: their position is inside the hull's outline grown
  by 16 units, and their feet are from 72 below the trigger's bottom up to its
  top. On the four sample demos this reproduces the carrier's `in_bomb_zone` flag
  for every one of 278,658 carrier samples (every 2nd tick), using their real
  position. A pixel is tinted where a player standing there would be in the zone:
  - where players stand comes from the nav mesh: every nav area of the image's
    floor under the pixel counts, not only the topmost one drawn, so Nuke's B
    floor shows through the pipes and ledges above it;
  - how high they stand comes from the map's own collision: the faces of
    `maps/<map>/world_physics.vmdl_c` (PHYS block) that are solid to players and
    flat enough to stand on. The floor of a nav area is the face nearest the
    area's height under the pixel (at the edge of a raised area, under the
    player's 32 x 32 box), within 24 units. CS2 lifts the player's box onto the
    highest face under it up to its 18-unit step height above that floor, and the
    feet are there; taller faces under the box are walls the player stands
    against, and the floor drawn up to them is tinted like the floor in front.
    The nav mesh alone is not precise enough: it lies up to about 24 units above
    the faces players stand on, which had left Inferno's B fountain basin and
    strips of Anubis A and B wrongly untinted. Where no world face lies within 24
    units of a nav area the area's own height is used (meant for an area on an
    entity's own collision, such as a func_brush box, which is not read); on
    the six maps that happens at 5 sub-pixels of Anubis A and changes none.
  - Only drawn floor is tinted (walls, void and obstacles stay untinted), plus
    openings inside a zone such as Nuke's A hatch. On Nuke, A is drawn on the
    upper image and B on the lower one, by trigger height.
  - On Inferno B the fountain basin (a player clip at z 184.3, under the trigger
    top of 186) is tinted. Its rim (z 193, one step above the basin) is not, nor
    the basin's edge within 16 units of the rim, where the box is lifted onto it.
- Checked against the four sample demos. Every `bomb_planted` position (42) and
  every carrier sample flagged `in_bomb_zone` (7,332) lies on a tinted pixel of
  its own site and floor. Of the 271,326 carrier samples not in the zone, 111 lie
  on the tint: 87 jumping above the site and 24 within a pixel of the zone's edge.
  The players' heights confirm the box rule: around each site 82-99 % of all
  player samples stand exactly on the highest face under their box (the rest are
  in the air), against 31-82 % on the face under their position.
- Inferno and Anubis have no sample demo. Their letters rest on the designation
  (Inferno's also on Valve's overview icons; Anubis's overview has none).

| Map | Site A | Site B |
| --- | --- | --- |
| Dust II | 2 hulls, x `976..1248`, y `2336..2624`, z `96..192` | 1 hull, x `-1728..-1344`, y `2496..2864`, z `0..96` |
| Mirage | 1 hull, x `-616..-264`, y `-2328..-1972`, z `-180..-156` | 1 hull, x `-2216..-1880`, y `72..440`, z `-159..-143` |
| Inferno | 2 hulls, x `1792..2160`, y `182..742`, z `160..200` | 3 hulls, x `136..568`, y `2552..2984`, z `160..186` |
| Ancient | 1 hull, x `-1568..-1216`, y `712..976`, z `52..84` | 2 hulls, x `669..1104`, y `-82..206`, z `128..160` |
| Nuke | 1 hull, x `502..874`, y `-940..-500`, z `-416..-320` (upper image) | 6 hulls, x `320..864`, y `-1312..-704`, z `-768..-728` (lower image) |
| Anubis | 1 hull (25-sided), x `947..1528`, y `1714..2193`, z `-192..-171` | 1 hull, x `-1226..-854`, y `518..870`, z `-14..10` |

Before 2026-09-30 the tint was a 300-unit disc (380 on Nuke B) around a
hand-measured centre per site, the Anubis ones taken from a third-party radar
image. That table and the disc code are gone.

## Regenerating

Needs Python 3.12 or newer (the pinned numpy requires it).

```bash
python3.12 -m venv scripts/maps/venv   # ignored by .gitignore (venv/)
scripts/maps/venv/Scripts/python -m pip install -r scripts/maps/requirements.txt   # bin/python on Linux/macOS
scripts/maps/venv/Scripts/python scripts/maps/build_radars.py --cs2 "C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive"
```

Output is deterministic for a given install and the pinned requirements. When a
map transform changes, change both config files, regenerate, and update this
table. See [`scripts/maps/README.md`](../../../scripts/maps/README.md).

## Files

Rendered 2026-10-01 with Python 3.12 from the CS2 install on the operator's
machine, `game/csgo/steam.inf`: `ClientVersion=2000922`, `PatchVersion=1.41.8.8`,
`VersionDate=Sep 30 2026`. A hash mismatch after a CS2 update comes from the game
data if the script is unchanged. (The unchanged pre-2026-09-30 script still
reproduced the previous hashes on this install, so the floors are unchanged;
only the site tint and labels differ. Each PNG has one 256-colour palette,
recomputed when the tint changes, so under 1 % of the pixels outside the sites
differ from the previous images, most by 4 colour levels or less and none by
more than 31.)

| File | Purpose | SHA-256 |
| --- | --- | --- |
| `de_ancient_radar.png` | Ancient tactical radar | `dc8b3e6aa3562d314cbd34abba45ba9106f636c8e52495d5e47fed142b30088b` |
| `de_anubis_radar.png` | Anubis tactical radar | `cf71beb0b30712b3bb92334fb0a26542512b892e4e2df5f75ce253386250ff47` |
| `de_dust2_radar.png` | Dust II tactical radar | `a481f960930ccce0b230b2a796f293acfbe43c9c7f90f569856087126a02b304` |
| `de_inferno_radar.png` | Inferno tactical radar | `0d0b1980029721459f100fc2c47e6b8af80001af4036302866c9e485a51ef276` |
| `de_mirage_radar.png` | Mirage tactical radar | `8a8a243d1a3b29036f21e3a584b7a49a130ec085e662c7a6113ed39682de36c2` |
| `de_nuke_radar.png` | Nuke upper floor tactical radar | `eed1bc671231ad9d2fd701b1e96e37d92197ea1cc6950ea6774499a6255b7617` |
| `de_nuke_lower_radar.png` | Nuke lower floor tactical radar, selected by world Z | `98f9d4768a833f8e67bb1086a85bd4cad68125bd2a04e513311078d03c001634` |

If a radar or trustworthy transform is missing for a new map, keep the map
explicit as fallback rather than reusing Dust II assets or transforms.
