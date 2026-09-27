# 雷达图生成

`frontend/public/maps/` 里的战术地图雷达图全部由 `build_radars.py` 生成：读取运营者自己电脑上 CS2 安装里每张图的导航网格（`game/csgo/maps/<map>.vpk` 内的 `maps/<map>.nav`），按 `backend/app/parser/map_config.py` 里该图的坐标变换直接画出可站立的地面。回放里的玩家坐标用的是同一个变换，所以点位和图天然对齐。仓库里不再有第三方雷达图，也不复制 Valve 的雷达美术素材；不过图形由 Valve 的游戏数据派生，能否这样公开分发尚未单独确认。

## 用法

需要 Python 3.12 或更高版本（固定版本的 numpy 要求 3.12）。

```bash
python3.12 -m venv scripts/maps/venv   # .gitignore 里的 venv/ 会忽略它；Windows 上可用 py -3.12 -m venv
scripts/maps/venv/Scripts/python -m pip install -r scripts/maps/requirements.txt   # Linux/macOS 用 scripts/maps/venv/bin/python
scripts/maps/venv/Scripts/python scripts/maps/build_radars.py --cs2 "C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive"
```

- `--cs2`：CS2 安装目录，或其中的 `game/csgo`。脚本只读取游戏文件，不写入、不移动任何东西。
- `--maps de_dust2 de_nuke`：只重画部分地图，默认画 `SUPPORTED_MAP_NAMES` 里的全部地图。
- `--out DIR`：输出目录，默认 `frontend/public/maps/`。先输出到临时目录比对也可以。

每张图输出 1024×1024 的 PNG（Nuke 另有下层图），并打印文件大小和 SHA-256。同一个 CS2 安装、同一组固定版本的依赖，输出逐字节一致；更新图片后把哈希和 `game/csgo/steam.inf` 里的 `ClientVersion` / `PatchVersion` 写进 `frontend/public/maps/ATTRIBUTION.md`。游戏更新改了地图时重新跑一遍即可；脚本没改而哈希变了，说明是游戏数据变了。

## 画法

- 深色画布 `#1d2732`（与 `--radar-bg` 一致），地面为石板灰，越高越亮。
- 导航网格离墙留了玩家碰撞半径（16 单位），脚本把地面轮廓外扩回这段距离。
- 被地面围住的小块空洞（面积不超过 256×256）：没有任何导航区域的是箱子、柱子等障碍物，画成带轮廓的浅色实心块；多层地图里大部分落在下层地面上方的是开口（如 Nuke A 点的舱口），画成比任何地面都暗的颜色。更大的空洞留空。
- 地面轮廓描 1 px 浅色线；相邻像素高差超过 40 单位时画一条淡的落差线。
- 包点：以 `SITE_CENTRES` 表里的中心为起点，沿导航网格连通、高差不超过 90 的区域（以及其中的开口），裁成半径 300 单位的圆（Nuke B 为 380），叠 35% 的红色，并标 36 px 的 `A` / `B`（Pillow 自带的 Aileron 字体，CC0；描边用深红色，不在地面上压出背景色的洞）。CS2 的导航网格没有地名，所以包点中心是实测值：Dust II、Mirage、Ancient、Nuke 取已解析比赛里 `bomb_planted` 的平均位置；Inferno 取 `game/csgo/pak01_dir.vpk` 里 `resource/overviews/de_inferno.txt` 的加载界面包点图标位置；Anubis 的 overview 文件没有包点图标，取的是本项目 2026-09-26 之前用的雷达图（来自 rabume/cs2-dma-radar）上两个包点标记的中心，那张图没有字母，哪个是 A、哪个是 B 是推定的。有了这些图的真实比赛后，用 `bomb_planted` 位置校正即可。
- 多层地图（Nuke）按 `lowerLevelMaxZ` 分层：导航区域最低点 `<= -495` 进下层图，最高点 `> -495` 进上层图，跨层的坡道两张图都有。下层图底下另画一层上层地面的淡色平涂剪影，便于对照位置。

## 改坐标变换时

1. 同时改 `backend/app/parser/map_config.py` 和 `frontend/lib/map-config.ts`，后端测试会比对两边的变换参数。
2. 重新生成图片并更新 `ATTRIBUTION.md` 的变换表和哈希表。
3. 已存的回放在 `mapMetadata.transform` 里记着当时的变换，读取时由 `legacy_radar_reprojection` 换算到新变换，不需要重新解析。被旧边界截在边缘上（坐标正好是 0 或 100）的点无法还原：读取时去掉它的 x/y（回放里不显示这个点），数量记在 `mapMetadata.legacyEdgePositionsHidden`，回放诊断里标出 `legacyRadarEdgePositions`；只有重新解析这场比赛才能找回。变换没变的回放，读取时会用当前配置刷新校准状态和署名等显示字段，坐标不动。

## 校验

```bash
PYTHONPATH=backend .venv/Scripts/python -m unittest backend.tests.test_map_config
cd frontend && node lib/map-config.test.mjs
```

对齐检查的做法：取真实比赛回放里每帧存活玩家的雷达百分比坐标，统计落在新图地面像素上（非背景色，Nuke 下层图还要排除上层剪影色；容差 2 px）的比例；Nuke 按玩家 Z 分到上下层图。
