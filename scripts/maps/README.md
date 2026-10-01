# 雷达图生成

`frontend/public/maps/` 里的战术地图雷达图全部由 `build_radars.py` 生成：读取运营者自己电脑上 CS2 安装里每张图的导航网格（`game/csgo/maps/<map>.vpk` 内的 `maps/<map>.nav`），按 `backend/app/parser/map_config.py` 里该图的坐标变换直接画出可站立的地面；包点取自同一个 vpk 里地图自带的 `func_bomb_target` 触发器，包点里玩家站的高度取自地图自己的碰撞数据（见下文“画法”）。回放里的玩家坐标用的是同一个变换，所以点位和图天然对齐。仓库里不再有第三方雷达图，也不复制 Valve 的雷达美术素材；不过图形由 Valve 的游戏数据派生，能否这样公开分发尚未单独确认。

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
- 包点：地图自己的 `func_bomb_target` 触发器，没有任何手量数值。实体取自编译后的实体表 `maps/<map>/entities/default_ents.vents_c`（及其子表），触发器形状取自它 `model` 指向的 `maps/<map>/entities/*.vmdl_c` 里 PHYS 块的凸包顶点，按实体的 `origin` / `scales` 放到世界坐标；两者都是二进制 KeyValues3，用 `keyvalues3` 包解码。字母取实体的 `bomb_site_designation`（0 = A，1 = B）；overview 文件有包点图标时（Anubis 没有），每个包点必须离自己字母的图标最近，否则脚本报错退出。
  - 红色范围就是能下包的位置：CS2 在玩家的碰撞盒（水平 32×32、高 72）碰到触发器时就算进入包点，即玩家坐标在触发器轮廓外扩 16 单位以内、脚下高度满足 `触发器底 - 72 <= 脚下高度 <= 触发器顶`。用样例比赛里持包者的真实坐标检验，这条规则与 `in_bomb_zone` 逐个采样一致（27.9 万个持包者采样，误判 0）。脚本把每个凸包的平面轮廓按玩家碰撞盒外扩（凸多边形与正方形的闵可夫斯基和，精确），再逐像素判断站在那里的玩家是否在包点里：
    - 站在哪里取自导航网格：当前图这一层里覆盖该像素的每个导航区域都算，不只是画在最上面的那个，所以 Nuke B 点上方管道、窄台下面的 B 点地面也会涂红。
    - 站多高取自地图自己的碰撞：`maps/<map>/world_physics.vmdl_c` 的 PHYS 块里对玩家实心、坡度不陡于可站立上限（法线 z ≥ 0.7）的面（三角网格和凸包的朝上面）。导航区域所在的地面 = 该像素正下方离导航高度最近的碰撞面（在高台边缘，正下方没有时取玩家 32×32 碰撞盒底下的），相差不超过 24 单位；CS2 会把玩家碰撞盒抬到盒子底下、比这层地面高不超过一个台阶（18 单位）的最高面上，脚就在那里。更高的面是玩家贴着的墙，墙前画出的那一圈地面按前面的地面算。只用导航高度不够准：导航面最多比玩家实际站的面高约 24 单位，以前因此把 Inferno B 喷泉池和 Anubis A/B 边缘的一些地方漏涂了。导航区域上下 24 单位内没有世界碰撞面时（本意是站在 func_brush 之类自带碰撞、脚本不读的实体上），用导航区域自己的高度；六张图的包点里只有 Anubis A 的 5 个子像素走到这一步，结果不受影响。这个 24 改成 22 或 30 成图逐字节不变，改成 20 或 32 红色范围会变：Anubis A 至少要 22，Inferno B 喷泉池沿的导航区域伸到下方 32 单位处的地面上空，所以要小于 32。碰撞层只认识已知的名字，遇到没见过的层、或包点附近能站上去的球体/胶囊体碰撞，脚本报错退出，不猜。
    - 只涂当前图已画出的地面（不涂墙、空洞、障碍物），再加上范围内的开口（Nuke A 的舱口）；叠 35% 的红色。Inferno B 的喷泉池（池底是 z 184.3 的玩家碰撞面，低于触发器顶 186）涂红；池沿（z 193，比池底高一个台阶以内）不涂，池里离池沿 16 单位以内（碰撞盒会被抬到池沿上）也不涂。
  - Nuke 按触发器高度分层：顶 `> -495` 的画在上层图（A），`底 - 72 <= -495` 的画在下层图（B）。
  - `A` / `B` 字母 36 px（Pillow 自带的 Aileron 字体，CC0；描边用深红色，不在地面上压出背景色的洞），放在红色区域（把它围住的柱子、喷泉等空洞算在内）的形心附近、离边缘至少半个字高的位置；区域太窄时放在最深处。
  - 用 4 场样例比赛核对过成图：42 次 `bomb_planted` 和全部 7332 个 `in_bomb_zone` 持包者采样（隔一个 tick 取一次）都落在所在包点、所在楼层的红色像素上；27.1 万个不在包点的持包者采样里有 111 个落在红色上，其中 87 个是在包点上空跳起，24 个在区域边缘一个像素内。比赛里玩家的高度也印证了碰撞盒规则：每个包点附近 82–99% 的玩家采样正好站在碰撞盒下最高的面上（其余在空中），按坐标正下方的面只有 31–82%。Inferno 和 Anubis 没有样例比赛。
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
