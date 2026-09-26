# API Reference V1

本文件是 HTTP 接口的完整清单和调用示例。README 只保留产品边界和合同描述，具体路由与 curl 命令看这里。

接口的行为约束（owner 隔离、fail-closed、private media）由 `production_auth_owner_private_media_v1.md` 定义，本文件不重复。

API 运行时的交互式文档在 `http://localhost:8000/docs`（仅 development/test；production 不提供 `/docs`、`/redoc`、`/openapi.json`）。

Production 隐藏 dev/QA 路由：`POST /uploads/mock`、`POST /demos/{demo_id}/video/upload`、`POST /demos/{demo_id}/video/calibration`、`POST /demos/{demo_id}/render/mock` 返回 `404`（manual video upload 在读取 body 之前就返回 `404`）。`POST /demos/{demo_id}/render/clip` 和 `POST /demos/{demo_id}/render/jobs/{job_id}/retry` 在 production 下也返回 `404`，除非设置 `RENDER_CLIPS_ENABLED=1`。未登录的 production 写请求仍先得到 `401`。`GET /auth/me` 返回 `capabilities: {devTools, renderClips}`，前端据此隐藏对应入口。

Production 的 Steam 登录受 `STEAM_LOGIN_ALLOWLIST` 限制（逗号分隔的 Steam ID64，或 `*`）。Session 记录登录时的 Steam ID；名单不是 `*` 时，每次解析 session 都会重新核对名单：被移出名单的用户、以及没有记录 Steam ID 的旧 session，在所有需要登录的路由上都会得到 `401`。

Production 的上传额度（`DEMO_UPLOAD_DAILY_LIMIT`、`DEMO_ACTIVE_PARSE_LIMIT`、`PARSE_QUEUE_GLOBAL_LIMIT`）作用于 `POST /uploads/demo` 和 `POST /demos/{demo_id}/parse/retry`。拒绝时返回 `{"detail": {"code": ..., "message": ..., "retryAfterSeconds": ...}}`、`Retry-After` 头和 `Cache-Control: private, no-store`：`parse_queue_full` → `503`，`active_parse_limit` → `429`，`upload_daily_limit` → `429`（仅上传），额度检查本身不可用时 `upload_quota_unavailable` → `503`（仅上传）。上传在读取 body 之前就会被拒绝。development/test 不检查额度。

## 用户 API

所有 demo/upload/replay/coaching/library/render/private-media 路由都从可信 session 派生 `owner_id`，并复用各自已有的 owner-scoped 查询。

### Core

- `GET /health`
- `GET /diagnostics`（仅 development/test；production 返回 `404`）
- `GET /auth/steam/login`
- `GET /auth/steam/callback`（不在 `STEAM_LOGIN_ALLOWLIST` 中的 Steam ID 会 `303` 到 `/auth/callback?error=not_invited`，不创建账号、不发 session，并撤销浏览器已有的 session）
- `GET /auth/me`（Steam 账号额外返回登录者本人的 `account.steamId`，用于在比赛中默认定位本人；development/OIDC 不返回）
- `POST /auth/logout`
- `GET /auth/login`、`GET /auth/oidc/callback`、`GET /auth/session`（`AUTH_PROVIDER=oidc` / legacy frontend 兼容）
- `GET /steam/connection`
- `POST /steam/connection/credentials`
- `DELETE /steam/connection`
- `POST /steam/sync`
- `GET /steam/matches`
- `POST /steam/matches/{match_id}/import`
- `GET /demos`
- `PATCH /demos/{demo_id}`
- `POST /demos/{demo_id}/archive`
- `GET /demos/{demo_id}/status`
- `GET /demos/{demo_id}/diagnostics`
- `POST /demos/{demo_id}/parse/retry`（production 受全站/个人处理中额度限制：`503` `parse_queue_full`、`429` `active_parse_limit`）
- `GET /uploads/quota`（当前 owner 的上传额度：`{dailyLimit, dailyUsed, dailyResetSeconds, activeLimit, activeCount, maxUploadBytes}`；development/test 下各 limit 为 `null`；`Cache-Control: private, no-store`；仅供提示，上传时仍以 `POST /uploads/demo` 的检查为准，不报告全站 `PARSE_QUEUE_GLOBAL_LIMIT`）
- `POST /uploads/mock`（仅 development/test；production 返回 `404`）
- `POST /uploads/demo`（production 受上传额度限制：`503` `parse_queue_full`、`429` `active_parse_limit` / `upload_daily_limit`、`503` `upload_quota_unavailable`）

### Replay and coaching

- `GET /demos/{demo_id}/replay`（紧凑 JSON；客户端发送 `Accept-Encoding: gzip` 时 gzip 压缩。JSON 响应 ≥1 KB 时都按此压缩，媒体、Range 与流式响应不压缩）
- `GET /demos/{demo_id}/coaching`（每条事件附带当前 owner 自己的 `feedback`：`{verdict, note, updated_at}` 或 `null`）
- `PUT /demos/{demo_id}/coaching/{event_id}/feedback`（body `{"verdict": "helpful" | "irrelevant" | "unsure", "note"?: ≤240 字符}`；事件不属于该 demo → 404）
- `DELETE /demos/{demo_id}/coaching/{event_id}/feedback`（204，幂等）
- `GET /coaching/feedback/summary?demo_id=`（owner 全部或指定 demo 的按规则判定汇总；见 `docs/coaching_feedback_v1.md`）

### Video and render

- `GET /demos/{demo_id}/video`
- `GET|HEAD /demos/{demo_id}/media/video`（校验 session + owner；支持 byte range）
- `POST /demos/{demo_id}/video/upload`（仅 development/test）
- `POST /demos/{demo_id}/video/calibration`（仅 development/test）
- `POST /demos/{demo_id}/render/mock`（仅 development/test）
- `POST /demos/{demo_id}/render/clip`（production 需 `RENDER_CLIPS_ENABLED=1`）
- `POST /demos/{demo_id}/render/jobs/{job_id}/retry`（production 需 `RENDER_CLIPS_ENABLED=1`）
- `GET /demos/{demo_id}/render/jobs`
- `GET|HEAD /demos/{demo_id}/render/jobs/{job_id}/media/video`（支持 Range）

### Render worker（service credential，非 browser session）

- `GET /render-worker/jobs/next`
- `GET /render-worker/jobs/{job_id}/manifest`
- `GET /render-worker/jobs/{job_id}/source`
- `POST /render-worker/jobs/{job_id}/media`
- `POST /render-worker/jobs/{job_id}/result`

## 调用示例

下列示例中的 `X-Dev-User-Id` header 只在显式 development/test mode 可用，production 会忽略或拒绝它，绝不作为 identity fallback。

### 健康检查

```bash
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
```

`/health` 只返回 coarse `status=ok|degraded`：正常时 HTTP `200` `{"status":"ok"}`，数据库、Redis 或 worker 配置任一检查失败时 HTTP `503` `{"status":"degraded"}`；不泄漏 dependency、URL 或 storage 配置。development/test 的 `/diagnostics` 返回 compact readiness、Redis queue/worker heartbeat、job counts、recent failed job summary 和 render-worker inferred status；production 对该 system endpoint 返回 `404`。它不暴露本地 storage path、env dump、token、stack trace、raw parser data 或上传内容。

### 上传 `.dem`

```bash
# Advisory allowance before uploading (limits are null outside production):
curl http://localhost:8000/uploads/quota
curl -F "file=@sample.dem" http://localhost:8000/uploads/demo
# development/test harness only:
curl -H "X-Dev-User-Id: owner-a" -F "file=@sample.dem" http://localhost:8000/uploads/demo
```

### 重试失败的解析

source artifact 仍存在时，owner-scoped retry 可以不重新上传就重新排队：

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/parse/retry
# development/test harness only:
curl -X POST -H "X-Dev-User-Id: owner-a" http://localhost:8000/demos/{demo_id}/parse/retry
```

### 创建 render clip

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

API 在**任何 `RENDER_WORKER_MODE` 下**都会验证：demo 已完成解析、replay blob 存在、`tickRate > 0`、`tickEnd > tickStart`、所选 POV 与已解析玩家名单一致、clip 时长不超过 `MAX_RENDER_CLIP_SECONDS`（默认 60 秒）。

**仅当 `RENDER_WORKER_MODE=external`** 时（默认是 `fallback`，不执行这几条）额外验证：source 必须是 accepted `.dem`、必须选中带 Steam ID 的玩家、tick rate 与已解析 replay 一致、tick 区间落在 replay 范围内。见 `backend/app/services/demo_service.py` 的 `create_render_clip_job`。

### 手动 MP4 绑定（开发/QA）

```bash
curl -F "file=@clip.mp4" http://localhost:8000/demos/{demo_id}/video/upload
```

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/video/calibration \
  -H "Content-Type: application/json" \
  -d '{"timeOriginSeconds":12.5,"tickStart":12345,"tickEnd":54321}'
```

## Render Worker V1 API

Render worker API 使用独立的 `X-Render-Worker-Token` service credential，不接受 browser session 代替。本地默认 token 是 `dev-render-worker-token`；production 必须配置非默认 `RENDER_WORKER_TOKEN`，否则 startup validation fails closed。Stage 3 保留这条 service-to-service auth，并在 multipart parser 消费媒体 body 前拒绝错误 token。

### 领取任务

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

### 上传媒体

仅在 job 已 claim 为 `rendering` 后上传 dev/worker mp4。响应中的 `storageKey` 是该 job 的 immutable `outputArtifact` snapshot reference，必须原样用于成功回调：

```bash
curl -X POST http://localhost:8000/render-worker/jobs/{job_id}/media \
  -H "X-Render-Worker-Token: dev-render-worker-token" \
  -F "file=@clip.mp4"
```

### 提交回调

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

### 回调的原子性与拒绝条件

API 只接受与同一个 `rendering` job、owner、demo、immutable generation 和请求 tick 区间完全匹配的 accepted output。另一个 job 的 artifact、过期 generation、不同 tick 范围或没有先绑定 media 的 callback 都会失败关闭。Development local adapter 仍可解析旧 `/media/videos/...` callback 作为 legacy compatibility，但 runner 的正常路径必须使用 media upload 返回的 accepted `storageKey`。User payload 只投影默认或 job 专属的私有媒体路由，不返回 `storageKey`。

失败回调把 replay failure state 与 terminal job 放在同一个 DB transaction 中，并在提交后删除该 job 的 bound output candidate；如果当前 replay video 是 `manual_upload`，失败不会清掉已有手动视频 metadata。成功回调同样原子提交新 replay reference 与 terminal job，随后清理旧 replay generation。已完成或已失败的 terminal render job 会拒绝后续 callback，避免 late callback 改写最终状态。

## 响应片段

### Ingestion snapshot

Demo list/detail responses 包含：

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

解析失败会保留 compact failure metadata，含 `errorCode`、`message`、`failedAt`、`updatedAt`、`retryable`、`attemptCount`。前端按 `errorCode` 显示中文原因（`frontend/lib/demo-library.ts` 的 `parseFailureCopy`），`message` 只留作技术信息。

### Parser failure taxonomy

短 `errorCode` 加一句安全文案：

- `INVALID_DEMO`：无效、不可读或 archive 内没有 `.dem`
- `UNSUPPORTED_PARSER_FORMAT`：上传格式或 parser support 不可用
- `MISSING_MATCH_METADATA`：缺少 rounds、playback ticks 或可采样 event ticks
- `MISSING_FRAMES`：parser 没有返回可用 player position ticks
- `NORMALIZATION_FAILED`：parser 输出无法整理成 replay contract
- `STORAGE_READ_FAILED`：上传 source artifact 无法从 storage service 读取
- `PARSER_UNEXPECTED`：未分类 parser exception

Parser/demo failure responses 和 diagnostics 不包含本地路径、stack trace 或 raw parser dump；开发排障细节只保留在 worker process logs。
