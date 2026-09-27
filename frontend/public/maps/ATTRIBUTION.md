# Map Asset Attribution

Every radar image in this folder is rendered by this project with
[`scripts/maps/build_radars.py`](../../../scripts/maps/build_radars.py) from the
navigation meshes (`maps/<map>.nav` inside `game/csgo/maps/<map>.vpk`) of the
operator's own CS2 install. No third-party radar image remains, and no Valve
texture, radar image, logo or font is copied: the pictures are drawn from the
walkable-floor geometry in the game's navigation data (slate floor shaded by
height, outlines, solid obstacles where the nav mesh has small enclosed holes,
darker openings such as Nuke's A hatch, tinted bomb sites; the Nuke lower-floor
image also shows the upper floor as a flat faded silhouette). The `A` / `B`
labels use Pillow's bundled Aileron font (CC0). The game install is only read.

The images are derived from Valve's game data. Whether that data may be
redistributed in this form has not been separately confirmed; this file only
records where the images come from.

The nav mesh reader follows awpy's `awpy/nav.py` (MIT, Copyright (c) 2020-2025
Peter Xenopoulos; its notice is kept in the script), extended for the version 36
nav files current CS2 ships.

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

Bomb site centres are a small table of world coordinates in the script; the nav
mesh itself has no place names:

- Dust II, Mirage, Ancient, Nuke: the mean `bomb_planted` position of demos
  parsed by this app. Nuke B uses a wider tint (380 units) because its plants
  spread about 550 units north-south.
- Inferno: Valve's loading-screen icons (`bombA_x/y`, `bombB_x/y` in
  `resource/overviews/de_inferno.txt`).
- Anubis: its overview file has no bomb icons, so the centres are the two site
  markers of the radar image this project shipped before 2026-09-26, from
  [rabume/cs2-dma-radar](https://github.com/rabume/cs2-dma-radar) (whose README
  credits the art to Lexogrine and Boltobserv). Only the two coordinates are
  used. That image has no letters, so which marker is A and which is B is an
  assumption (A north-east, B west) until an Anubis demo's `bomb_planted`
  events confirm it.

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

Rendered 2026-09-26 with Python 3.12 from the CS2 install on the operator's
machine, `game/csgo/steam.inf`: `ClientVersion=2000918`, `PatchVersion=1.41.8.5`,
`VersionDate=Sep 25 2026`. A hash mismatch after a CS2 update comes from the game
data if the script is unchanged.

| File | Purpose | SHA-256 |
| --- | --- | --- |
| `de_ancient_radar.png` | Ancient tactical radar | `f25e42dfa51d356573f5fc78a4901db470dcaff99e9017c80a1644e12c6f3e00` |
| `de_anubis_radar.png` | Anubis tactical radar | `d962527e0074392f9de71fb541f42232eacd32bc71bad560188367a41b84d1f9` |
| `de_dust2_radar.png` | Dust II tactical radar | `35660b46334f6601def76798a9a9dfd489f1d990e1342ef21513e41ca86b3b79` |
| `de_inferno_radar.png` | Inferno tactical radar | `99e4febb909cfabf2e3b9dce5a21e24bb022426c18c5059c9ae21ef33789c488` |
| `de_mirage_radar.png` | Mirage tactical radar | `d3280646602bd032fdec01a6a015412b3296baab465388e7f25e7f7a71faa340` |
| `de_nuke_radar.png` | Nuke upper floor tactical radar | `c70ab9d3fa0f0ea71ae490a40d2a3629754ea1d104acb0a464b30c79fdff9cd4` |
| `de_nuke_lower_radar.png` | Nuke lower floor tactical radar, selected by world Z | `cf994e877fa1d643f359a37e71835ec9314e8bb15950977361e2ce4efdd9da2c` |

If a radar or trustworthy transform is missing for a new map, keep the map
explicit as fallback rather than reusing Dust II assets or transforms.
