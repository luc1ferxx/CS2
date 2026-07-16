# CS2 Demo AI Coach — Rules-Based 2D Beta V1

这是一个网站型 CS2 demo 复盘与规则教练原型。当前项目重点已经从单纯 mock 流程推进到“真实 `.dem` 解析 spike + Demo Library + 回放复盘界面 + deterministic coaching + render clip 合约”。

当前版本是规则型 2D 公测 V1 的 Stage 3：在 Stage 2 的 provider-neutral OIDC、Redis opaque browser session、owner 授权和私有视频字节访问之上，加入 provider-neutral private artifact storage、`.dem` quarantine/intake/promotion、完整性 metadata 和确定性清理。仍没有可靠任务恢复、parser 隔离、生产 observability/backup、OpenAI 调用或真实 CS2 自动渲染。核心价值是验证 `.dem` 到 2D replay/coaching 的闭环，以及后续受控基础设施的交接边界。

## 当前状态

已经具备的能力：

- Docker Compose 本地栈：`frontend`、`api`、`worker`、`postgres`、`redis`。
- Production identity boundary：OIDC Authorization Code + PKCE/JWKS 验证后创建 Redis-backed opaque `HttpOnly` session；所有 user API 从可信 session 派生稳定的 `owner_id`。`DEV_USER_ID` / `X-Dev-User-Id` 仅在显式 development/test mode 可用。
- `/dashboard` Demo Library：搜索、状态/地图筛选、排序、bounded 上传/任务轮询、重命名、软归档、空/失败/无结果状态、渲染状态摘要。
- Upload/parser observability：demo list/detail responses 包含 compact ingestion snapshot，失败解析有短错误、attempts、stale/active/retryable 状态，并支持 owner-scoped retry。
- Diagnostics boundary：`GET /diagnostics` 只在 development/test 提供 compact 排障信息，production 返回 `404`；`GET /demos/{demo_id}/diagnostics` 始终要求登录并按 owner 隔离。public `GET /health` 只返回 coarse status。
- Private media：视频 metadata 只返回 `/demos/{demo_id}/media/video`；GET/HEAD/Range 在字节交付时再次验证 session、owner 和 artifact path，没有 public `/media/videos` static mount。
- Safe Artifact Intake：公开上传只接受 `.dem`；source 先流式写入 private quarantine，同时计算真实长度和 SHA-256，验证后 promotion 为 owner/demo-bound accepted artifact，只有 accepted source 才能创建 parser job。
- Provider-neutral artifact storage：development/test 使用 private local adapter，production 必须配置 private S3-compatible adapter；source、replay、video 的逻辑 reference 不暴露 bucket、object key、provider URL 或本地路径。
- Mock demo flow：快速生成合成 replay、coaching events 和 mock first-person shell。
- Real demo parser spike：主产品入口上传 `.dem`，后端队列异步解析；archive upload 只保留为开发兼容路径。
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

- 不提供密码、账号资料、团队或 provider-specific 管理 UI；浏览器不处理 provider token。
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

PostgreSQL 只存可索引的元数据、backend-neutral logical reference、compact ingestion/render metadata 和 coaching event rows。Replay frames、parser event contract、video metadata 和 compact replay diagnostics 都在 replay JSON artifact 内；上传 demo、replay artifact 和视频文件始终留在 private artifact storage，不进入 PostgreSQL。

Artifact storage 通过 `backend/app/services/storage.py` 的 provider-neutral contract 统一出入口。服务端生成的 V1 logical reference 绑定 lifecycle state、artifact kind、opaque owner、demo 和随机 artifact id；客户端 filename 不参与 physical object key。Development/test 的 local adapter 使用 API/worker 共享的 private Compose volume，production 选择 private S3-compatible adapter。S3 path 对 seekable private upload 做 1 MiB hash pass 后直接上传，并用 generation-bound、destination-conditional server-side copy promotion，不创建第二份整文件 scratch。Legacy `local://uploads|replays|videos/...` 只保留为本地旧数据读取兼容，不是新 source 通过 intake 的证明。

不要把 `.dem`、replay JSON、大视频或 raw parser dump 存入 PostgreSQL，也不要把 cloud credentials、bucket-specific code 或 public object URL 分散到 API route、worker 或 parser。完整 Stage 3 生命周期、错误分类和验收合同见 `docs/object_storage_safe_artifact_intake_v1.md`。

## Production Identity and Owner Boundary

Production uses provider-neutral OIDC Authorization Code flow with PKCE. The backend validates state, nonce, issuer, audience, timestamps, the configured `RS256`/`ES256` asymmetric allow-list, and JWKS, then maps the verified `(issuer, subject)` pair to a stable opaque `owner_v1_...` value that fits the existing `owner_id` column. It stores an opaque session in Redis and sends only an `HttpOnly`, `SameSite=Lax`, `Secure`, `__Host-` production cookie to the browser. Production frontend/API use one exact HTTPS origin; `CORS_ORIGINS` must contain only that origin, and unsafe browser mutations require the same exact `Origin`. All browser-private responses disable shared caching and vary on cookie/origin. Provider tokens, raw subject/issuer claims, session tokens, storage keys, unknown internal replay fields, local paths, and worker-provided raw error text are not user-facing payloads or application logs; render failures use stable safe codes and copy.

API 里的 demo、upload、manual video、coaching/replay read、library mutation、user-facing render job actions 和 private media 都从 session 派生 owner 并复用现有 owner-scoped queries。`GET /auth/login` starts the OIDC flow; the server-only `/auth/oidc/callback` creates the session and returns through the trusted frontend `/auth/callback` page; `GET /auth/session` checks it; `POST /auth/logout` revokes it.

本地测试仍可显式设置 `AUTH_MODE=development` 或 `AUTH_MODE=test`，用 `DEV_USER_ID` 和请求头模拟 owner：

```bash
X-Dev-User-Id: owner-a
```

该 header 在 production mode 被忽略或拒绝，绝不作为 production identity fallback。完整身份、cookie、owner mapping、private media 和 acceptance matrix 见 `docs/production_auth_owner_private_media_v1.md`。

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
  deployment_target_decision_v1.md
  internal_preview_packaging_v1.md
  release_candidate_qa_v1.md
  *_goal.md               prior phase goals and design notes
```

## 本地运行

完整本地栈：

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

### 2. Mock Upload

`POST /uploads/mock` 创建 `mock_parse` job。worker 会写入 mock replay JSON 和 coaching rows，然后把 demo 标记为 `completed`。这条路径用于快速验证前端 replay、timeline、coaching 和 render UI。

### 3. Real Demo Upload

`POST /uploads/demo` 的唯一公开产品类型是 `.dem`，实际字节上限为 1 GiB；`.zip` 和其它 archive 在写入前拒绝。API 不运行 parser：它先把请求流式写入 private quarantine，计算真实长度和 SHA-256，执行已固化的 extension/MIME/content policy，再把验证通过的 generation promotion 为 immutable accepted source。只有 accepted metadata 已绑定 owner/demo 并随 `Demo` + `real_parse` job 一起提交后才会推入 Redis。语义损坏但无法由 byte-level intake 证明的 `.dem` 仍由异步 parser 安全归类为 `INVALID_DEMO`。

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

- `FirstPersonReplay`：有 owner-authorized `/demos/{demo_id}/media/video` 时用 credentialed GET/Range 播放 MP4；没有或无法访问时显示同步的 2D/mock first-person shell。
- `Timeline`：play/pause、seek、0.5x/1x/2x/4x、coaching markers、parser event markers。
- `ReplayViewer`：tactical map、玩家点位、死亡状态、bomb state、附近 parser events。
- `RoundReviewPanel`：回合列表、winner、tick range、first kill、plant、kill/utility/coaching counts、quick jumps。
- `CoachingPanel`：按回合分组，支持 severity/rule/search 过滤，点击事件跳到 tick，并可为事件创建 render clip。
- `ReplayDiagnosticsPanel`：compact contract diagnostics，包括 contract version、parser/coaching/round/player/frame counts、legacy normalization、missing/degraded optional fields、missing event families 和 render fallback state。
- `RenderOperatorPanel`：内部 operator 视角展示最新 `render_clip` job、tick range、event/player/POV、视频输出状态和错误。
- `VideoSetupPanel`：开发/QA 用手动 MP4 上传和 sync calibration。

这些视图共用同一个 `currentTick`、`selectedRound` 和 replay contract；不要引入平行状态来让回合列表、timeline、地图和 coaching 脱节。

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
- `tickRate > 0`
- clip 时长不超过 `MAX_RENDER_CLIP_SECONDS`，默认 60 秒

本地 worker 识别 `render_clip` 后会把 job 从 `queued` 推到 `rendering`，然后标记为 `failed`：

```text
GPU worker not connected for render_clip. A separate Windows/Linux GPU worker or manual operator must process this job.
```

这是有意设计的边界标记。API 容器不能负责真实 CS2 渲染。

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

API 只接受与同一个 `rendering` job、owner、demo、immutable generation 和请求 tick 区间完全匹配的 accepted output。另一个 job 的 artifact、过期 generation、不同 tick 范围或没有先绑定 media 的 callback 都会失败关闭。Development local adapter 仍可解析旧 `/media/videos/...` callback 作为 legacy compatibility，但 runner 的正常路径必须使用 media upload 返回的 accepted `storageKey`。User payload 只投影 `/demos/{demo_id}/media/video`，不返回 `storageKey`。

失败回调把 replay failure state 与 terminal job 放在同一个 DB transaction 中，并在提交后删除该 job 的 bound output candidate；如果当前 replay video 是 `manual_upload`，失败不会清掉已有手动视频 metadata。成功回调同样原子提交新 replay reference 与 terminal job，随后清理旧 replay generation。已完成或已失败的 terminal render job 会拒绝后续 callback，避免 late callback 改写最终状态。

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
- `GET /auth/login`
- `GET /auth/oidc/callback`
- `GET /auth/session`
- `POST /auth/logout`
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

Render worker:

- `GET /render-worker/jobs/next`
- `GET /render-worker/jobs/{job_id}/manifest`
- `POST /render-worker/jobs/{job_id}/media`
- `POST /render-worker/jobs/{job_id}/result`

## Configuration

身份和 private media 的完整合同见 `docs/production_auth_owner_private_media_v1.md`；runtime deployment 说明见 `docs/deployment_readiness_v1.md`，Cloud Preview runbook 见 `docs/cloud_preview_deploy_v1.md`。关键环境变量：

| Name | Default | Used by |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | frontend browser API/media URL |
| `AUTH_MODE` | unset (required) | backend identity mode: `development`, `test`, or `production` |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | server-trusted frontend redirect origin; must equal the HTTPS API origin in production |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | backend public origin; same exact origin as frontend in production |
| `OIDC_ISSUER` | unset | exact production issuer |
| `OIDC_CLIENT_ID` | unset | production OIDC client/audience |
| `OIDC_CLIENT_SECRET` | unset | optional server-side confidential-client secret |
| `OIDC_ALLOWED_ALGORITHMS` | `RS256,ES256` | supported asymmetric signature allow-list |
| `OIDC_AUTHORIZATION_ENDPOINT` | unset | production authorization endpoint |
| `OIDC_TOKEN_ENDPOINT` | unset | production server-side code exchange endpoint |
| `OIDC_JWKS_URL` | unset | production trusted signing keys |
| `OIDC_REDIRECT_URI` | unset | production backend `/auth/oidc/callback` URL |
| `AUTH_COOKIE_SECURE` | `false` | must be enabled in production |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | opaque session cookie name |
| `AUTH_STATE_COOKIE_NAME` | `__Host-cs2_oidc_state` | short-lived OIDC state cookie name |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | bounded Redis/browser session lifetime |
| `AUTH_LOGIN_TTL_SECONDS` | `300` | bounded one-time login attempt lifetime |
| `AUTH_CLOCK_SKEW_SECONDS` | `30` | bounded OIDC timestamp leeway |
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

Docker Compose uses service names inside containers (`postgres`, `redis`) and host-facing URLs for the browser (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`). Local Compose must explicitly use `AUTH_MODE=development` and may use the local adapter. Production requires the complete well-formed HTTPS OIDC/`__Host-` cookie/single-origin/CORS contract, a non-default render-worker credential, and a private S3-compatible artifact configuration or startup fails closed. The queue worker validates its narrower auth contract plus the same production storage boundary, without receiving browser OIDC/client/cookie secrets. Local artifacts default under `ARTIFACT_STORAGE_ROOT=/data`; production credentials may come from the runtime credential chain and must never enter `NEXT_PUBLIC_*`, URLs, source, logs, or checked-in env files. Hosted preview builds can use `docker-compose.preview.yml`; rebuild the frontend image whenever `NEXT_PUBLIC_API_BASE_URL` changes because Next.js bundles that public origin at build time.

## Deploy Smoke Checklist

Minimal local smoke for a clean environment. For internal preview handoff, use `docs/internal_preview_packaging_v1.md`. For full release-candidate validation, prefer `./scripts/rc_check.sh` plus the manual browser checklist in `docs/release_candidate_qa_v1.md`.

1. `docker compose up --build`
2. `curl http://localhost:8000/health`
3. In explicit development mode, `curl http://localhost:8000/diagnostics`; in production, confirm it returns `404`
4. Open `http://localhost:3000/dashboard`; production should require OIDC sign-in, while development uses the explicit local harness
5. Create a mock upload and wait for completion
6. Upload a real `.dem` if a sample is available
7. Open a demo detail page
8. Use round review quick jumps and confirm replay, tactical map, timeline, parser markers, and coaching cards stay synchronized
9. If video exists, confirm `/demos/{demo_id}/media/video` supports authenticated GET/HEAD/Range and the 2D fallback remains available when media is denied
10. Click `Generate Clip`
11. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`
12. Run the owner A/B/anonymous/expired/revoked matrix in `docs/production_auth_owner_private_media_v1.md`
13. For development-mode Cloud Preview validation, run `API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py` or point those variables at the preview URLs
14. For stricter parser validation, set `SAMPLE_DEMO_PATH` and rerun the smoke; add `--require-sample` when preview validation must fail without a fresh real upload

## Current Limitations

- Production identity is provider-neutral OIDC; selecting and provisioning a concrete provider remains a deployment decision, not an application-code dependency.
- Redis-backed opaque sessions require Redis availability; Stage 4 has not yet added durable job delivery or crash recovery.
- Development/test 可使用 private local adapter；production 必须提供 private S3-compatible storage，但具体 bucket/IAM/credential provisioning 不由仓库创建。
- Parser frame 是采样数据，不是完整 tick density。
- `demoparser2` 对不同 demo 的 event family 和字段可用性不稳定；normalizer 必须继续容错。
- Bomb/utility events 是 best-effort；缺失时 UI count、quick jump 或 event-backed rules 可能为空。
- Tactical map 只有 Dust II 是 calibrated；其它支持地图是 approximate。
- 没有经济、装备快照、line-of-sight、utility trajectory 和高级战术上下文。
- `render_clip` 当前只创建合约 job；真实视频要等外部 GPU worker。
- Manual MP4 必须人工校准，且只能代表它实际覆盖的 tick range。
- Ingestion snapshots、safe diagnostics、replay diagnostics 和 regression fixtures 是 compact QA/debugging aids，不是生产 telemetry、日志平台或 parser trace storage。

## Next Useful Work

下一阶段必须是 Stage 4：可靠任务投递、崩溃恢复、原子 claim 和幂等执行。之后仍按冻结顺序推进 parser 隔离，migration/CI/CD/observability/backup，最后建立真实 demo corpus 并进行邀请制公测。每个阶段一个 branch、一个 PR，不跨阶段捆绑。

保持产品方向：核心复盘体验必须在 `.dem` 上传解析后立即可用；真实 first-person footage 是异步 clip 增强，而不是使用网站的前置条件。
