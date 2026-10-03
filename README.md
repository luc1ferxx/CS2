# CS2 Demo Coach

[![CI](https://github.com/luc1ferxx/CS2/actions/workflows/ci.yml/badge.svg)](https://github.com/luc1ferxx/CS2/actions/workflows/ci.yml)

上传一场 CS2 比赛的 `.dem` 文件，在网页上复盘自己：战术回放、回合、时间轴，以及按回合整理、能直接跳到那一刻的规则建议。

- **建议来自 7 条确定性规则**（`backend/app/analysis/rules.py`），每条都附带可查看的依据。仓库不调用 OpenAI 或任何大模型；"Coach" 是产品名，不是实现方式。
- **第一人称视频是可选增强**：由我们自己运维的渲染机离线生成短片段。没有视频，复盘照样完整可用。
- **当前阶段**：准备以网站形式开放邀请制内测（Steam 登录、上传配额）。上线门槛见 [2D 内测上线计划](docs/rules_2d_beta_launch_v1.md)，进展见 [项目进展](docs/project_status_2026-09-13.md)。

## 目录

- [能做什么](#能做什么)
- [快速开始](#快速开始)
- [架构](#架构)
- [开发与验证](#开发与验证)
- [配置](#配置)
- [部署](#部署)
- [安全与隐私边界](#安全与隐私边界)
- [已知限制](#已知限制)
- [路线图](#路线图)
- [文档索引](#文档索引)

## 能做什么

| 场景 | 说明 |
| --- | --- |
| 比赛库 `/dashboard` | 上传 `.dem`（显示进度，可取消，可拖拽）、解析状态、失败原因与重新处理、每场比分（战队名取自 demo）、搜索、按地图和状态筛选、排序、重命名、软归档；永久删除（需确认，不可撤销） |
| 复盘工作区 `/demos/{id}` | 战术地图、回合条、时间轴、播放控制与键盘快捷键；地图、时间轴、回合和建议共用同一个时间与回合状态；复盘位置写入网址，刷新后可恢复 |
| 实时名单与道具 | 播放时两队名单随时间更新：金钱、血量、护甲、武器、携带的道具、到此刻的击杀/死亡、全队装备价值；地图上显示飞行中的道具和烟雾、火、闪光、手雷的效果范围 |
| 实时按键显示 | 回放时显示一名玩家此刻按着的键：W/A/S/D、静步（Shift）、蹲（Ctrl）、跳（空格）和鼠标左右键，按下的键亮起；跟随在地图或名单上点选的玩家，没有点选时跟随正在复盘的玩家，阵亡后隐藏。面板不遮挡地图：宽屏复盘工作台上放在该玩家所在队伍名单的底部，较窄、较矮的屏幕和手机上放在地图下方。按键来自 demo 里的 usercmd 记录，没有这类数据的 demo 不显示（目前只在 BLAST.tv 的 GOTV demo 上验证过）；较早上传的比赛在后台重新解析后才会出现。面板的视觉设计改编自 [cs2-sandbox](https://github.com/bugkingZHT/cs2-sandbox)（MIT，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)） |
| 回合经济 | 回合条下标出两队每回合的经济类型（手枪局 / 全起 / 强起 / 半起 / ECO，按冻结时间结束时的装备价值和余钱判断），可按类型和队伍筛选回合；"经济"面板画出每回合两队的装备价值，并统计各经济类型的回合数和胜场。旧比赛没有经济数据时不显示 |
| 道具反查 | 回放区的"道具反查"页：按道具类型、投掷队伍或玩家、回合范围筛选，在地图上拖框选出落点区域，列出落在选区里的每一颗；点"看这颗"回到战术回放、停在出手前 2 秒，并只突出投掷者（"显示全部"恢复）。旧比赛在后台补齐道具数据前，这一页会提示稍后刷新 |
| 个人复盘 | 默认用登录的 Steam 账号匹配比赛中的玩家（本地 development 模式默认匹配 xelex），也可切换成其他玩家；只列出该玩家的建议，按回合分组、回合内按严重程度排序，一键跳到最值得回看的一条 |
| 建议 | 按回合分组；可按严重程度、规则和关键词筛选；"查看这一刻"跳到事件前几秒；每条建议可评价"有帮助 / 无关 / 判断不足"。每次阵亡最多一张卡，标出击杀武器和阵亡前后双方人数，输掉的回合另标「回合输了」，站位过远等同一次阵亡的其他原因附在卡片里；建议多于 5 条时，「全部回合」视图（没有筛选和搜索时）顶部先列出「本场最值得回看」的 5 条（按回合输赢、首个阵亡、人数劣势排序），下面的回合分组只列其余的 |
| 建议评价 | 评价按事件 ID 保存，重新解析和规则更新后的后台重算之后，建议还在就仍然有效；按规则汇总的评价是调整规则阈值的依据，见 [coaching_feedback_v1](docs/coaching_feedback_v1.md) |
| 第一人称片段 | 围绕某条建议或当前时刻创建短片段任务，由独立渲染机完成；生产环境默认关闭（`RENDER_CLIPS_ENABLED=0`）。默认 `RENDER_WORKER_MODE=fallback` 下，片段任务会立即以 `RENDER_WORKER_UNAVAILABLE` 失败；`external` 模式由操作者启用的 Windows 渲染机（CSDM + HLAE + FFmpeg，FFprobe 校验）完成 |
| 账户与数据 `/account` | 账户信息、网站保存了哪些数据；删除账户及全部数据（只对 Steam 登录的真实账户开放，所有设备上的登录同时失效） |
| 隐私说明 `/privacy` | 公开页面，不需要登录：保存什么、存在哪里、保存多久、删除后还剩什么；每个页面的页脚都有链接和"与 Valve 无关联"声明 |

**明确不做**：
- 不做 AI 聊天或模型生成的文字；
- 不控制用户电脑、不读取本地文件、不录屏；
- 不在 API 或解析容器里运行 CS2 / Steam / OBS / ffmpeg；
- 不把用户上传的 MP4 当作主流程；
- 不把 `.dem`、回放帧或视频存进 PostgreSQL。

浏览器不能直接播放 `.dem`：它是游戏状态与事件记录，不是视频流。

## 快速开始

需要 Docker。启动整套本地环境（前端、API、解析 worker、Postgres、Redis）：

```bash
docker compose up --build
```

- 比赛库：http://localhost:3000/dashboard
- API：http://localhost:8000 · 健康检查 `/health` · 诊断 `/diagnostics`（仅 development/test；production 返回 `404`）

本地为 development 模式，不需要登录。可以用"示例比赛"快速生成一场模拟数据，也可以上传真实 `.dem` 走完整解析流程。

Compose 里的前端是生产构建（`frontend/Dockerfile.preview`，`next build` 后 `next start`），和线上部署是同一种构建；改了前端代码要重新 `--build` 才能看到（启动脚本每次都带 `--build`，代码没变时命中缓存）。`next build` 会做 lint 和类型检查，有错误时前端镜像构建失败。

已配置好的 Windows 机器可以双击根目录的 **`Start CS2 Coach.cmd`**，或运行：

```powershell
& .\scripts\start-local.ps1        # -SkipRenderer 只启动网页；-PreflightOnly 只检查本机配置
```

只改前端时，可以只启动后端服务，再单独跑前端开发服务器（两者都占用 3000 端口，不要同时运行 Compose 里的 frontend）。开发服务器用的是开发版 React，每帧开销是生产构建的数倍，判断回放是否流畅请用 Compose 里的生产构建：

```bash
docker compose up --build -d api worker postgres redis
cd frontend && npm install && npm run dev
```

在 Mac 上开发的启动与数据交接见 [macos_development_windows_release_v1](docs/macos_development_windows_release_v1.md)。

## 架构

```text
浏览器 ── Next.js 15 前端（App Router）
            │
            ▼
         FastAPI API ──── PostgreSQL   账号、Steam 身份、比赛元数据、任务、建议、评价、上传账本、删除任务
            │        ├─── Redis        解析任务队列、登录会话
            │        └─── 对象存储     .dem 原文件、回放 JSON、视频（开发用本地目录，生产用私有 S3 兼容存储）
            ▼
         解析 worker（独立子进程解析，有超时与内存上限）→ 规则分析 → 写回回放与建议
            ⋮
         独立渲染机（可选，操作者手动启用）：领取片段任务 → 渲染 → 回传 MP4
```

- **上传流程**：先流式写入隔离区，同时计算真实长度和 SHA-256，校验通过后才转为正式文件并创建解析任务。
- **存储与数据库**：所有文件都经过 `backend/app/services/storage/` 的统一接口读写；PostgreSQL 只存元数据和存储引用。
- **任务队列**：带租约和数据库对账。worker 崩溃后，任务会自动回到队列。
- **比分摘要**：解析完成时把双方战队名、开局阵营和最终比分存进 `demos.match_summary`（按玩家帧判定每回合阵营，半场和加时换边都算对），比赛库和复盘页从 `matchSummary` 读取；此前已完成、或摘要版本低于当前版本（2）的比赛由 worker 空闲时回填，只读 replay 和源 `.dem`，不改 replay。阵营、队伍和比分的判定规则前后端各实现一份，写在 `match_summary.py` 和 `frontend/lib/match-stats.ts` 的注释里，由共享用例 `fixtures/match-rules/` 固定。见 [api_reference_v1](docs/api_reference_v1.md)。
- **后台补算**：worker 空闲时（队列里有任务就让出）每轮最多处理一场已完成的比赛，不改比赛状态和时间，也不占上传次数。
  - 回放早于当前契约的比赛，从存储的 `.dem` 重新解析一次（`REPLAY_UPGRADE_ENABLED` / `REPLAY_UPGRADE_MAX_ATTEMPTS` / `REPLAY_UPGRADE_RETRY_SECONDS`）；这一步不重新分析建议。
  - 建议早于当前规则版本 `COACHING_RULES_VERSION` 的比赛（真实解析的比赛和 Steam 导入，不含示例比赛；有待升级的回放时先等升级完成），用已存的回放重新跑规则、替换建议（`COACHING_RECOMPUTE_ENABLED` 默认开启，`COACHING_RECOMPUTE_MAX_ATTEMPTS` 默认 3 次，`COACHING_RECOMPUTE_RETRY_SECONDS` 默认 900 秒、每失败一次翻倍；次数用完的比赛保留旧建议，下次提升规则版本后重新计数）。id 没变的建议保留评价；不再生成的建议，评价行保留但不显示、不计入汇总。
  - 改了规则输出就提升 `COACHING_RULES_VERSION`（`backend/app/analysis/version.py`），已有比赛会在后台逐场重算，见 [coaching_feedback_v1](docs/coaching_feedback_v1.md)。

```text
frontend/        app/ 页面 · components/ 界面组件 · lib/ 客户端与纯函数 helper · public/maps/ 雷达图
backend/         app/api/ 路由 · app/analysis/ 规则 · app/parser/ demoparser2 适配与归一化
                 app/services/ 领域服务（demo_service/、storage/ 为包）· app/workers/ 解析 worker
render-worker/   独立渲染机的 runner 与适配器（fake / manual / CSDM），见 render-worker/README.md
docs/            接口与配置参考、运维手册、验收记录
deploy/          生产 Caddyfile 与 env 模板（配合 docker-compose.prod.yml）
scripts/         verify.sh · rc_check.sh · cloud_preview_smoke.py · Windows 启动脚本 · deploy/ VPS 部署脚本
fixtures/        前后端共用的测试用例（match-rules/：每回合阵营与比分规则；round-economy/：每回合经济类型）
```

## 开发与验证

```bash
./scripts/verify.sh      # 与 CI 相同：后端编译/测试、render-worker、ruff、mypy、前端测试/lint/类型检查/构建
./scripts/rc_check.sh    # verify.sh + Docker 构建启动 + 健康检查 + 预览冒烟（发布候选用）
```

`verify.sh` 不会在第一个失败处停下，最后会汇总所有失败项。它优先使用 `.venv/` 里的 Python，也可以用 `PYTHON=/path/to/python ./scripts/verify.sh` 指定。

单独运行：

```bash
cd frontend && npm run lint && npm run typecheck && npm test   # Vitest + Testing Library 组件/页面测试
cd frontend && node lib/demo-library.test.mjs                   # 每个 lib/*.test.mjs 都能直接运行
cd frontend && npm run build

# Windows 上把 .venv/bin/python 换成 .venv/Scripts/python.exe
PYTHONPATH=backend .venv/bin/python -m unittest discover backend/tests
.venv/bin/python -m ruff check .       # 规则与豁免理由见 ruff.toml
.venv/bin/python -m mypy               # 范围与遗留问题见 mypy.ini
```

- **测试约定**：交互组件和页面状态的改动（加载、轮询、失败、重试）要附带同目录下的 `*.test.tsx`，用 mock 掉的 `@/lib/api` 挂载组件；测试数据放在 `frontend/lib/test-fixtures/review.ts`。前后端必须一致的规则用 `fixtures/` 下的共享 JSON 用例固定：`fixtures/match-rules/*.json` 同时由 `backend/tests/test_match_side_rules.py` 和 `frontend/lib/match-side-rules.test.mjs` 逐个运行；`fixtures/round-economy/*.json` 同时由 `backend/tests/test_round_economy.py` 和 `frontend/lib/round-economy-fixtures.test.mjs` 运行，固定 `frontend/lib/round-economy.ts` 与后端移植 `backend/app/analysis/round_economy.py` 的经济类型判定。改规则时两边和用例一起改。
- **手动检查**：界面改动还需要在浏览器里走一遍比赛库和复盘页，清单见 [release_candidate_qa_v1](docs/release_candidate_qa_v1.md)。
- **更多命令**：常用 API 调用（上传、状态轮询、回放、建议、评价、删除、渲染任务）见 [API Reference](docs/api_reference_v1.md) 和 `AGENTS.md`。

## 配置

完整列表（90 项）见 [Configuration Reference](docs/configuration_reference_v1.md)。最常用的：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AUTH_MODE` | 未设置（必填） | `development` / `test` / `production` |
| `AUTH_PROVIDER` | 未设置（production 必填） | `steam`，或兼容路径 `oidc` |
| `STEAM_LOGIN_ALLOWLIST` | 未设置 | 内测邀请名单：逗号分隔的 Steam ID64，或 `*` 表示所有 Steam 账号；production + Steam 登录时必填。修改后需要重建 API 容器（`up -d`），`docker compose restart` 不会读取新值 |
| `DEMO_UPLOAD_DAILY_LIMIT` / `DEMO_ACTIVE_PARSE_LIMIT` / `PARSE_QUEUE_GLOBAL_LIMIT` | `10` / `2` / `50` | 仅 production：每人滚动 24 小时上传数（按上传账本计，删除比赛不退还）、每人同时处理中的比赛数、全站处理中的比赛数；`0` 表示不限 |
| `RENDER_CLIPS_ENABLED` | `0` | 仅 production：为 `1` 时才开放生成和重试第一人称片段 |
| `FRONTEND_PUBLIC_URL` / `BACKEND_PUBLIC_URL` | `http://localhost:3000` / `:8000` | production 必须是同源 HTTPS |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | 浏览器访问 API 和媒体的地址 |
| `NEXT_PUBLIC_PRIVACY_CONTACT` / `NEXT_PUBLIC_DATA_REGION` | 未设置 | 构建时写进前端，显示在 `/privacy`：隐私问题和删除请求的联系方式（邮箱、网址或文字；VPS 部署必填，`deploy.sh` 会检查）和服务器所在地区；未设置时分别说明"本站没有公开联系方式"和显示"海外 VPS，具体地区由站长部署时选定" |
| `DATABASE_URL` / `REDIS_URL` | 本地默认值 | API 与 worker |
| `ARTIFACT_STORAGE_BACKEND` / `OBJECT_STORAGE_BUCKET` | `local` / 未设置 | production 必须是 `s3` 和一个私有 bucket |
| `STEAM_WEB_API_KEY` | 未设置 | 仅服务端使用，production 必填 |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | 仓库内的开发 key | production 必须换成 32 随机字节的 URL-safe base64，例如 `python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `RENDER_WORKER_MODE` / `RENDER_WORKER_TOKEN` | `fallback` / `dev-render-worker-token` | production 必须更换 token |
| `MAX_DEMO_UPLOAD_BYTES` | `1073741824` | 单个 `.dem` 的实际字节上限 |
| `DEV_USER_ID` | `dev-user` | 仅 development/test |

**production 启动检查**：以下任何一项不满足，服务都会拒绝启动：
- 显式的登录方式；
- HTTPS、`__Host-` cookie、前后端同源、精确的 CORS；
- 真实的 Steam Web API key；
- Steam 登录时的 `STEAM_LOGIN_ALLOWLIST`；
- 非开发用的加密 key 和渲染 token；
- 私有的 S3 兼容存储。

**凭证与数据**：
- production 凭证不能出现在 `NEXT_PUBLIC_*`、源码、日志、提交的 env 文件或浏览器响应里。
- 真实 `.dem`、视频、回放缓存和本地 `.env` 必须留在 git 忽略的目录中。

## 部署

- **生产（单台海外 VPS）**：`docker-compose.prod.yml` 叠加在 base 和 preview 之上，加入 Caddy（自动 HTTPS、同源路由）；`scripts/deploy/` 提供初始化、部署/回滚、生产冒烟、每日备份和恢复演练。步骤见 [vps_deploy_v1](docs/vps_deploy_v1.md)。
- **预览环境**：`docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build`，使用 production 模式和生产构建的前端。必填变量、冒烟命令与回滚步骤见 [cloud_preview_deploy_v1](docs/cloud_preview_deploy_v1.md)。
- **冒烟测试**：`scripts/cloud_preview_smoke.py` 会读取 `/auth/me` 返回的能力开关，跳过 production 隐藏的模拟数据和片段步骤。对 production 预览需要提供已登录的会话 cookie 和一份真实 `.dem`；每次运行都会占用该账号滚动 24 小时上传配额（`DEMO_UPLOAD_DAILY_LIMIT`）中的一次。
- **上线检查清单**：[deployment_readiness_v1](docs/deployment_readiness_v1.md) 和 [release_candidate_qa_v1](docs/release_candidate_qa_v1.md)。

在正式开放之前，[上线计划](docs/rules_2d_beta_launch_v1.md) 中还有这些没有完成：解析器的 CPU、磁盘与输出隔离（目前只有超时和内存上限），数据库迁移，自动部署（目前是手动运行 `scripts/deploy/deploy.sh`），监控告警，在真实部署上完成一次备份恢复演练，存储孤儿清理，以及真实 demo 语料的强制冒烟门禁。

## 安全与隐私边界

- **身份**：production 使用 Steam OpenID 2.0，验证 SteamID64 后映射到不透明的账号 ID，会话保存在 Redis，通过 `HttpOnly` cookie 传递。不在邀请名单里的账号不会被创建；从名单移除后，已有会话立即失效。
- **数据隔离**：所有用户接口都按账号隔离；私有媒体在每次读取时都会重新校验会话和归属，不存在公开的静态文件目录。
- **开发工具**：模拟数据、手动 MP4、模拟渲染和 API 文档页在 production 一律返回 `404`；前端根据 `/auth/me` 返回的能力开关隐藏对应入口。
- **防滥用**：
  - 上传配额在接收文件之前就会检查；
  - 并发的上传和重试通过加锁排队，不能绕过同时处理数的上限；
  - 解析在子进程里运行，默认超时 20 分钟、内存上限 4 GB。
- **Steam 比赛授权**：与登录分离。Game Authentication Code 和分享码在服务端以 AES-256-GCM 加密保存，不会回传给浏览器。
- **开发模式**：可以用 `X-Dev-User-Id` 模拟不同用户，这个请求头在 production 会被拒绝。

**隐私**：下面的事实与公开的 `/privacy` 页面（`frontend/app/privacy/page.tsx`）一致。改动数据的收集、保存或删除方式时，两边要一起改，同时更新 [data_deletion_v1](docs/data_deletion_v1.md)。

- **范围**：邀请制的 CS2 比赛复盘网站，只做 2D 回放和规则建议。不使用 AI 大模型，没有广告，也没有统计分析。
- **登录**：
  - Steam 登录只返回 SteamID64，网站拿不到 Steam 密码。
  - 服务器用 Steam Web API 读取公开昵称和头像地址并保存，每次登录更新。
  - 未受邀的账号不会被保存，但登录时仍会向 Steam 查询一次公开资料。
- **上传的比赛**：
  - 原始 `.dem` 按原样保存（用于重新解析），另外保存解析出的回放数据和建议。回放数据包括每名玩家的位置、血量、金钱、武器、携带的道具和按键记录（移动、静步、蹲、跳和鼠标左右键），以及每颗道具的轨迹和落点，全部来自 `.dem` 本身；较早上传的比赛会在后台用已保存的 `.dem` 重新解析一次来补上。规则更新后，网站会在后台用已保存的回放数据重新计算建议，不收集新的数据；有的建议可能因此不再显示，用户对它的评价仍随比赛保存，直到删除这场比赛或账户。
  - `.dem` 里包含同场所有玩家的 SteamID64、游戏内昵称、位置和击杀记录，只对上传者本人可见。其他玩家如希望移除，可以联系站长。
  - 另外保存的只有：用户对建议的评价（有帮助 / 无关 / 判断不足），以及改过的比赛名。
- **Steam 比赛记录（可选）**：只有用户主动关联时才保存，内容是加密后的游戏验证码和比赛分享码。每次点"同步"才向 Valve 查询；断开关联即删除。
- **Cookie 和浏览器存储**：
  - 只有两个必需的 cookie：`__Host-cs2_session` 保持登录 1 小时，`__Host-cs2_steam_state` 只在登录过程中存在 5 分钟。没有统计或广告 cookie。
  - localStorage 只存一项"你在比赛里选的玩家"偏好（含本人 SteamID64）。删除账户时在当前浏览器清除。
- **日志**：服务器访问日志记录 IP 地址、浏览器标识和访问的页面地址（含搜索词），用于排查故障和防滥用。日志按大小轮转（每个服务最多约 50 MB），不按时间删除。
- **存放位置和第三方**：
  - 网站跑在一台海外 VPS 上，地区由 `NEXT_PUBLIC_DATA_REGION` 写在隐私页。比赛文件和备份在 Cloudflare R2 私有存储桶。
  - 经手数据的第三方只有：VPS 服务商、Cloudflare（存储）、Valve/Steam（登录、公开资料、用户主动开启的比赛记录同步）、Let's Encrypt（只签发 HTTPS 证书）。不出售，也不共享给其他人。
- **保存与删除**：
  - 账户和比赛一直保存，直到用户删除；会话 1 小时后过期。
  - 可以永久删除单场比赛（`DELETE /demos/{id}`），也可以在 `/account` 删除账户和全部数据（`DELETE /auth/account`，仅 production），所有设备上的登录同时失效。
  - 删除立即作用于数据库和存储，存储清理失败会自动重试。每日备份里的副本最多再保留 30 天；从备份恢复后，站长会重做那之后的删除。
  - 用户自己无法删除时（同场其他玩家的移除请求、已被移出邀请名单的用户），站长用运维命令 `python -m app.cli.delete_data` 代为删除，走同一套删除流程（见 [data_deletion_v1](docs/data_deletion_v1.md#代用户删除)）。
  - 邀请名单（受邀者的 SteamID64）保存在服务器配置里，删除账户不会改动它；隐私页和"账户与数据"页都写明了。
  - 删除比赛不会恢复当天的上传次数。
- **与 Valve 无关联**：每个页面的页脚都写明"本站与 Valve Corporation 无关联。Counter-Strike、CS2 和 Steam 是 Valve 的商标。"隐私页的联系方式由 `NEXT_PUBLIC_PRIVACY_CONTACT` 配置。

细节见 [steam_auth_accounts_v1](docs/steam_auth_accounts_v1.md)、[production_auth_owner_private_media_v1](docs/production_auth_owner_private_media_v1.md)、[object_storage_safe_artifact_intake_v1](docs/object_storage_safe_artifact_intake_v1.md) 和 [data_deletion_v1](docs/data_deletion_v1.md)。

## 已知限制

- **建议是否真的有用还没有经过真人验证。** 规则阈值来自 Dust II 上的手工调整；评价功能已上线，要靠内测数据来校正。
- **地图坐标精度不一**：只有 Dust II 和 Nuke 使用真实的 CS2 overview 校准；Mirage、Inferno、Ancient、Anubis 仍是手估范围；其他地图按单场比赛的站位范围推算。
- **不能自动下载比赛**：Valve 没有公开个人比赛的下载接口，仓库也没有获得许可的来源，所以只能手动上传 `.dem`。相关导入代码已经写好并有测试，但在这个版本里走不到。
- **解析是单 worker 串行执行**：崩溃恢复是自动的，但有分钟级延迟。租约为 60 秒；如果 Redis 数据全部丢失，排队中的任务约 5 分钟（`PARSE_REDISPATCH_AFTER_SECONDS`）后重新投递，正在解析的任务要等 `PARSE_RECLAIM_AFTER_SECONDS`（默认 30 分钟）。
- **CI 不解析真实 `.dem`**：真实文件只在可选的样本冒烟测试里校验。
- **健康检查很粗**：`/health` 在数据库、Redis 或 worker 配置任一项检查失败时返回 HTTP 503 `{"status":"degraded"}`（正常为 200 `{"status":"ok"}`），但不说明是哪一项；production 下要看 `docker compose logs api` 才能定位。
- **数据覆盖有限**：
  - 解析帧是采样数据；炸弹和道具事件尽量提取，不保证完整；
  - 没有视线信息（帧里没有玩家朝向）。
  - 实时按键显示依赖 demo 里的 usercmd 数据，只在 BLAST.tv 的 GOTV demo 上验证过；匹配、FACEIT、第一人称 POV 等来源可能没有这类数据，这时不显示按键面板。
- **第一人称片段**：只在一台本地 Windows 渲染机上验收过；排队超过 `RENDER_CLIP_QUEUE_TIMEOUT_SECONDS`（默认 30 分钟）的任务会被标记失败，可以重试。
- **单场失败比赛可以无限次重新处理**；Redis 客户端没有设置超时。
- **解析器隔离还不完整**：只有超时和内存上限，还没有 CPU、磁盘和输出的限制。
- **Steam 登录尚未在真实部署中验证**：真实 HTTPS 回调、publisher key 资格、Game Authentication Code 行为和限流，都还需要在一次真实部署上冒烟。
- **删除追不回备份**：已删除的数据在备份里最多再保留 30 天。从备份恢复数据库会把备份之后删除的数据带回来，要按 [VPS 部署](docs/vps_deploy_v1.md) 第 7 步用 `python -m app.cli.delete_data` 重做这些删除。
- **删除账户后可以重新注册**：仍在邀请名单里的 Steam 账号重新登录，会得到一个全新的空账户，上传次数也从零算起；要禁止此人登录，只能把他移出 `STEAM_LOGIN_ALLOWLIST`。移出名单不会删除他的数据，需要时先用运维命令代为删除。

## 路线图

当前方向是**网站优先**：先以邀请制内测验证建议是否真的有用，Windows 桌面安装包暂缓（见 [desktop_distribution_v1](docs/desktop_distribution_v1.md)）。按顺序：

1. 完成上线计划剩下的阶段：解析器资源隔离，数据库迁移，自动部署与回滚，监控告警，备份恢复；配置 HTTPS 入口，并在真实 HTTPS 部署上跑一次 Steam 登录冒烟。
2. ~~支持真正删除比赛和账号，补上隐私条款~~（已完成：永久删除比赛和账户、`/privacy` 隐私说明页，见 [data_deletion_v1](docs/data_deletion_v1.md)）；~~去掉第三方雷达图~~（已完成：原来的 MIT/GPL 第三方雷达图已换成本项目从 CS2 导航网格渲染的图，见 [scripts/maps](scripts/maps/README.md)）；这些图由 Valve 的游戏数据派生，能否公开分发还没有单独确认，扩大开放前要确认。
3. 邀请少量玩家内测，用评价数据调整规则阈值、去重和排序；校准更多地图。
4. 视内测反馈，再决定是否扩大开放、是否恢复第一人称片段。

## 文档索引

**参考**：
- [API Reference](docs/api_reference_v1.md)
- [Configuration Reference](docs/configuration_reference_v1.md)
- [安全上传与对象存储](docs/object_storage_safe_artifact_intake_v1.md)
- [Steam 登录与账号](docs/steam_auth_accounts_v1.md)
- [Steam 比赛同步](docs/steam_match_sync_v1.md)
- [Steam Demo 导入](docs/steam_demo_import_v1.md)
- [数据隔离与私有媒体](docs/production_auth_owner_private_media_v1.md)
- [建议评价](docs/coaching_feedback_v1.md)
- [数据删除与隐私](docs/data_deletion_v1.md)
- [render-worker](render-worker/README.md)

**上线与运维**：
- [2D 内测上线计划](docs/rules_2d_beta_launch_v1.md)
- [VPS 部署](docs/vps_deploy_v1.md)
- [部署准备](docs/deployment_readiness_v1.md)
- [云端预览部署](docs/cloud_preview_deploy_v1.md)
- [部署目标决策](docs/deployment_target_decision_v1.md)
- [内部预览打包](docs/internal_preview_packaging_v1.md)
- [发布候选 QA](docs/release_candidate_qa_v1.md)
- [样本 Demo 约定](docs/sample_demo_fixture_v1.md)
- [本机启动故障恢复](docs/local_startup_recovery_2026-09-07.md)

**进展与决策**：
- [项目进展](docs/project_status_2026-09-13.md)
- [桌面版方案（暂缓）](docs/desktop_distribution_v1.md)
- [Mac 开发说明](docs/macos_development_windows_release_v1.md)
- [项目审计](docs/cs2_coach_audit_v1.md)
- [前端体验审查](docs/frontend_ux_audit_2026-09-07.md)

**验收记录**：
- [四份真实 Demo](docs/real_demo_acceptance_2026-09-06.md)
- [个人复盘与回放精度](docs/personal_review_acceptance_2026-09-07.md)
- [本机第一人称录制](docs/local_xelex_render_acceptance_2026-09-07.md)
- [片段保存与复用](docs/saved_clip_reuse_acceptance_2026-09-07.md)
- [复盘工作区改版](docs/frontend_redesign_acceptance_2026-09-08.md)
- [真实复盘质量](docs/real_review_quality_acceptance_2026-09-12.md)

**阶段目标（历史记录）**：`docs/*_goal.md`，包括存储边界、解析质量与可靠性、规则分析 V3、回合复盘、渲染片段、可观测性等。当前事实以代码和近期验收记录为准。

## 致谢

雷达图由本项目用 [scripts/maps/build_radars.py](scripts/maps/README.md) 从运营者自己的 CS2 安装里的导航网格渲染（包点取自地图自带的 `func_bomb_target` 触发器，包点里的站立高度取自地图的碰撞数据），不含第三方或 Valve 的雷达美术素材，但图形由 Valve 的游戏数据派生，公开分发的条款尚未单独确认；导航网格的读取参考了 [awpy](https://github.com/pnxenopoulos/awpy)（MIT）。Nuke 的 overview 参数来自 [CS Demo Manager](https://github.com/akiver/cs-demo-manager)，Inferno 与 Anubis 的 overview 参数取自游戏 `game/csgo/pak01_dir.vpk` 里的 `resource/overviews`。详见 [ATTRIBUTION.md](frontend/public/maps/ATTRIBUTION.md)。

复盘页实时按键面板的视觉设计改编自 [bugkingZHT/cs2-sandbox](https://github.com/bugkingZHT/cs2-sandbox) 的 `KeyboardOverlay.vue`（MIT，Copyright (c) 2026 huN7er），用 React 重新实现；许可原文见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
