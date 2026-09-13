# CS2 Demo AI Coach — Rules-Based 2D Beta V1

这是一个网站型 CS2 demo 复盘与规则教练原型。当前项目重点已经从单纯 mock 流程推进到“真实 `.dem` 解析 spike + Demo Library + 回放复盘界面 + deterministic coaching + render clip 合约”。

已完成能力、真实验收数据和后续优先级见 [项目进展与下一步（2026-09-13）](docs/project_status_2026-09-13.md)。

本机渲染试验：操作者明确启用的独立 Windows GPU worker 可以使用 CSDM 与 FFmpeg，通过 CS2 原生录制或兼容版本的 HLAE 生成所选玩家的短片段并回传 App。当前电脑已完成 xelex 的 20 秒真实片段验证，见 [本机录制验收](docs/local_xelex_render_acceptance_2026-09-07.md)。该模式不会成为网站用户的安装或录屏要求；浏览器仍只上传 `.dem`。

当前版本保留规则型 2D 公测 V1 Stage 3 的 artifact/replay 能力，并增加 Steam-first 账号、比赛发现与 Demo 导入适配器基础：Steam OpenID 2.0、正式 account/external identity 映射、Redis opaque browser session、encrypted match-history authorization、近期 sharing-code cursor sync、authenticated `owner_id`，以及复用现有安全 intake 的 owner-scoped import API。由于 Valve 没有公开个人比赛 Demo 下载接口，且仓库尚无正式许可 Provider，运行时默认且仅允许 `disabled`；手动 `.dem` 上传仍是可靠入口。仍没有可靠任务恢复、parser 隔离、生产 observability/backup、OpenAI 调用或生产渲染集群。

## 当前状态

已经具备的能力：

- Docker Compose 本地栈：`frontend`、`api`、`worker`、`postgres`、`redis`。
- Production identity boundary：Steam-first 部署显式选择 Steam OpenID 2.0 验证 SteamID64，再通过 `accounts` / `external_identities` 创建 Redis-backed opaque `HttpOnly` session；所有 user API 从可信 session 派生稳定的 `owner_id`。原 OIDC 实现仅作为 `AUTH_PROVIDER=oidc` 兼容路径；production 缺少 provider 时 fail closed，`DEV_USER_ID` / `X-Dev-User-Id` 仅在显式 development/test mode 可用。
- Steam match discovery：登录账号可提交 Game Authentication Code 和一个初始 Match Sharing Code；服务端 AES-GCM 加密凭证和 cursor，通过 Valve 官方 `GetNextMatchSharingCode` 每次最多发现 20 场，Dashboard 只展示真实 discovery/status，不伪造地图、比分、玩家或 Demo 来源。
- Steam Demo import boundary：`POST /steam/matches/{id}/import` 将发现记录接到可插拔 `DemoSourceProvider`；本 build 只注册 fail-closed 的 disabled provider。未来获正式许可的 source 必须经过 exact-host HTTPS 下载保护和现有 quarantine → SHA-256/type validation → accepted promotion，不能绕过 artifact intake。
- `/dashboard` Demo Library：搜索、状态/地图筛选、排序、bounded 上传/任务轮询、重命名、软归档、空/失败/无结果状态、渲染状态摘要。
- 中文复盘工作区：桌面同屏显示视频或战术回放、播放控制、时间轴与个人建议；身份设置、历史片段和技术工具按需展开。点击建议会定位并显示对应画面，优先复用覆盖该时刻的已保存 POV 视频。见 [前端改版验收](docs/frontend_redesign_acceptance_2026-09-08.md)。
- xelex 真实复盘质量：四场建议事实核对、站位高度/提示文案/包点编号修复、名称保留，以及新上传、真实片段生成与重播的完整验证证据见 [真实复盘质量验收](docs/real_review_quality_acceptance_2026-09-12.md)。
- Upload/parser observability：demo list/detail responses 包含 compact ingestion snapshot，失败解析有短错误、attempts、stale/active/retryable 状态，并支持 owner-scoped retry。
- Diagnostics boundary：`GET /diagnostics` 只在 development/test 提供 compact 排障信息，production 返回 `404`；`GET /demos/{demo_id}/diagnostics` 始终要求登录并按 owner 隔离。public `GET /health` 只返回 coarse status。
- Private media：默认视频使用 `/demos/{demo_id}/media/video`，保存的片段使用 `/demos/{demo_id}/render/jobs/{job_id}/media/video`；GET/HEAD/Range 在字节交付时再次验证 session、owner 和 artifact path，没有 public `/media/videos` static mount。
- Safe Artifact Intake：公开上传只接受 `.dem`；source 先流式写入 private quarantine，同时计算真实长度和 SHA-256，验证后 promotion 为 owner/demo-bound accepted artifact，只有 accepted source 才能创建 parser job。
- Provider-neutral artifact storage：development/test 使用 private local adapter，production 必须配置 private S3-compatible adapter；source、replay、video 的逻辑 reference 不暴露 bucket、object key、provider URL 或本地路径。
- Mock demo flow：快速生成合成 replay、coaching events 和 mock first-person shell。
- Real demo parser spike：主产品入口上传 `.dem`，后端队列异步解析；archive upload 只保留为开发兼容路径。
- Replay contract：回合、玩家、采样帧、击杀/死亡、compact parser events、地图 metadata、视频 metadata、contract diagnostics。
- Demo detail review：first-person shell/video、tactical map、timeline、round selector、round review、coaching panel、Replay Contract diagnostics 同步到同一个 tick/round state。
- Personal review：默认匹配 `xelex`，可保存昵称或 Steam ID、明确选择比赛中的其他玩家；建议、关键事件、回合建议计数和跳转跟随所选玩家。
- Rules-based coaching：固定规则生成事件，不调用 LLM，不生成不透明 AI 文案。
- Tactical map assets：Dust II、Mirage、Inferno、Ancient、Nuke、Anubis radar 支持；Dust II 和 Nuke 使用 CS2 overview transform，其余是 approximate bounds。Nuke 支持上下层与跟随所选玩家楼层。
- Manual MP4 binding：仅用于开发和 QA，支持上传 `.mp4` 并保存 tick/video calibration。
- `render_clip` boundary：用户可围绕 coaching event 或当前 tick 创建短 POV clip job。
- Render Worker V1 contract：token-gated manifest claim、media upload、terminal-safe result callback。
- `render-worker/`：fake/manual 开发适配器，以及操作者显式启用的 CSDM 本机真实录制适配器；真实视频通过 FFprobe 校验后回传。
- Parser quality regression fixtures：backend/frontend compact fixtures 覆盖 legacy replay、malformed optional fields、missing event families、coaching evidence 和 degraded detail states。

明确没有做的事情：

- 不提供密码、Steam Guard、团队或通用账号管理 UI；只提供紧凑的 Steam 登录、当前账号和退出入口，浏览器不处理 provider token。
- 不实现或启用社区 replay CDN/share-code 解码，不抓取 Steam 私有网页、不模拟 Game Coordinator；发现的比赛继续支持独立手动 `.dem` 上传兜底。
- 不在 API 或解析 worker 容器里启动 CS2、Steam、OBS、ffmpeg。
- 网站不控制用户电脑、不读取用户上传后的本地文件、不录屏；独立录制工具只在操作者指定并启用的渲染电脑运行。
- 不把用户上传 MP4 设计成主产品路径。
- 不把 replay 大帧数据、raw parser dataframe、大视频或 `.dem` 文件塞进 PostgreSQL。
- 不调用 OpenAI 或其它 LLM；coaching 结果是确定性规则输出。

## 产品边界

目标产品流是 demo-first：

```text
user uploads .dem
  -> backend parses rounds, players, sampled positions, kills, deaths, bomb/utility events
  -> rules analyzer creates deterministic coaching events
  -> website immediately enables tactical replay, round review, timeline, and coaching review
  -> user clicks an event or tick
  -> if rendered footage exists, first-person video plays in sync
  -> otherwise the main canvas shows the usable tactical replay
  -> user may request a render_clip job for a short selected range
```

浏览器不能直接播放 CS2 `.dem`。`.dem` 是游戏状态/事件记录，不是视频流。真实第一人称视频必须作为异步增强，由我们控制的 Windows/Linux GPU worker 渲染短 clip，再把 mp4/HLS metadata 写回 replay contract。

## 架构

```text
frontend (Next.js)
  -> FastAPI API
    -> PostgreSQL metadata: accounts, external identities, encrypted Steam connections/matches, owner-scoped demos, demo_jobs, coaching_events
    -> Redis queue: parse/render job dispatch
    -> artifact storage service: uploads, replay blobs, summaries, videos
  -> worker process
    -> mock_parse / real_parse / mock_render / render_clip status handling
  -> standalone render-worker (fake / manual / opt-in CSDM)
    -> external process that calls token-gated render-worker API
```

PostgreSQL 只存可索引的元数据、backend-neutral logical reference、compact ingestion/render metadata 和 coaching event rows。Replay frames、parser event contract、video metadata 和 compact replay diagnostics 都在 replay JSON artifact 内；上传 demo、replay artifact 和视频文件始终留在 private artifact storage，不进入 PostgreSQL。

Artifact storage 通过 `backend/app/services/storage.py` 的 provider-neutral contract 统一出入口。服务端生成的 V1 logical reference 绑定 lifecycle state、artifact kind、opaque owner、demo 和随机 artifact id；客户端 filename 不参与 physical object key。Development/test 的 local adapter 使用 API/worker 共享的 private Compose volume，production 选择 private S3-compatible adapter。S3 path 对 seekable private upload 做 1 MiB hash pass 后直接上传，并用 generation-bound、destination-conditional server-side copy promotion，不创建第二份整文件 scratch。Legacy `local://uploads|replays|videos/...` 只保留为本地旧数据读取兼容，不是新 source 通过 intake 的证明。

不要把 `.dem`、replay JSON、大视频或 raw parser dump 存入 PostgreSQL，也不要把 cloud credentials、bucket-specific code 或 public object URL 分散到 API route、worker 或 parser。完整 Stage 3 生命周期、错误分类和验收合同见 `docs/object_storage_safe_artifact_intake_v1.md`。

## Production Identity and Owner Boundary

The Steam-first production path explicitly sets `AUTH_PROVIDER=steam`; production startup rejects a missing provider instead of silently changing identity authority. `GET /auth/steam/login` starts Steam OpenID 2.0; the backend pins Valve's provider endpoint and verifies state, exact realm/`return_to`, claimed-ID XRDS discovery, required signed fields, SteamID64, nonce freshness/replay, and the assertion through direct `check_authentication` before touching account or session state. Steam OpenID only supplies SteamID64. Nickname/avatar enrichment uses the server-only `STEAM_WEB_API_KEY`; profile failure degrades to a generic account label and never fails login, while Stage 2 production startup requires the same publisher key for match-history sync.

`accounts` owns stable opaque `owner_v1_...` identifiers. `external_identities` enforces unique `(provider, subject)` and unique `(owner_id, provider)` mappings. There is no implicit profile-based merge or Phase 1 identity-binding endpoint. A future binding flow must re-authenticate both sides and reject an identity already owned by another account. The existing OIDC implementation remains available only when explicitly selected with `AUTH_PROVIDER=oidc` and resolves through the same account/session boundary.

After verification, the backend creates one opaque Redis session and sends only an `HttpOnly`, `SameSite=Lax`, `Secure`, `__Host-` production cookie. API demo/upload/replay/coaching/library/render/private-media routes continue deriving owner from that session and reuse their existing owner-scoped queries. `GET /auth/me` returns compact display metadata without SteamID64 or `owner_id`; `POST /auth/logout` revokes the session.

Match-history authorization is separate from OpenID. The Dashboard accepts only a Game Authentication Code and one initial Match Sharing Code; SteamID64 is resolved from the authenticated external identity. Credentials, cursor, and discovered sharing codes are AES-256-GCM encrypted server-side and never returned to the browser. Manual Sync now uses Valve's fixed HTTPS `GetNextMatchSharingCode/v1` endpoint, persists bounded retry/repair state, and stops after at most 20 new codes. Valve does not return map, score, players, match time, or a supported Demo URL from this endpoint, so those fields stay absent until a real `.dem` is parsed. The import adapter defaults to a non-networked disabled provider and cannot be enabled by setting an arbitrary hostname or provider name. See `docs/steam_match_sync_v1.md` and `docs/steam_demo_import_v1.md`.

本地测试仍可显式设置 `AUTH_MODE=development` 或 `AUTH_MODE=test`，用 `DEV_USER_ID` 和请求头模拟 owner：

```bash
X-Dev-User-Id: owner-a
```

该 header 在 production mode 被忽略或拒绝，绝不作为 production identity fallback。Steam/account 合同见 `docs/steam_auth_accounts_v1.md`；共享 cookie、owner、private media 和 acceptance matrix 见 `docs/production_auth_owner_private_media_v1.md`。

## 项目结构

```text
frontend/
  app/                    Next.js App Router pages
  components/             replay, coaching, upload UI
  lib/                    API client, review helpers, map config, time sync
  public/maps/            CS2 radar images and attribution
  types/                  frontend replay/demo/coaching types

backend/
  app/api/                FastAPI routes
  app/analysis/           deterministic rules analyzer
  app/models/             SQLAlchemy models
  app/parser/             demoparser2 adapter, normalizer, map config
  app/schemas/            Pydantic response/request models
  app/services/           demo/upload/replay/video/render job and storage services
  app/workers/worker.py   Redis queue worker entrypoint
  tests/                  backend unit tests
  tests/fixtures/         compact regression fixtures

render-worker/
  runner.py               external worker CLI
  adapters/               fake video, manual CS2, and opt-in CSDM adapters

docs/
  steam_auth_accounts_v1.md
  steam_match_sync_v1.md
  production_auth_owner_private_media_v1.md
  deployment_target_decision_v1.md
  internal_preview_packaging_v1.md
  release_candidate_qa_v1.md
  *_goal.md               prior phase goals and design notes
```

## 本地运行

这台已配置 xelex 录制环境的 Windows 电脑，双击仓库根目录 **`Start CS2 Coach.cmd`**。也可以在 PowerShell 运行：

```powershell
& .\scripts\start-local.ps1
```

入口会等待 Docker 引擎，加载本机 loopback/external-worker 配置，启动 App、CSDM 数据库及录制进程，并确认健康状态。成功后双击入口自动打开 `/dashboard`；失败会保留错误窗口。它保留现有 demo 和视频，不重建镜像或清除数据。只启动网页可加 `-SkipRenderer`；只检查本机配置文件可加 `-PreflightOnly`。本机配置和软件位于 ignored `.local/`，此入口不负责在新电脑上安装录制软件。录制进程只处理网页提交的片段任务。

其他开发环境首次构建完整本地栈（不启用上述本机录制环境）：

```bash
docker compose up --build
```

打开：

- Frontend: http://localhost:3000
- Dashboard: http://localhost:3000/dashboard
- API: http://localhost:8000
- Health: http://localhost:8000/health
- Diagnostics (development/test only): http://localhost:8000/diagnostics

API 健康检查：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
```

`/health` 只返回 coarse `status=ok|degraded`，不会泄漏 dependency、URL 或 storage 配置。development/test 的 `/diagnostics` 返回 compact readiness、Redis queue/worker heartbeat、job counts、recent failed job summary 和 render-worker inferred status；production 对该 system endpoint 返回 `404`。它不暴露本地 storage path、env dump、token、stack trace、raw parser data 或上传内容。

单独运行 frontend：

```bash
cd frontend
npm run dev
```

一键本地验证：

```bash
./scripts/verify.sh
```

该脚本会按顺序运行 backend compile、backend unit tests、render-worker compile、render-worker unit tests、frontend lint、frontend typecheck 和 frontend build。也可以单独运行常用命令：

```bash
cd frontend
npm run lint
npm run typecheck
npm run build
```

```bash
python3 -m compileall backend/app
PYTHONPATH=backend python3 -m unittest discover backend/tests
```

Frontend helper regression tests live next to the helpers. `scripts/verify.sh` runs every `lib/*.test.mjs` suite; individual suites can also be run directly:

```bash
cd frontend
node lib/demo-library.test.mjs
node lib/replay-diagnostics.test.mjs
node lib/replay-events.test.mjs
node lib/round-review.test.mjs
node lib/coaching-review.test.mjs
node lib/replay-quality-fixtures.test.mjs
node lib/map-config.test.mjs
node lib/personal-review.test.mjs
node lib/replay-frames.test.mjs
node lib/steam-matches.test.mjs
node lib/auth.test.mjs
node lib/private-media.test.mjs
```

Release-candidate validation:

```bash
./scripts/rc_check.sh
```

`rc_check.sh` runs `./scripts/verify.sh`, Docker build/up, `/health`, `/diagnostics`, and cloud preview smoke. If `SAMPLE_DEMO_PATH` is set, it also runs sample smoke; set `REQUIRE_SAMPLE_DEMO=1` to fail when no sample is configured. Manual browser QA is still required and is listed in `docs/release_candidate_qa_v1.md`.

`docs/deployment_target_decision_v1.md` 记录的是 Stage 2 之前的内部 preview 决策；其 split-origin 和 public `/media/videos` 建议不是生产合同，已被本 README 与 `docs/production_auth_owner_private_media_v1.md` 的生产要求取代。开发模式的内部 preview 打包和 reviewer handoff checklist 见 `docs/internal_preview_packaging_v1.md`。当前部署准备、runtime env 和 smoke checklist 见 `docs/deployment_readiness_v1.md`，完整 RC runbook 见 `docs/release_candidate_qa_v1.md`。

## 主要流程

### 1. Demo Library

访问 `/dashboard`。这里是工作型 demo library，不是营销页。

支持：

- `Create mock demo` 创建合成 demo，用于快速 UI smoke。
- `Upload .dem` 上传真实 demo 并走 parser flow。上传成功后会显示可直接打开的新 demo 链接。
- 只在 loading、upload/create、queued/parsing/analyzing 或 render active 时轮询，避免空闲页面无限刷新。
- 空库、加载、API fetch 失败、仅有 archived demo、search/filter 无结果都有明确状态和动作：create mock demo、upload `.dem`、refresh library、clear filters、show archived。
- 显示 compact ingestion phase、active/stale、attempt count、failure reason 和 retry availability。
- 搜索 demo name、original filename、map。
- 按 status/map 过滤，按 recent/name/map/status 排序。
- inline rename。
- soft archive；归档 demo 默认隐藏，但仍可通过 ID 打开。
- 展示 parse/coaching/review/render/video 摘要状态。

### 2. Recent Steam Matches

Dashboard 的紧凑 Steam 区域提供：

- 只输入 Game Authentication Code 和一个初始 Match Sharing Code；每次提交后立即清空，不写 browser storage。
- `Sync now` 调用官方 match-history cursor API，每次最多枚举 20 场。
- 真实展示 `discovered`、`demo_pending`、`downloading`、`parsing`、`ready`、`unavailable` 状态；默认 disabled provider 的导入请求明确进入 `unavailable`，不会伪造下载或 ready。
- 403/412 进入需要修复授权的停止状态；429/503 记录指数退避和下一次允许时间，不在请求中 sleep。
- Redis 对手动同步执行 `3/owner/minute` 与 `30/global/minute` 的原子调用预算；Valve 429 会触发共享 publisher-key breaker，Redis 不可用时 fail closed。
- Disconnect 删除 server-side credentials、cursor、connection 和 discovered match rows，并停止后续同步。未来已导入的 owner-owned Demo 保留在 Demo Library。
- Valve 接口不提供 Demo URL 或比赛详情；无正式许可 Provider 时自动来源保持 disabled，手动 `.dem` 上传始终可用。

### 3. Mock Upload

`POST /uploads/mock` 创建 `mock_parse` job。worker 会写入 mock replay JSON 和 coaching rows，然后把 demo 标记为 `completed`。这条路径用于快速验证前端 replay、timeline、coaching 和 render UI。

### 4. Real Demo Upload

`POST /uploads/demo` 的唯一公开产品类型是 `.dem`，实际字节上限为 1 GiB；`.zip` 和其它 archive 在写入前拒绝。API 不运行 parser：它先把请求流式写入 private quarantine，计算真实长度和 SHA-256，执行已固化的 extension/MIME/content policy，再把验证通过的 generation promotion 为 immutable accepted source。只有 accepted metadata 已绑定 owner/demo 并随 `Demo` + `real_parse` job 一起提交后才会推入 Redis。语义损坏但无法由 byte-level intake 证明的 `.dem` 仍由异步 parser 安全归类为 `INVALID_DEMO`。

worker 使用 `demoparser2==0.42.0` 做 best-effort 解析：

- map name、tick rate
- rounds、freeze/end ticks、winner side
- player roster
- sampled player positions，常规采样间隔 0.25 秒；约 30,000 个常规采样 tick 的预算之外，保留回合边界、事件 tick 及事件前一 tick，超长比赛会放宽常规采样间隔
- kills/deaths
- best-effort damage events
- best-effort round start/end events
- best-effort bomb pickup/drop/plant/defuse/explode events，以及帧中的实际炸弹状态；缺少可信状态时显示 `unknown`
- best-effort smoke/flash/molotov/he events

这些精度改进只作用于新解析生成的 replay。旧记录需要从已保存的 source 重新解析，或重新上传 `.dem`；刷新页面不会增加旧 replay 的采样点或补齐 Z 坐标。播放只对相邻有效位置进行插值，生命值、存活和炸弹状态在记录的 tick 变化；不会跨回合或在旧的稀疏采样间虚构连续移动。

解析失败会把 demo 标记为 `failed`，写入 compact failure metadata，并且不会继续跑 rules analyzer。单个 damage/bomb/utility/round event family 缺失不会让整个解析失败；这些缺失会作为 partial parse / replay diagnostics 暴露给 QA 界面。

当前 parser failure taxonomy 使用短 `errorCode` 和安全的一句话 `message`：

- `INVALID_DEMO`：无效、不可读或 archive 内没有 `.dem`。
- `UNSUPPORTED_PARSER_FORMAT`：上传格式或 parser support 不可用。
- `MISSING_MATCH_METADATA`：缺少 rounds、playback ticks 或可采样 event ticks。
- `MISSING_FRAMES`：parser 没有返回可用 player position ticks。
- `NORMALIZATION_FAILED`：parser 输出无法整理成 replay contract。
- `STORAGE_READ_FAILED`：上传 source artifact 无法从 storage service 读取。
- `PARSER_UNEXPECTED`：未分类 parser exception。

Parser/demo failure API responses and diagnostics 不包含本地路径、stack trace 或 raw parser dump；开发排障细节只保留在 worker process logs。

在 Dashboard 和 Demo Detail 中，失败状态以 compact 文案呈现：invalid/corrupt demo 显示 `INVALID_DEMO` 和一句原因；unsupported parser/support 显示 `UNSUPPORTED_PARSER_FORMAT`；partial parse 的 missing event family 留在 Replay Contract diagnostics；development/test 中 API/Redis/worker 不可达时可检查 `/diagnostics`；render clip 未连接 GPU worker 时显示 `GPU worker not connected for render_clip`；媒体 URL 缺失、被拒绝或加载失败时保留同步 2D/mock shell 并提示 private media 不可用。

Demo list/detail responses include an `ingestion` snapshot:

```json
{
  "phase": "uploaded",
  "active": true,
  "stale": false,
  "retryable": false,
  "attemptCount": 0,
  "jobType": "real_parse",
  "jobStatus": "queued",
  "hasSourceDemo": true,
  "failure": null
}
```

Failed parses keep short failure metadata with `errorCode`, `message`, `failedAt`, `updatedAt`, `retryable`, and `attemptCount`. If the uploaded source artifact still exists, an owner-scoped retry can requeue parsing without reuploading:

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/parse/retry
# development/test harness only:
curl -X POST -H "X-Dev-User-Id: owner-a" http://localhost:8000/demos/{demo_id}/parse/retry
```

CLI 示例：

```bash
curl -F "file=@sample.dem" http://localhost:8000/uploads/demo
# development/test harness only:
curl -H "X-Dev-User-Id: owner-a" -F "file=@sample.dem" http://localhost:8000/uploads/demo
```

### Optional Sample Demo Smoke

The repository does not commit real `.dem`, demo archives, replay, video, or media artifacts. For local parser smoke checks, place a real `.dem` sample outside git, for example:

```bash
mkdir -p sample-demos
# put sample.dem under sample-demos/
export SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem"
export SAMPLE_DEMO_NAME="Local Sample Demo"
```

`sample-demos/`, `samples/`, `.local/`, local storage directories, `.dem`, demo archives, and common video outputs are ignored by git. Treat real match demos as untrusted and potentially private: only use samples you are allowed to store locally, and do not commit them.

Cloud Preview smoke keeps the mock upload path mandatory and the real sample path optional:

```bash
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" python3 scripts/cloud_preview_smoke.py
SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" python3 scripts/cloud_preview_smoke.py --require-sample
REQUIRE_SAMPLE_DEMO=1 python3 scripts/cloud_preview_smoke.py
```

When `SAMPLE_DEMO_PATH` is absent, the script prints a clear skip message and exits successfully after the mock smoke and compact diagnostics summary. When any smoke step fails, it attempts to print `/diagnostics` summary; if diagnostics is unavailable, it says so without hiding the original failure. When `--require-sample`, `REQUIRE_SAMPLE_DEMO=1`, or `SAMPLE_DEMO_REQUIRED=1` is set, a missing or invalid sample is a failure. When a sample is present, the script uploads it through `POST /uploads/demo`, waits for parser completion, and prints the map, round count, coaching event count, and map calibration/fallback status. Existing parsed rows are useful for UI regression checks, but they do not validate fresh upload/parser ingestion.

More details and an ad hoc `curl` upload command are in `docs/sample_demo_fixture_v1.md`.

For release-candidate sign-off, use `docs/release_candidate_qa_v1.md` as the source checklist for local Docker checks, preview checks, optional/strict sample validation, and manual browser smoke.

### 4. Demo Detail Review

打开 `/demos/{demoId}` 后，完成状态的 demo 会加载：

- `PersonalReviewPanel`：默认昵称为 `xelex`；昵称与 Steam ID 均使用忽略大小写的完整匹配。偏好保存在当前浏览器，可修改；找不到玩家或存在重名时不自动指认其他玩家，使用 `Player to review` 明确选择。该偏好仅选择复盘对象，不改变登录身份或数据 owner。
- `FirstPersonReplay`：有 owner-authorized `/demos/{demo_id}/media/video` 时用 credentialed GET/Range 播放 MP4；没有或无法访问时显示同步的 2D/mock first-person shell。
- `Timeline`：play/pause、seek、0.5x/1x/2x/4x、coaching markers、parser event markers。
- `ReplayViewer`：tactical map、玩家点位、死亡状态、bomb state、附近 parser events。
- `RoundReviewPanel`：回合列表、winner、tick range、first kill、plant、kill/utility/coaching counts、quick jumps。
- `CoachingPanel`：按回合分组，支持 severity/rule/search 过滤，点击事件跳到 tick，并可为事件创建 render clip。
- `ReplayDiagnosticsPanel`：compact contract diagnostics，包括 contract version、parser/coaching/round/player/frame counts、legacy normalization、missing/degraded optional fields、missing event families 和 render fallback state。
- `RenderOperatorPanel`：内部 operator 视角展示最新 `render_clip` job、tick range、event/player/POV、视频输出状态和错误。
- `VideoSetupPanel`：开发/QA 用手动 MP4 上传和 sync calibration。

这些视图共用同一个 `currentTick`、`selectedRound` 和 replay contract；不要引入平行状态来让回合列表、timeline、地图和 coaching 脱节。

个人建议严格按 `event.player_id` 归属，`involvedPlayerIds` 只表示证据涉及的玩家。地图和时间线显示与所选玩家有关的事件，并保留炸弹与回合上下文；回合胜负、全场击杀/道具计数及快速跳转继续提供比赛背景。没有该玩家建议时会明确说明，不能据此认定没有问题。

Detail 顶部的 compact summary strip 汇总 file、map、calibration/fallback、round count、coaching count、parser status/failure category、media status 和最新 render job status，方便 first-run preview 先判断 demo 是否可复盘。

## Rules-Based Coaching

当前 analyzer 在 `backend/app/analysis/`，入口是 `analyze_replay`。它读取 replay JSON 的 rounds、frames、players、kills/deaths、frame-level bomb state 和 compact parser events，只写 compact `coaching_events` rows。

当前规则：

- `untraded_death`
- `isolated_entry`
- `poor_spacing`
- `post_plant_spread_issue`
- `post_plant_spacing_with_bomb_event`
- `retake_desync`
- `weak_utility_before_execute`
- `late_post_plant_utility`

规则输出包含 `ruleId`、`involvedPlayerIds`、`evidenceTicks`、`relatedEventIds`、距离/窗口/utility/bomb/site 等 compact evidence metadata。规则是 best-effort 和 deterministic 的：缺少 parser event family 时跳过对应规则，不让整个分析失败。

建议以 `assessment=review_candidate` 表示待复核的片段，并附带 `action`、`limitation`、`targetPlayerId` 和 `evidenceSource`；展开卡片可读到具体尝试方向与证据局限。采样距离无法证明视线、可行路径、语音沟通或战术意图，候选不等于已经证明的失误。默认每位玩家最多 48 条、每场最多 480 条，按玩家和回合分配候选，避免前几轮耗尽全场名额。真实样本、运行和浏览器验收记录见 [个人复盘验收](docs/personal_review_acceptance_2026-09-07.md)。

补枪判断使用同一击杀者与完整的 5 秒有效回合窗口；位置证据采用事件之前不超过 1 秒的采样。距离单位是 radar 百分点，Nuke 缺少高度或跨层时跳过相关几何判断；道具规则只统计已知 T 阵营的事件。

## Replay Contract

Replay blob 的核心字段：

```json
{
  "demoId": "demo-id",
  "mapName": "de_dust2",
  "mapMetadata": {},
  "tickRate": 64,
  "video": {},
  "rounds": [],
  "players": [],
  "frames": [],
  "kills": [],
  "deaths": [],
  "events": [],
  "contractVersion": "replay_contract_v1",
  "diagnostics": {},
  "generatedAt": "..."
}
```

`events` 是 backward-compatible compact parser event list。旧 replay blob 没有 `events` 时按 `events: []` 处理。

Replay blob 写入前会做轻量 validation/sanitization：tick rate 和 tick ranges 必须可用，rounds/frames 会按 tick 排序，player id/name 会稳定化，非有限坐标会被忽略，malformed parser markers/events 会 best-effort 丢弃或降级。Unknown maps 使用 `mapMetadata.confidence = "fallback"` 和 dynamic bounds grid，不复用 Dust II radar 或坐标 transform。

`diagnostics` 是为 QA 和降级 UI 准备的 compact contract snapshot，不是 raw parser log：

```json
{
  "contractVersion": "replay_contract_v1",
  "normalizedLegacy": false,
  "parserEventCount": 12,
  "roundCount": 24,
  "playerCount": 10,
  "frameCount": 720,
  "missingFields": [],
  "degradedFields": [],
  "eventFamilyCounts": {
    "combat": 4,
    "damage": 2,
    "objective": 3,
    "utility": 3
  },
  "missingEventFamilies": []
}
```

Malformed optional fields are ignored best-effort and reported through `degradedFields`. Missing optional fields are reported through `missingFields`. Neither should crash replay loading.

支持的 parser event types：

- `kill`
- `death`
- `damage`
- `bomb_pickup`
- `bomb_dropped`
- `bomb_planted`
- `bomb_defused`
- `bomb_exploded`
- `smoke`
- `flash`
- `molotov`
- `he`
- `round_start`
- `round_end`

`video` metadata 支持：

- `status`: `pending`、`queued`、`rendering`、`ready`、`failed`
- `source`: `mock`、`manual_upload`、`rendered`
- `url`
- `durationSeconds`
- `tickStart`
- `tickEnd`
- `tickRate`
- `timeOriginSeconds`
- `errorMessage`

tick/video 映射：

```text
tickToVideoTime(tick) = timeOriginSeconds + (tick - tickStart) / tickRate
videoTimeToTick(time) = tickStart + (time - timeOriginSeconds) * tickRate
```

## Tactical Maps

地图配置必须通过两个集中入口同步维护：

- backend: `backend/app/parser/map_config.py`
- frontend: `frontend/lib/map-config.ts`

当前地图支持：

| Map | Radar image | Coordinate status |
| --- | --- | --- |
| `de_dust2` | `frontend/public/maps/de_dust2_radar.png` | calibrated |
| `de_mirage` | `frontend/public/maps/de_mirage_radar.png` | approximate |
| `de_inferno` | `frontend/public/maps/de_inferno_radar.png` | approximate |
| `de_ancient` | `frontend/public/maps/de_ancient_radar.png` | approximate |
| `de_nuke` | `frontend/public/maps/de_nuke_radar.png` + `de_nuke_lower_radar.png` | calibrated overview + upper/lower |
| `de_anubis` | `frontend/public/maps/de_anubis_radar.png` | approximate |

Unknown maps 使用 fallback grid，不复用 Dust II 图片或坐标变换。

Nuke 保留世界 Z 坐标，以集中配置的高度边界区分上下层，可手动切换或跟随所选玩家。没有高度数据的旧记录不会把玩家强放在某一层，界面会说明并保留可选择的名单。

新增或更新地图时，同步修改 `backend/app/parser/map_config.py` 和 `frontend/lib/map-config.ts`，把 radar PNG 放在 `frontend/public/maps/`，更新 `frontend/public/maps/ATTRIBUTION.md`，并运行 `PYTHONPATH=backend python3 -m unittest backend.tests.test_map_config` 与 `cd frontend && node lib/map-config.test.mjs`。没有可信 transform 或 radar asset 时，保留 explicit fallback/approximate metadata，不要复用 Dust II transform。

## Render Clip Boundary

`render_clip` 是未来外部 GPU worker 的短 clip 合约，不是当前 API 容器内的真实渲染。

前端创建 job：

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/render/clip \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "event-id",
    "playerId": "player-id",
    "tickStart": 1000,
    "tickEnd": 3560,
    "tickRate": 64,
    "roundNumber": 3,
    "renderPreset": "event_clip_v1"
  }'
```

API 会验证：

- demo 已完成解析
- replay blob 存在
- `tickEnd > tickStart`
- tick rate 与已解析 replay 一致，tick 区间落在 replay 范围内
- 所选 POV 与已解析玩家名单一致
- clip 时长不超过 `MAX_RENDER_CLIP_SECONDS`，默认 60 秒

默认 `RENDER_WORKER_MODE=fallback` 下，本地解析 worker 识别 `render_clip` 后会把 job 从 `queued` 推到 `rendering`，然后标记为 `failed`：

```text
GPU worker not connected for render_clip. A separate Windows/Linux GPU worker or manual operator must process this job.
```

启用 `RENDER_WORKER_MODE=external` 后任务会保持 queued，等待独立录制进程原子领取。API 容器不能负责真实 CS2 渲染。

Demo Detail 的玩家片段列表保留每个任务的回合、tick 区间和状态，完成后可直接播放。相同源文件快照、玩家 POV、tick 区间、tick rate 和 render preset 的请求复用正在进行的任务或仍可读取的已完成视频；不同 coaching event ID 不会重复生成相同 footage。失效文件和失败任务允许重新生成。后端在 PostgreSQL 中锁定同一个 demo 后检查和创建任务，避免多次点击或多个请求同时创建重复任务。

`GET /demos/{demo_id}/render/jobs` 为可播放任务提供可选 `video` metadata，使用 job 专属私有地址。每段视频保留自己的时间校准，生成新片段或进行开发用手动上传后仍可播放旧片段。前端选中的片段与后台默认视频分别维护，轮询不会改变正在复盘的片段。旧任务若没有保存校准且已不再是当前视频，会显示为不可播放；不会猜测其时间偏移。

## Render Worker V1 API

Render worker API 使用独立的 `X-Render-Worker-Token` service credential，不接受 browser session 代替。本地默认 token 是 `dev-render-worker-token`；production 必须配置非默认 `RENDER_WORKER_TOKEN`，否则 startup validation fails closed。Stage 3 保留这条 service-to-service auth，并在 multipart parser 消费媒体 body 前拒绝错误 token。

获取下一个 queued manifest 并默认 claim 为 `rendering`：

```bash
curl http://localhost:8000/render-worker/jobs/next \
  -H "X-Render-Worker-Token: dev-render-worker-token"
```

获取指定 job manifest 并默认 claim 为 `rendering`：

```bash
curl http://localhost:8000/render-worker/jobs/{job_id}/manifest \
  -H "X-Render-Worker-Token: dev-render-worker-token"
```

只检查 manifest 而不 claim，用 `claim=false`：

```bash
curl "http://localhost:8000/render-worker/jobs/{job_id}/manifest?claim=false" \
  -H "X-Render-Worker-Token: dev-render-worker-token"
```

仅在 job 已 claim 为 `rendering` 后上传 dev/worker mp4。响应中的 `storageKey` 是该 job 的 immutable `outputArtifact` snapshot reference，必须原样用于成功回调：

```bash
curl -X POST http://localhost:8000/render-worker/jobs/{job_id}/media \
  -H "X-Render-Worker-Token: dev-render-worker-token" \
  -F "file=@clip.mp4"
```

提交成功回调：

```bash
curl -X POST http://localhost:8000/render-worker/jobs/{job_id}/result \
  -H "X-Render-Worker-Token: dev-render-worker-token" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "videoUrl": "/demos/demo-id/media/video",
    "storageKey": "artifact://v1/accepted/video/...",
    "tickStart": 1000,
    "tickEnd": 3560,
    "tickRate": 64,
    "timeOriginSeconds": 0,
    "durationSeconds": 40,
    "errorMessage": null
  }'
```

API 只接受与同一个 `rendering` job、owner、demo、immutable generation 和请求 tick 区间完全匹配的 accepted output。另一个 job 的 artifact、过期 generation、不同 tick 范围或没有先绑定 media 的 callback 都会失败关闭。Development local adapter 仍可解析旧 `/media/videos/...` callback 作为 legacy compatibility，但 runner 的正常路径必须使用 media upload 返回的 accepted `storageKey`。User payload 只投影默认或 job 专属的私有媒体路由，不返回 `storageKey`。

失败回调把 replay failure state 与 terminal job 放在同一个 DB transaction 中，并在提交后删除该 job 的 bound output candidate；如果当前 replay video 是 `manual_upload`，失败不会清掉已有手动视频 metadata。成功回调同样原子提交新 replay reference 与 terminal job，随后清理旧 replay generation。已完成或已失败的 terminal render job 会拒绝后续 callback，避免 late callback 改写最终状态。

## Standalone Render Worker

`render-worker/runner.py` 默认使用 fake adapter。操作者可以在指定 Windows GPU 电脑设置 `RENDER_ADAPTER=csdm`，连接已安装并配置的 CSDM、HLAE、FFmpeg/FFprobe 与 PostgreSQL 客户端。

API 和解析 worker 同时设置 `RENDER_WORKER_MODE=external` 后，短片任务保留在数据库等待独立 worker 领取。默认 `fallback` 保留 GPU 未连接提示。源文件通过 token 保护的 `GET /render-worker/jobs/{job_id}/source` 下载，校验 manifest 的长度与 SHA256，不读取浏览器用户的本地文件。单机运行使用固定工作目录和进程锁；当前没有分布式租约恢复服务。

配置：

```bash
export API_BASE_URL=http://localhost:8000
export RENDER_WORKER_TOKEN=dev-render-worker-token
export WORK_DIR=.render-worker-work
export POLL_INTERVAL_SECONDS=5
export DEV_FAKE_VIDEO_PATH=/absolute/path/to/dev-placeholder.mp4
```

常用命令：

```bash
python3 render-worker/runner.py dry-run {job_id}
python3 render-worker/runner.py dry-run
python3 render-worker/runner.py process-job {job_id}
python3 render-worker/runner.py poll-once
python3 render-worker/runner.py prepare-job --job-id {job_id} --adapter cs2-manual
python3 render-worker/runner.py complete-prepared-job --job-id {job_id}
python3 render-worker/runner.py complete-prepared-job --job-id {job_id} --video-path /absolute/path/to/clip.mp4
```

`FakeVideoAdapter` 会把本地 dev mp4 上传到 API 并提交 completed callback。`CS2ManualAdapter` 会生成 manifest、instructions、expected output 和 status 文件，供受控渲染机器上的人工 operator 使用；它不会启动 Steam/CS2/OBS/ffmpeg。

更多细节见 `render-worker/README.md`。

## Manual MP4 Binding

手动 MP4 是开发/QA 桥，不是最终用户路径。

Demo 完成解析后，可在 detail page 的 `Video Setup / Sync Calibration`：

1. 上传 `.mp4`。
2. API 通过 artifact store 的 quarantine/promotion 写入 private accepted video，只把内部 logical reference 写回 replay JSON；user-facing payload 只投影 private route，不返回 storage key/path。
3. 保存 `timeOriginSeconds`、`tickStart`、`tickEnd`、`tickRate`。
4. `FirstPersonReplay` 通过 authenticated `/demos/{demo_id}/media/video` GET/Range 使用 calibration 在视频时间和 demo tick 之间同步。

CLI：

```bash
curl -F "file=@clip.mp4" http://localhost:8000/demos/{demo_id}/video/upload
```

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/video/calibration \
  -H "Content-Type: application/json" \
  -d '{"timeOriginSeconds":12.5,"tickStart":12345,"tickEnd":54321}'
```

## API Surface

Core:

- `GET /health`
- `GET /diagnostics` (development/test only; production returns `404`)
- `GET /auth/steam/login`
- `GET /auth/steam/callback`
- `GET /auth/me`
- `POST /auth/logout`
- `GET /auth/login`, `GET /auth/oidc/callback`, `GET /auth/session` (`AUTH_PROVIDER=oidc` / legacy frontend compatibility)
- `GET /steam/connection`
- `POST /steam/connection/credentials`
- `DELETE /steam/connection`
- `POST /steam/sync`
- `GET /steam/matches`
- `POST /steam/matches/{id}/import`
- `GET /demos`
- `PATCH /demos/{demo_id}`
- `POST /demos/{demo_id}/archive`
- `GET /demos/{demo_id}/status`
- `GET /demos/{demo_id}/diagnostics`
- `POST /demos/{demo_id}/parse/retry`
- `POST /uploads/mock`
- `POST /uploads/demo`

Replay and coaching:

- `GET /demos/{demo_id}/replay`
- `GET /demos/{demo_id}/coaching`

Video and render:

- `GET /demos/{demo_id}/video`
- `GET|HEAD /demos/{demo_id}/media/video` (session + owner checked; supports byte ranges)
- `POST /demos/{demo_id}/video/upload`
- `POST /demos/{demo_id}/video/calibration`
- `POST /demos/{demo_id}/render/mock`
- `POST /demos/{demo_id}/render/clip`
- `GET /demos/{demo_id}/render/jobs`
- `GET|HEAD /demos/{demo_id}/render/jobs/{job_id}/media/video`（支持 Range）

Render worker:

- `GET /render-worker/jobs/next`
- `GET /render-worker/jobs/{job_id}/manifest`
- `POST /render-worker/jobs/{job_id}/media`
- `POST /render-worker/jobs/{job_id}/result`

## Configuration

Steam/account 合同见 `docs/steam_auth_accounts_v1.md`；比赛授权、加密、同步状态与删除策略见 `docs/steam_match_sync_v1.md`；Demo Provider、下载和 intake 边界见 `docs/steam_demo_import_v1.md`；共享 owner/private media 合同见 `docs/production_auth_owner_private_media_v1.md`；runtime deployment 说明见 `docs/deployment_readiness_v1.md`，Cloud Preview runbook 见 `docs/cloud_preview_deploy_v1.md`。关键环境变量：

| Name | Default | Used by |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | frontend browser API/media URL |
| `NEXT_PUBLIC_AUTH_PROVIDER` | `steam` | public frontend login label/path; must match server `AUTH_PROVIDER` |
| `AUTH_MODE` | unset (required) | backend identity mode: `development`, `test`, or `production` |
| `AUTH_PROVIDER` | unset (required in production) | production browser identity provider: `steam` or compatibility `oidc`; local Compose sets `steam` |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | server-trusted frontend redirect origin; must equal the HTTPS API origin in production |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | backend public origin; same exact origin as frontend in production |
| `AUTH_COOKIE_SECURE` | `false` | must be enabled in production |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | opaque session cookie name |
| `STEAM_AUTH_STATE_COOKIE_NAME` | `__Host-cs2_steam_state` | short-lived Steam state cookie name |
| `STEAM_OPENID_NONCE_TTL_SECONDS` | `600` | Steam assertion freshness/replay reservation window |
| `STEAM_WEB_API_KEY` | unset | server-only GetPlayerSummaries + match-history publisher key; required in production, never frontend/worker-visible |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | unset | URL-safe base64 of 32 random bytes; required in production and never logged/returned |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION` | `dev-v1` | bounded active AES-GCM key version stored with ciphertext; production should set its own version |
| `STEAM_SYNC_MAX_MATCHES` | `20` | hard maximum new sharing codes per Sync-now request |
| `STEAM_SYNC_TIMEOUT_SECONDS` | `5` | bounded Valve request timeout |
| `STEAM_SYNC_RETRY_BASE_SECONDS` | `30` | first persisted transient backoff |
| `STEAM_SYNC_RETRY_MAX_SECONDS` | `3600` | maximum persisted transient backoff |
| `STEAM_SCHEDULED_SYNC_ENABLED` | `false` | independent fail-closed gate; V1 has no scheduler and rejects true |
| `STEAM_DEMO_PROVIDER` | `disabled` | API-only Demo source selector; this build rejects every value except `disabled` |
| `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED` | `false` | unsupported experiment guard; `true` always fails startup |
| `STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS` | unset | reserved exact lowercase DNS allowlist for a separately reviewed licensed provider |
| `STEAM_DEMO_DOWNLOAD_MAX_BYTES` | `536870912` | bounded raw `.dem` transfer; hard maximum 1 GiB |
| `STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS` | `3` | independently revalidated redirect-hop limit |
| `STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS` | `5`, `10`, `60` | bounded future licensed-provider transfer timeouts |
| `STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY`, `STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY` | `4`, `1` | Redis-coordinated API download budgets |
| `STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS` | `120` | expiring concurrency lease; must cover total timeout |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | bounded Redis/browser session lifetime |
| `AUTH_LOGIN_TTL_SECONDS` | `300` | bounded one-time login attempt lifetime |
| `AUTH_CLOCK_SKEW_SECONDS` | `30` | bounded identity timestamp leeway |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID` | unset | required only when `AUTH_PROVIDER=oidc` |
| `OIDC_AUTHORIZATION_ENDPOINT`, `OIDC_TOKEN_ENDPOINT`, `OIDC_JWKS_URL`, `OIDC_REDIRECT_URI` | unset | legacy-compatible OIDC endpoints, required only when selected |
| `OIDC_CLIENT_SECRET`, `OIDC_ALLOWED_ALGORITHMS`, `AUTH_STATE_COOKIE_NAME` | unset / existing defaults | server-only OIDC compatibility configuration |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | backend API; production requires only the exact `FRONTEND_PUBLIC_URL` origin |
| `DATABASE_URL` | `postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach` | API, worker |
| `REDIS_URL` | `redis://localhost:6379/0` | API, worker |
| `REDIS_QUEUE_NAME` | `cs2-demo-jobs` | API, worker |
| `ARTIFACT_STORAGE_BACKEND` | `local` | API, worker; production requires `s3` |
| `OBJECT_STORAGE_BUCKET` | unset | private S3-compatible bucket; required in production |
| `OBJECT_STORAGE_PREFIX` | `cs2-artifacts-v1` | bounded server-side object namespace |
| `OBJECT_STORAGE_REGION` | `us-east-1` | S3-compatible region setting |
| `OBJECT_STORAGE_ENDPOINT_URL` | unset | optional private S3-compatible HTTPS endpoint |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | unset | optional server-side credential or runtime credential chain |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | unset | optional server-side credential; never frontend-visible |
| `ARTIFACT_QUARANTINE_TTL_SECONDS` | `3600` | abandoned quarantine cleanup cutoff |
| `MAX_DEMO_UPLOAD_BYTES` | `1073741824` | actual streamed source-byte limit |
| `MAX_VIDEO_UPLOAD_BYTES` | `2147483648` | actual streamed dev/QA/worker video limit |
| `MAX_REPLAY_ARTIFACT_BYTES` | `134217728` | replay JSON artifact limit |
| `UPLOAD_CHUNK_BYTES` | `1048576` | bounded upload/read chunk size |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker |
| `DEV_USER_ID` | `dev-user` | development/test owner harness only |
| `MAX_RENDER_CLIP_SECONDS` | `60` | backend API |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker |
| `API_BASE_URL` | `http://localhost:8000` | render-worker runner |
| `WORK_DIR` | `.render-worker-work` | render-worker runner |
| `POLL_INTERVAL_SECONDS` | `5` | render-worker runner |
| `DEV_FAKE_VIDEO_PATH` | unset | render-worker fake adapter |
| `CS2_INSTALL_DIR` | unset | render-worker manual adapter |
| `STEAM_USER_DATA_DIR` | unset | render-worker manual adapter |
| `CS2_MANUAL_OUTPUT_FILENAME` | `{job_id}.mp4` | render-worker manual adapter |
| `SAMPLE_DEMO_PATH` | unset | optional smoke sample upload |
| `SAMPLE_DEMO_NAME` | unset | optional display name for smoke sample |
| `REQUIRE_SAMPLE_DEMO` | `0` | make smoke fail when no sample is configured |

Docker Compose uses service names inside containers (`postgres`, `redis`) and host-facing URLs for the browser (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`). Local Compose must explicitly use `AUTH_MODE=development` and may use the local adapter plus the clearly marked non-production Steam encryption key. Production requires an explicit supported identity provider, the complete HTTPS/`__Host-` cookie/single-origin/CORS contract, a real server-only Steam Web API key, a random non-development Steam credential encryption key, `STEAM_DEMO_PROVIDER=disabled` with the experimental CDN switch off, a non-default render-worker credential, and a private S3-compatible artifact configuration or startup fails closed. Steam realm/callback are derived from `BACKEND_PUBLIC_URL`; OIDC fields are required only when `AUTH_PROVIDER=oidc`. Demo source/download configuration belongs only to the API; the parser/render queue worker validates its narrower auth/storage contract and never receives Steam/OIDC browser secrets, match-history credentials, source-provider configuration, or provider secrets. Local artifacts default under `ARTIFACT_STORAGE_ROOT=/data`; production credentials must never enter `NEXT_PUBLIC_*`, source, logs, checked-in env files, or browser responses.

## Deploy Smoke Checklist

Minimal local smoke for a clean environment. For internal preview handoff, use `docs/internal_preview_packaging_v1.md`. For full release-candidate validation, prefer `./scripts/rc_check.sh` plus the manual browser checklist in `docs/release_candidate_qa_v1.md`.

1. `docker compose up --build`
2. `curl http://localhost:8000/health`
3. In explicit development mode, `curl http://localhost:8000/diagnostics`; in production, confirm it returns `404`
4. Open `http://localhost:3000/dashboard`; production should require Steam sign-in (or explicitly selected compatibility OIDC), while development uses the explicit local harness
5. With a formal Steam account, configure two match-history codes, run Sync now, and confirm only real discovered/status/timestamp fields appear; do not use real credentials in shared evidence
6. Disconnect Steam match history and confirm authorization plus discovered rows disappear while existing Demo Library entries remain
7. Create a mock upload and wait for completion
8. Upload a real `.dem` if a sample is available
9. Open a demo detail page
10. Use round review quick jumps and confirm replay, tactical map, timeline, parser markers, and coaching cards stay synchronized
11. If video exists, confirm `/demos/{demo_id}/media/video` supports authenticated GET/HEAD/Range and the 2D fallback remains available when media is denied
12. Click `Generate Clip`
13. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`
14. Run the owner A/B/anonymous/expired/revoked matrix in `docs/production_auth_owner_private_media_v1.md` plus Stage 2 owner isolation tests
15. For development-mode Cloud Preview validation, run `API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py` or point those variables at the preview URLs
16. For stricter parser validation, set `SAMPLE_DEMO_PATH` and rerun the smoke; add `--require-sample` when preview validation must fail without a fresh real upload

## Current Limitations

- Steam-first production explicitly selects Steam OpenID 2.0, but real HTTPS callback/provider availability, publisher-key eligibility, Game Authentication Code behavior, rate limiting, and encrypted-key deployment still require a live deployment smoke. Match discovery is live-provider dependent; automatic Demo download cannot receive a live smoke until a formally licensed provider contract exists.
- Redis-backed opaque sessions require Redis availability. The Steam import path can manually requeue a queued parser job after its 30-second dispatch marker becomes stale, and the worker CAS prevents duplicate execution; automatic durable queue recovery, stale-processing recovery, and terminal reconciliation remain future work.
- Development/test 可使用 private local adapter；production 必须提供 private S3-compatible storage，但具体 bucket/IAM/credential provisioning 不由仓库创建。
- Parser frame 是采样数据，不是完整 tick density。
- `demoparser2` 对不同 demo 的 event family 和字段可用性不稳定；normalizer 必须继续容错。
- Bomb/utility events 是 best-effort；缺失时 UI count、quick jump 或 event-backed rules 可能为空。
- Tactical map 的 Dust2、Nuke 已标记 calibrated；Mirage、Ancient、Inferno、Anubis 仍为 approximate，需要更多真实样本校准。
- 没有经济、装备快照、line-of-sight、utility trajectory 和高级战术上下文。
- `render_clip` 已通过本机显式启用的独立 Windows worker 生成 xelex 真实短片，并验证保存与重播；其它地图、Demo/游戏版本兼容及生产 GPU 集群仍待验收。默认未连接 worker 时保留明确的失败提示。
- Manual MP4 必须人工校准，且只能代表它实际覆盖的 tick range。
- Ingestion snapshots、safe diagnostics、replay diagnostics 和 regression fixtures 是 compact QA/debugging aids，不是生产 telemetry、日志平台或 parser trace storage。

## Next Useful Work

下一步优先验证 xelex 建议的实际帮助，减少重复站位提醒并改善排序，然后扩充地图校准、可复现安装配置与解析/录制任务恢复。完整优先级和完成标准见 [项目进展与下一步](docs/project_status_2026-09-13.md)。

Steam Demo 导入适配器保持 fail closed：仅 Valve 明确授权的 partner endpoint 或正式许可 Provider 才能在后续独立审阅中注册；任何社区 share-code/CDN 路径继续默认关闭且标为 unsupported/experimental，下载内容必须进入现有 artifact intake。手动 `.dem` 上传始终保留为可靠兜底。之后再按独立阶段推进可靠任务投递、parser 隔离、migration/CI/CD/observability/backup 和真实 demo corpus；不跨阶段捆绑。

保持产品方向：核心复盘体验必须在 `.dem` 上传解析后立即可用；真实 first-person footage 是异步 clip 增强，而不是使用网站的前置条件。
