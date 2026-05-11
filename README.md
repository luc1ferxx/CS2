# CS2 Demo AI Coach Mock MVP

这是一个网站型 CS2 demo 复盘与规则教练原型。当前项目重点已经从单纯 mock 流程推进到“真实 `.dem` 解析 spike + Demo Library + 回放复盘界面 + deterministic coaching + render clip 合约”。

当前版本仍然是本地 mock MVP：没有生产登录系统、没有 OpenAI 调用、没有对象存储、没有真实 CS2 自动渲染。它只有一个 dev-only local owner boundary，用来把 demo/upload/render 管理动作先按 owner 隔离，方便后续替换成生产身份系统。它的核心价值是验证产品边界、replay 数据契约、规则分析结果、前端复盘体验，以及后续外部 GPU render worker 的 API 交接方式。

## 当前状态

已经具备的能力：

- Docker Compose 本地栈：`frontend`、`api`、`worker`、`postgres`、`redis`。
- Dev-only owner boundary：默认 `DEV_USER_ID=dev-user`，测试或本地调试可用 `X-Dev-User-Id` 模拟不同 owner；这不是生产认证。
- `/dashboard` Demo Library：搜索、状态/地图筛选、排序、上传轮询、重命名、软归档、渲染状态摘要。
- Upload/parser observability：demo list/detail responses 包含 compact ingestion snapshot，失败解析有短错误、attempts、stale/active/retryable 状态，并支持 owner-scoped retry。
- Mock demo flow：快速生成合成 replay、coaching events 和 mock first-person shell。
- Real demo parser spike：上传 `.dem` 或包含 `.dem` 的 `.zip`，后端队列异步解析。
- Replay contract：回合、玩家、采样帧、击杀/死亡、compact parser events、地图 metadata、视频 metadata、contract diagnostics。
- Demo detail review：first-person shell/video、tactical map、timeline、round selector、round review、coaching panel、Replay Contract diagnostics 同步到同一个 tick/round state。
- Rules-based coaching：固定规则生成事件，不调用 LLM，不生成不透明 AI 文案。
- Tactical map assets：Dust II、Mirage、Inferno、Ancient、Nuke、Anubis radar 支持；Dust II 使用 CS2 overview transform，其余是 approximate bounds。
- Manual MP4 binding：仅用于开发和 QA，支持上传 `.mp4` 并保存 tick/video calibration。
- `render_clip` boundary：用户可围绕 coaching event 或当前 tick 创建短 POV clip job。
- Render Worker V1 contract：token-gated manifest claim、media upload、terminal-safe result callback。
- `render-worker/` skeleton：fake video adapter 和 manual operator adapter，用同一套回调链证明 future GPU worker 合约。
- Parser quality regression fixtures：backend/frontend compact fixtures 覆盖 legacy replay、malformed optional fields、missing event families、coaching evidence 和 degraded detail states。

明确没有做的事情：

- 不实现真实登录、OAuth、JWT、密码、账号管理 UI，且不集成第三方 auth provider。
- 不在 API 或 worker 容器里启动 CS2、Steam、OBS、ffmpeg。
- 不控制用户电脑、不读取用户上传后的本地文件、不录屏。
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
  -> otherwise the mock first-person shell remains available
  -> user may request a render_clip job for a short selected range
```

浏览器不能直接播放 CS2 `.dem`。`.dem` 是游戏状态/事件记录，不是视频流。真实第一人称视频必须作为异步增强，由我们控制的 Windows/Linux GPU worker 渲染短 clip，再把 mp4/HLS metadata 写回 replay contract。

## 架构

```text
frontend (Next.js)
  -> FastAPI API
    -> PostgreSQL metadata: owner-scoped demos, demo_jobs, coaching_events
    -> Redis queue: parse/render job dispatch
    -> artifact storage service: uploads, replay blobs, summaries, videos
  -> worker process
    -> mock_parse / real_parse / mock_render / render_clip status handling
  -> render-worker skeleton
    -> external process that calls token-gated render-worker API
```

PostgreSQL 只存可索引的元数据、storage key、compact ingestion/render metadata 和 coaching event rows。Replay frames、parser event contract、video metadata 和 compact replay diagnostics 都在 replay JSON blob 内；上传 demo、replay artifact 和视频文件放在 Docker volumes 中，后续可以替换为 S3/R2 或其它对象存储。

Artifact storage 通过 `backend/app/services/storage.py` 统一出入口。默认是 local filesystem implementation，root 为 `ARTIFACT_STORAGE_ROOT=/data`，分类 key 形如：

- `local://uploads/{demo_id}/{safe_filename}`
- `local://replays/{demo_id}.json`
- `local://summaries/{demo_id}/{safe_filename}`
- `local://videos/{demo_id}/{uuid_safe_filename}`

不要把 `.dem`、replay JSON、大视频或 raw parser dump 存入 PostgreSQL。未来替换 S3/R2 时，应保留这些应用层 key 语义，把 local implementation 换成 object storage adapter，而不是把 cloud credentials 或 bucket-specific code 散落到 API routes、worker 或 parser 里。

## Dev-Only Owner Boundary

API 里的 demo、upload、manual video、coaching/replay read、library mutation 和 user-facing render job actions 都按当前 local owner 做隔离。默认 owner 是 `dev-user`，可用 `DEV_USER_ID` 覆盖。为了让本地测试能模拟多用户边界，请求也可以带：

```bash
X-Dev-User-Id: owner-a
```

这是开发期边界，不是生产 authentication/authorization。它没有 session、OAuth、JWT、密码登录或账号管理 UI。生产化时应将这个 helper 替换为 Clerk、Auth0、Supabase Auth 或同类 identity provider 的 authenticated user id，并继续使用 `owner_id` 作为 demo ownership 字段。

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
  runner.py               external worker skeleton CLI
  adapters/               fake video and manual CS2 operator adapters

docs/
  *_goal.md               prior phase goals and design notes
```

## 本地运行

完整本地栈：

```bash
docker compose up --build
```

打开：

- Frontend: http://localhost:3000
- API: http://localhost:8000
- Health: http://localhost:8000/health

API 健康检查：

```bash
curl http://localhost:8000/health
```

`/health` 会返回 API、PostgreSQL、Redis、worker dependency 配置和本地 storage path 的 compact readiness payload；`status=degraded` 表示至少一个依赖不可用或关键 worker 配置缺失。

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

Frontend helper regression tests live next to the helpers and are run directly with Node when those surfaces change:

```bash
cd frontend
node lib/demo-library.test.mjs
node lib/replay-diagnostics.test.mjs
node lib/replay-events.test.mjs
node lib/round-review.test.mjs
node lib/coaching-review.test.mjs
node lib/replay-quality-fixtures.test.mjs
node lib/map-config.test.mjs
```

部署准备、runtime env 和 smoke checklist 见 `docs/deployment_readiness_v1.md`。

## 主要流程

### 1. Demo Library

访问 `/dashboard`。这里是工作型 demo library，不是营销页。

支持：

- `Mock Upload` 创建合成 demo。
- `Real Demo Upload` 上传 `.dem` 或 `.zip`。
- 自动轮询 queued/parsing/analyzing 状态。
- 显示 compact ingestion phase、active/stale、attempt count、failure reason 和 retry availability。
- 搜索 demo name、original filename、map。
- 按 status/map 过滤，按 recent/name/map/status 排序。
- inline rename。
- soft archive；归档 demo 默认隐藏，但仍可通过 ID 打开。
- 展示 parse/coaching/review/render/video 摘要状态。

### 2. Mock Upload

`POST /uploads/mock` 创建 `mock_parse` job。worker 会写入 mock replay JSON 和 coaching rows，然后把 demo 标记为 `completed`。这条路径用于快速验证前端 replay、timeline、coaching 和 render UI。

### 3. Real Demo Upload

`POST /uploads/demo` 接收 `.dem` 或 `.zip`，大小上限为 1 GiB。API 通过 storage service 保存 source artifact，创建 `real_parse` job 并推入 Redis。

worker 使用 `demoparser2==0.41.0` 做 best-effort 解析：

- map name、tick rate
- rounds、freeze/end ticks、winner side
- player roster
- sampled player positions，最多约 720 个采样帧加死亡 tick
- kills/deaths
- best-effort damage events
- best-effort round start/end events
- best-effort bomb plant/defuse/explode events
- best-effort smoke/flash/molotov/he events

解析失败会把 demo 标记为 `failed`，写入 compact failure metadata，并且不会继续跑 rules analyzer。单个 damage/bomb/utility/round event family 缺失不会让整个解析失败。

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
curl -X POST -H "X-Dev-User-Id: owner-a" http://localhost:8000/demos/{demo_id}/parse/retry
```

CLI 示例：

```bash
curl -F "file=@sample.dem" http://localhost:8000/uploads/demo
curl -H "X-Dev-User-Id: owner-a" -F "file=@sample.dem" http://localhost:8000/uploads/demo
```

### 4. Demo Detail Review

打开 `/demos/{demoId}` 后，完成状态的 demo 会加载：

- `FirstPersonReplay`：有视频 URL 时播放 MP4；没有 URL 时显示同步的 mock first-person shell。
- `Timeline`：play/pause、seek、0.5x/1x/2x/4x、coaching markers、parser event markers。
- `ReplayViewer`：tactical map、玩家点位、死亡状态、bomb state、附近 parser events。
- `RoundReviewPanel`：回合列表、winner、tick range、first kill、plant、kill/utility/coaching counts、quick jumps。
- `CoachingPanel`：按回合分组，支持 severity/rule/search 过滤，点击事件跳到 tick，并可为事件创建 render clip。
- `ReplayDiagnosticsPanel`：compact contract diagnostics，包括 contract version、parser/coaching/round/player/frame counts、legacy normalization、missing/degraded optional fields、missing event families 和 render fallback state。
- `RenderOperatorPanel`：内部 operator 视角展示最新 `render_clip` job、tick range、event/player/POV、视频输出状态和错误。
- `VideoSetupPanel`：开发/QA 用手动 MP4 上传和 sync calibration。

这些视图共用同一个 `currentTick`、`selectedRound` 和 replay contract；不要引入平行状态来让回合列表、timeline、地图和 coaching 脱节。

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
| `de_nuke` | `frontend/public/maps/de_nuke_radar.png` | approximate |
| `de_anubis` | `frontend/public/maps/de_anubis_radar.png` | approximate |

Unknown maps 使用 fallback grid，不复用 Dust II 图片或坐标变换。

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
- `tickRate > 0`
- clip 时长不超过 `MAX_RENDER_CLIP_SECONDS`，默认 60 秒

本地 worker 识别 `render_clip` 后会把 job 从 `queued` 推到 `rendering`，然后标记为 `failed`：

```text
Render clip worker is not connected yet. A Windows/Linux GPU worker must process this job.
```

这是有意设计的边界标记。API 容器不能负责真实 CS2 渲染。

## Render Worker V1 API

Render worker API 使用 `X-Render-Worker-Token`。本地默认 token 是 `dev-render-worker-token`，可用 `RENDER_WORKER_TOKEN` 覆盖。它只是本地开发门禁，不是生产认证方案。

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

上传 dev mp4：

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
    "videoUrl": "/media/videos/demo-id/clip.mp4",
    "tickStart": 1000,
    "tickEnd": 3560,
    "tickRate": 64,
    "timeOriginSeconds": 0,
    "durationSeconds": 40,
    "errorMessage": null
  }'
```

失败回调只更新 job error；如果当前 replay video 是 `manual_upload`，失败不会清掉已有手动视频 metadata。已完成或已失败的 terminal render job 会拒绝后续 callback，避免 late callback 改写最终状态。

## Render Worker Skeleton

`render-worker/runner.py` 是外部 worker skeleton，不做真实渲染。

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
2. API 写入 `/data/videos/{demo_id}/...`，只把 metadata 写回 replay JSON。
3. 保存 `timeOriginSeconds`、`tickStart`、`tickEnd`、`tickRate`。
4. `FirstPersonReplay` 使用 calibration 在视频时间和 demo tick 之间同步。

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
- `GET /demos`
- `PATCH /demos/{demo_id}`
- `POST /demos/{demo_id}/archive`
- `GET /demos/{demo_id}/status`
- `POST /demos/{demo_id}/parse/retry`
- `POST /uploads/mock`
- `POST /uploads/demo`

Replay and coaching:

- `GET /demos/{demo_id}/replay`
- `GET /demos/{demo_id}/coaching`

Video and render:

- `GET /demos/{demo_id}/video`
- `POST /demos/{demo_id}/video/upload`
- `POST /demos/{demo_id}/video/calibration`
- `POST /demos/{demo_id}/render/mock`
- `POST /demos/{demo_id}/render/clip`
- `GET /demos/{demo_id}/render/jobs`

Render worker:

- `GET /render-worker/jobs/next`
- `GET /render-worker/jobs/{job_id}/manifest`
- `POST /render-worker/jobs/{job_id}/media`
- `POST /render-worker/jobs/{job_id}/result`

## Configuration

完整部署准备说明见 `docs/deployment_readiness_v1.md`。关键环境变量：

| Name | Default | Used by |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | frontend browser API/media URL |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | backend API |
| `DATABASE_URL` | `postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach` | API, worker |
| `REDIS_URL` | `redis://localhost:6379/0` | API, worker |
| `REDIS_QUEUE_NAME` | `cs2-demo-jobs` | API, worker |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker |
| `DEV_USER_ID` | `dev-user` | backend API |
| `MAX_RENDER_CLIP_SECONDS` | `60` | backend API |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker |
| `API_BASE_URL` | `http://localhost:8000` | render-worker runner |
| `WORK_DIR` | `.render-worker-work` | render-worker runner |
| `POLL_INTERVAL_SECONDS` | `5` | render-worker runner |
| `DEV_FAKE_VIDEO_PATH` | unset | render-worker fake adapter |
| `CS2_INSTALL_DIR` | unset | render-worker manual adapter |
| `STEAM_USER_DATA_DIR` | unset | render-worker manual adapter |
| `CS2_MANUAL_OUTPUT_FILENAME` | `{job_id}.mp4` | render-worker manual adapter |

Docker Compose uses service names inside containers (`postgres`, `redis`) and host-facing URLs for the browser (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`). Artifact directories default under `ARTIFACT_STORAGE_ROOT=/data`, with per-category overrides for local development. Do not commit production secrets or object storage credentials.

## Deploy Smoke Checklist

Minimal local smoke for a clean environment:

1. `docker compose up --build`
2. `curl http://localhost:8000/health`
3. Open `http://localhost:3000/dashboard`
4. Create a mock upload and wait for completion
5. Upload a real `.dem` or `.zip` if a sample is available
6. Open a demo detail page
7. Use round review quick jumps and confirm replay, tactical map, timeline, parser markers, and coaching cards stay synchronized
8. Click `Generate Clip`
9. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`

## Current Limitations

- 没有真实认证和授权；当前只有 `DEV_USER_ID` / `X-Dev-User-Id` 驱动的 dev-only owner boundary。
- 生产 auth replacement path 仍待实现，建议接入 Clerk、Auth0、Supabase Auth 或其它 identity provider，并把 authenticated user id 映射到 `owner_id`。
- 本地文件和 Docker volumes 通过 local storage adapter 替代对象存储。
- Parser frame 是采样数据，不是完整 tick density。
- `demoparser2` 对不同 demo 的 event family 和字段可用性不稳定；normalizer 必须继续容错。
- Bomb/utility events 是 best-effort；缺失时 UI count、quick jump 或 event-backed rules 可能为空。
- Tactical map 只有 Dust II 是 calibrated；其它支持地图是 approximate。
- 没有经济、装备快照、line-of-sight、utility trajectory 和高级战术上下文。
- `render_clip` 当前只创建合约 job；真实视频要等外部 GPU worker。
- Manual MP4 必须人工校准，且只能代表它实际覆盖的 tick range。
- Ingestion snapshots、replay diagnostics 和 regression fixtures 是 compact QA/debugging aids，不是生产 telemetry、日志平台或 parser trace storage。

## Next Useful Work

优先级较高的下一步：

- 更严格的 upload session / quarantine / S3-R2 storage boundary。
- Parser telemetry 和更稳定的 damage/bomb/utility/round event extraction。
- 更准确的 map-specific coordinate calibration，尤其是 Mirage、Inferno、Ancient、Nuke、Anubis。
- Render worker lease/heartbeat 语义，避免外部 worker claim 后长时间卡住。
- 真实受控 GPU worker adapter：拉取 `.dem`、渲染短 clip、上传 mp4/HLS、提交 result callback。
- 更完整的 rules evidence，包括 economy、utility timing、line-of-sight 和 trade metadata。
- 真实用户系统和 demo ownership。

保持产品方向：核心复盘体验必须在 `.dem` 上传解析后立即可用；真实 first-person footage 是异步 clip 增强，而不是使用网站的前置条件。
