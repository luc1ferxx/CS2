# Map Asset Attribution

The replay viewer uses CS2 radar images from `rabume/cs2-dma-radar`:

- Source repository: https://github.com/rabume/cs2-dma-radar
- Source directory: `client/src/assets/map/`

The source repository README attributes radar map assets to CS2 React HUD by
Lexogrine under MIT and Boltobserv by boltgolt under GPL-3.0. Keep this
attribution file with the assets. For production, prefer extracting radar assets
from a CS2 install owned by the operator or replacing this with an explicitly
licensed internal asset pipeline.

## Files

| File | Purpose | Download URL | SHA-256 |
| --- | --- | --- | --- |
| `de_ancient_radar.png` | CS2 Ancient tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_ancient_radar.png | `0e7689cdb175aa8446efb8d311dd4cf514fffd751c8a558f42e84fa58cbd9c85` |
| `de_anubis_radar.png` | CS2 Anubis tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_anubis_radar.png | `a117c55fcd6690c74210125c2f409a32b9348c931213b388cf3ab5ed0c3d47ae` |
| `de_dust2_radar.png` | CS2 Dust II tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_dust2_radar.png | `834c3cda4b87c80344caefcfd2c20aada7e09ce1bd6a8da7f8f6ab5590b7e188` |
| `de_inferno_radar.png` | CS2 Inferno tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_inferno_radar.png | `074557015e7c5778a6f7177fe118d18152da351897a88ed55c29a29b67139415` |
| `de_mirage_radar.png` | CS2 Mirage tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_mirage_radar.png | `72b825fcd0e1ba1b7b6cd0129f8fc67eb08ff5809c15da9ddc34e35b9e13b1fd` |
| `de_nuke_radar.png` | CS2 Nuke upper tactical radar background | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_nuke_radar.png | `e648fa0e262f7a0f92f5243fe947179b6888e904a0cd85e9cafaf03252535d04` |
| `de_nuke_lower_radar.png` | CS2 Nuke lower tactical radar background, reserved for future floor-aware rendering | https://raw.githubusercontent.com/rabume/cs2-dma-radar/main/client/src/assets/map/de_nuke_lower_radar.png | `046e4ff6ada2fcf97a755b07c9669912b74037ffdff1d95c294bd4e3e0cb399e` |

## Coordinate Sources

Dust II uses the official-style CS2 overview transform already present in this
mock app. Mirage, Inferno, Ancient, Nuke, and Anubis use approximate bounds
adapted from the same source repository's `Map.vue` map area configuration and
are marked `calibrated: false` / `confidence: approximate` in app metadata.
