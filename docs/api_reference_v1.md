# API Reference V1

本文件是 HTTP 接口的完整清单和调用示例。README 只保留产品边界和合同描述，具体路由与 curl 命令看这里。

接口的行为约束（owner 隔离、fail-closed、private media）由 `production_auth_owner_private_media_v1.md` 定义，本文件不重复。

API 运行时的交互式文档在 `http://localhost:8000/docs`（仅 development/test；production 不提供 `/docs`、`/redoc`、`/openapi.json`）。

Production 隐藏 dev/QA 路由：`POST /uploads/mock`、`POST /demos/{demo_id}/video/upload`、`POST /demos/{demo_id}/video/calibration`、`POST /demos/{demo_id}/render/mock` 返回 `404`（manual video upload 在读取 body 之前就返回 `404`）。`POST /demos/{demo_id}/render/clip` 和 `POST /demos/{demo_id}/render/jobs/{job_id}/retry` 在 production 下也返回 `404`，除非设置 `RENDER_CLIPS_ENABLED=1`。未登录的 production 写请求仍先得到 `401`。`GET /auth/me` 返回 `capabilities: {devTools, renderClips}`，前端据此隐藏对应入口。

Production 的 Steam 登录受 `STEAM_LOGIN_ALLOWLIST` 限制（逗号分隔的 Steam ID64，或 `*`）。Session 记录登录时的 Steam ID；名单不是 `*` 时，每次解析 session 都会重新核对名单：被移出名单的用户、以及没有记录 Steam ID 的旧 session，在所有需要登录的路由上都会得到 `401`。

Session 是滑动续期的：空闲窗口 `AUTH_SESSION_TTL_SECONDS`（默认 1 小时），从登录起绝对上限 `AUTH_SESSION_MAX_AGE_SECONDS`（默认 24 小时）。带着有效 cookie 的请求（读写都算）在剩余时间不到空闲窗口一半时，响应里会带一个新的 `Set-Cookie`：同一个 token、同样的属性（`HttpOnly`、`Secure`、`SameSite=lax`、`Path=/`），只有 `Max-Age` 变成新的剩余时间（一般是整个空闲窗口，接近上限时是到上限为止）。登录时间不变，所以账户删除和"在所有设备上退出"照样让它失效；已经退出、被撤销或过期的 session 不会被续上。登录回调、退出登录和账户删除自己设置或清除 cookie，不再追加续期；分片上传的 part `PUT`（只认上传令牌）和 `/render-worker/` 路由不读 cookie，也不续期。

Production 的上传额度（`DEMO_UPLOAD_DAILY_LIMIT`、`DEMO_ACTIVE_PARSE_LIMIT`、`PARSE_QUEUE_GLOBAL_LIMIT`）作用于 `POST /uploads/demo`、分片上传的 `POST /uploads/sessions/{session_id}/complete` 和 `POST /demos/{demo_id}/parse/retry`。拒绝时返回 `{"detail": {"code": ..., "message": ..., "retryAfterSeconds": ...}}`、`Retry-After` 头和 `Cache-Control: private, no-store`：`parse_queue_full` → `503`，`active_parse_limit` → `429`，`upload_daily_limit` → `429`（仅上传），额度检查本身不可用时 `upload_quota_unavailable` → `503`（仅上传）。`POST /uploads/demo` 在读取 body 之前就会被拒绝；分片上传在建会话（`POST /uploads/sessions`）时先做一次同样的检查，只作提示，权威检查在 `complete` 的提交事务里，那里额度不足时会话回到 `open`、已收到的分片保留，不写上传账本。development/test 不检查额度。

## 用户 API

所有 demo/upload/replay/coaching/library/render/private-media 路由都从可信 session 派生 `owner_id`，并复用各自已有的 owner-scoped 查询。

### Core

- `GET /health`
- `GET /health/worker`（解析 worker 心跳：所有模式都提供，production 也是）
- `GET /diagnostics`（仅 development/test；production 返回 `404`）
- `GET /auth/steam/login`
- `GET /auth/steam/callback`（不在 `STEAM_LOGIN_ALLOWLIST` 中的 Steam ID 会 `303` 到 `/auth/callback?error=not_invited`，不创建账号、不发 session，并撤销浏览器已有的 session）
- `GET /auth/me`（Steam 账号额外返回登录者本人的 `account.steamId`，用于在比赛中默认定位本人；development/OIDC 不返回）
- `POST /auth/logout`
- `DELETE /auth/account`（body `{"confirm": "delete-my-account"}` → `204`，删除当前账户及全部数据，使该账户在所有设备上的会话失效，并按退出登录的属性让会话 cookie 过期；confirm 缺失或错误 → `400` `confirmation_required`；没有会话 → `401`；development/test → `409` `account_deletion_unavailable`。见 `docs/data_deletion_v1.md`）
- `GET /auth/login`、`GET /auth/oidc/callback`、`GET /auth/session`（`AUTH_PROVIDER=oidc` / legacy frontend 兼容）
- `GET /steam/connection`
- `POST /steam/connection/credentials`
- `DELETE /steam/connection`
- `POST /steam/sync`
- `GET /steam/matches`
- `POST /steam/matches/{match_id}/import`
- `GET /demos`（每项带可选 `matchSummary`，见下方“比分摘要”）
- `PATCH /demos/{demo_id}`
- `POST /demos/{demo_id}/archive`
- `DELETE /demos/{demo_id}`（永久删除比赛及其任务、建议、评价和存储文件，不可撤销：`204`；别人的或不存在的 id、重复删除 → `404`；Steam 导入的比赛只解除关联。见 `docs/data_deletion_v1.md`）
- `GET /demos/{demo_id}/status`（同样带可选 `matchSummary`）
- `GET /demos/{demo_id}/diagnostics`
- `POST /demos/{demo_id}/parse/retry`（production 受全站/个人处理中额度限制：`503` `parse_queue_full`、`429` `active_parse_limit`）
- `GET /uploads/quota`（当前 owner 的上传额度，每日已用次数来自上传账本，删除比赛不退还：`{dailyLimit, dailyUsed, dailyResetSeconds, activeLimit, activeCount, maxUploadBytes}`；development/test 下各 limit 为 `null`；`Cache-Control: private, no-store`；仅供提示，上传时仍以 `POST /uploads/demo` 或分片上传 `complete` 的检查为准，不报告全站 `PARSE_QUEUE_GLOBAL_LIMIT`）
- `POST /uploads/mock`（仅 development/test；production 返回 `404`）
- `POST /uploads/demo`（单个 multipart 请求上传整个 `.dem`，保留给 curl 和冒烟脚本，前端改用下面的分片上传；production 受上传额度限制：`503` `parse_queue_full`、`429` `active_parse_limit` / `upload_daily_limit`、`503` `upload_quota_unavailable`）
- `POST /uploads/sessions`、`GET /uploads/sessions/current`、`GET /uploads/sessions/{session_id}`、`POST /uploads/sessions/{session_id}/token`、`PUT /uploads/sessions/{session_id}/parts/{index}`、`POST /uploads/sessions/{session_id}/complete`、`DELETE /uploads/sessions/{session_id}`（分片、并行、可续传的 `.dem` 上传，见下方[分片上传会话](#分片上传会话)）

### Replay and coaching

- `GET /demos/{demo_id}/replay`（紧凑 JSON；客户端发送 `Accept-Encoding: gzip` 时 gzip 压缩。JSON 响应 ≥1 KB 时都按此压缩，媒体、Range 与流式响应不压缩。带 `ETag` 和 `Cache-Control: private, no-store`，`If-None-Match` 命中时返回 `304`，见下方[回放响应缓存](#回放响应缓存etag-与-304)）
- `GET /demos/{demo_id}/coaching`（每条事件附带当前 owner 自己的 `feedback`：`{verdict, note, updated_at}` 或 `null`）
- `PUT /demos/{demo_id}/coaching/{event_id}/feedback`（body `{"verdict": "helpful" | "irrelevant" | "unsure", "note"?: ≤240 字符}`；事件不属于该 demo（包括后台重算后已不存在的建议）→ 404）
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
curl http://localhost:8000/health/worker
curl http://localhost:8000/diagnostics
```

`/health` 只返回 coarse `status=ok|degraded`：正常时 HTTP `200` `{"status":"ok"}`，数据库、Redis 或 worker 配置任一检查失败时 HTTP `503` `{"status":"degraded"}`；不泄漏 dependency、URL 或 storage 配置。`/health/worker` 同样粗粒度：解析 worker 的 Redis 心跳不超过 90 秒时 HTTP `200` `{"status":"ok"}`，心跳缺失、过期、读不出或 Redis 连不上时 HTTP `503` `{"status":"degraded"}`。它在 production 也提供（经 Caddy 的 `@api` 路由），供 `deploy.sh`、`prod_smoke.sh`、`watch.sh` 和外部拨测使用；故意不并进 `/health`，因为 caddy 和 frontend 依赖 api 的健康检查启动，worker 挂掉不能把整站拖下。development/test 的 `/diagnostics` 返回 compact readiness、Redis queue/worker heartbeat、job counts、recent failed job summary 和 render-worker inferred status；production 对该 system endpoint 返回 `404`。它不暴露本地 storage path、env dump、token、stack trace、raw parser data 或上传内容。

### 上传 `.dem`

```bash
# Advisory allowance before uploading (limits are null outside production):
curl http://localhost:8000/uploads/quota
curl -F "file=@sample.dem" http://localhost:8000/uploads/demo
# development/test harness only:
curl -H "X-Dev-User-Id: owner-a" -F "file=@sample.dem" http://localhost:8000/uploads/demo
```

### 分片上传会话

浏览器上传 `.dem` 走这条路径：先建会话，再把文件按 `partSize`（`UPLOAD_PART_BYTES`，默认 8 MiB）切片，每次最多 `maxParallelParts`（默认 4）片并行 `PUT`，最后 `complete`。中断后可以续传：已收到的分片保存在 API 所在主机的本地磁盘上（命名卷 `upload-staging`），直到会话完成、放弃或过期（从创建算起 `UPLOAD_SESSION_TTL_SECONDS`，默认 24 小时，不续期）。`complete` 把各片拼成一个流交给与 `POST /uploads/demo` 完全相同的 intake（隔离区、SHA-256、签名检查、转正、建行、派发），所以结果、错误码和额度规则都与旧路由一致。

所有响应都带 `Cache-Control: private, no-store`。会话类错误用结构化的 `{"detail": {"code", "message", "retryAfterSeconds"?, ...}}`（额外字段平铺在 `detail` 里）；intake 错误沿用旧路由的 `{"detail": "<安全文案>", "errorCode": "<code>"}`。

| 路由 | 认证 | 成功 | 主要错误 |
| --- | --- | --- | --- |
| `POST /uploads/sessions`，body `{filename, size, contentType?, replace?}` | cookie；production 还要 `Origin` | `201` `{sessionId, uploadToken, partSize, partCount, maxParallelParts, expiresAt, receivedParts: []}` | `400` `INTAKE_TYPE_REJECTED` / `INTAKE_EMPTY` / `INTAKE_TRUNCATED` / `INTAKE_CONTENT_MISMATCH`；`413` `INTAKE_TOO_LARGE`；额度 `429` / `503`（形状同上）；`409` `upload_session_exists`（每个 owner 同时只有一个未完成的会话，`detail` 附 `sessionId, filename, size, receivedBytes, state`；`replace: true` 丢弃旧的 `open` 会话后重建，旧会话正在 `completing` 时仍是 `409`）；`503` `upload_capacity_busy`（全站同时打开的会话达到 `UPLOAD_SESSION_GLOBAL_LIMIT`）；`503` `upload_storage_full`（暂存盘余量不足 `size + UPLOAD_STAGING_MIN_FREE_BYTES`）；`503` `INTAKE_STORAGE_UNAVAILABLE`；`401` `account_deleted` |
| `GET /uploads/sessions/current` | cookie | `200` `{session: <状态> 或 null}`（不用 `204`，前端的 JSON helper 总会解析 body） | — |
| `GET /uploads/sessions/{session_id}` | cookie，按 owner 隔离 | `200` `{sessionId, state, filename, size, partSize, partCount, maxParallelParts, receivedParts, receivedBytes, part0Sha256?, expiresAt, demo?, error?}`；`state` 为 `open` / `completing` / `completed` / `failed`；`demo` 是 `completed` 时建好的 `DemoListItem`，`error` 是 `failed` 时的 `{errorCode, message}` | `404`（别人的、不存在或已过期的会话） |
| `POST /uploads/sessions/{session_id}/token` | cookie；production 还要 `Origin` | `200` `{uploadToken, ...状态}`，旧令牌随即失效 | `404`；`409` `upload_session_not_open` |
| `PUT /uploads/sessions/{session_id}/parts/{index}`，body 为这一片的原始字节 | 只认 `X-Upload-Token`（不认 cookie，也不需要）；production 下 `Origin` 必须是站点源 | `200` `{index, sizeBytes, sha256, receivedCount}`；同一片可以重复上传 | `404` `upload_session_not_found`（会话不存在、已过期或令牌不对，不区分）；`400` `upload_part_invalid`（index 越界，`Content-Length` 缺失或不等于这一片的长度，body 长度不符，`X-Part-SHA256` 格式不对）；`422` `upload_part_digest_mismatch`（与 `X-Part-SHA256` 不符，不写入）；`409` `upload_session_not_open`；`400` `INTAKE_CONTENT_MISMATCH`（第 0 片是压缩包或可执行文件的签名，会话随即转为 `failed`）；`503` `INTAKE_BUSY` 带 `Retry-After`（全站分片池 `UPLOAD_PART_POOL` 或本会话的并行上限已满）；`503` `INTAKE_STORAGE_UNAVAILABLE`；`413`（`Content-Length` 超过 `UPLOAD_PART_BYTES + 64 KiB`）；`403`（`Origin` 不对） |
| `POST /uploads/sessions/{session_id}/complete` | cookie；production 还要 `Origin` | 首次 `201` `DemoListItem`；已经完成过、比赛还在时 `200` 同一个 `DemoListItem`；会话的租约还在、intake 槽却空闲时（上一次尝试中断了；真正在处理的尝试占着 intake 槽，这时得到的是 `503` `INTAKE_BUSY`）返回 `202` `{state: "completing"}`，客户端隔几秒再次 `POST …/complete`（API 重启后的租约由这次请求接手；`GET /uploads/sessions/{id}` 只读状态，不会接手） | `409` `upload_parts_missing`（`detail.missingParts`，补传后再 `complete`）；`409` `upload_parts_in_flight` 带 `Retry-After: 1`；额度 `429` / `503`（会话回到 `open`）；`401` `account_deleted`；intake 拒绝 `400` / `413`（会话转为 `failed`，暂存分片随即删除）；`503` `INTAKE_BUSY`（intake 槽被另一次整文件 intake 占用）；`503` `INTAKE_STORAGE_UNAVAILABLE`（会话回到 `open`）；`503` `INTAKE_UNAVAILABLE`（派发失败：比赛已建好，会话已 `completed`，由解析任务的过期恢复重新派发）；`404` |
| `DELETE /uploads/sessions/{session_id}` | cookie；production 还要 `Origin` | `204`，删除会话行和暂存分片 | `404`；`409` `upload_session_not_open`（正在 `completing`） |

**令牌与 CSRF**：只有 `PUT ^/uploads/sessions/[0-9a-f]{32}/parts/[0-9]{1,5}$` 这一条精确的方法加路径免于会话 cookie 检查；路由自己核对上传令牌（数据库只存它的 sha256，用 `hmac.compare_digest` 比对）和 `Origin`，不回退到 cookie。令牌只能往这一个会话的暂存目录里写字节；退出登录不会让它失效，到会话过期为止。Caddy 的访问日志删掉 `X-Upload-Token`。

**完整性**：服务器自己算每一片的 SHA-256 并严格核对长度；带了 `X-Part-SHA256`（64 位小写十六进制）就比对。整个文件的 SHA-256 由 intake 在拼接流上计算，写进 job 元数据的 `sourceArtifact`，和旧路由一样。

**完成恰好一次**：`complete` 先非阻塞地拿 intake 槽，再把会话改为 `completing`（租约 900 秒），等本会话进行中的分片结束（最多 2 秒），把各片拼成流交给 intake，最后在额度锁里一次提交：账户围栏、上传账本、比赛和解析任务、会话改为 `completed`。客户端断开也没关系：重试的 `complete` 得到 `202` 或建好的比赛；租约过期后（API 重启时立即）下一次 `complete` 先清掉上一次尝试留下的对象再接手。

**上传账本**：只在 `complete` 的提交事务里写一行。放弃、过期、失败的会话什么都不写；删除比赛不退还。

**单进程前提**：分片池、每个会话的进行中计数和 intake 槽都在 API 进程内存里，依赖 Compose 只运行一个 uvicorn 进程（`docker-compose.prod.yml`）。

**测速日志**：每个完成的会话在 API 日志里记一行 `Upload session completed: bytes=… parts=… part_puts=… seconds=…`（不含文件名、owner 或 IP）。

```bash
# development/test: the owner comes from X-Dev-User-Id (or DEV_USER_ID).
SIZE=$(stat -c %s sample.dem)
curl -s -X POST http://localhost:8000/uploads/sessions -H "X-Dev-User-Id: owner-a" \
  -H "Content-Type: application/json" -d "{\"filename\":\"sample.dem\",\"size\":$SIZE}"
# -> {"sessionId":"<sid>","uploadToken":"<token>","partSize":8388608,"partCount":N,...}
# Part i is bytes [i*partSize, (i+1)*partSize) of the file; the last part is the rest.
dd if=sample.dem bs=8388608 skip=0 count=1 status=none | curl -s -X PUT \
  http://localhost:8000/uploads/sessions/<sid>/parts/0 -H "X-Upload-Token: <token>" \
  -H "Content-Type: application/octet-stream" --data-binary @-
curl -s http://localhost:8000/uploads/sessions/<sid> -H "X-Dev-User-Id: owner-a"      # receivedParts
curl -s -X POST http://localhost:8000/uploads/sessions/<sid>/complete -H "X-Dev-User-Id: owner-a"
curl -i -X DELETE http://localhost:8000/uploads/sessions/<sid> -H "X-Dev-User-Id: owner-a"  # abandon
```

`scripts/cloud_preview_smoke.py` 的样本上传走的就是这条路径（逐片上传，校验每片的 SHA-256，处理 `202` 和缺片）。

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

### 比分摘要（matchSummary）

Demo list items 和 `GET /demos/{demo_id}/status` 带可选的 `matchSummary`（旧数据、未完成或无法判定双方的比赛为 `null`）：

```json
{
  "teams": [
    {"key": "A", "name": "MOUZ", "startSide": "T", "score": 13},
    {"key": "B", "name": "Spirit", "startSide": "CT", "score": 11}
  ],
  "rounds": 24,
  "version": 2
}
```

队伍 = 首个有人可判定阵营的回合里同一阵营的玩家；A 队开局 T，B 队开局 CT。每名玩家每回合的阵营取该回合 `startTick`–`endTick` 之间玩家帧里的多数（平票取最早的一帧；该回合没有范围内的帧时用它的全部帧；仍没有就用该回合的击杀记录），队伍阵营由队员投票，无法判定的回合沿用最近一个已判定回合的阵营；不按回合号推断，所以半场和加时换边都算对。完整规则写在两边实现的文件头注释里，并由前后端共用的 `fixtures/match-rules/` 用例固定；比分 = 该队所在阵营获胜的回合数。`name` 是 demo 里的战队名（`team_clan_name`，匹配赛通常没有 → `null`）。解析完成时写入 `demos.match_summary`；此前已完成的比赛（没有摘要，或摘要 `version` 低于 2）由 worker 空闲时回填或重算（每 30 秒最多 3 场：比分读已存 replay，战队名在 parse 子进程里只读源 `.dem` 的几个 tick；读不到名字就只存比分；从不改 replay）。计算在 `backend/app/services/demo_service/match_summary.py`，与前端 `frontend/lib/match-stats.ts` 的定义保持一致。

### 回放响应缓存（ETag 与 304）

`GET /demos/{demo_id}/replay` 先做和以前一样的检查：比赛不存在或不属于当前 owner → `404`，还没完成 → `409`，回放不存在 → `404`。已删除的比赛和别人的比赛走不到缓存。

- 通过检查后，`200` 响应带强 `ETag`（带引号的 32 位十六进制）、`Cache-Control: private, no-store` 和 `Vary: Accept-Encoding`。浏览器不保存回放：`core/auth.py` 的 `_protect_browser_response` 给所有登录后的路径加 `private, no-store`，回放也不例外；ETag 主要给非浏览器客户端校验用，提速靠下面的服务端缓存。
- 请求的 `If-None-Match` 与当前 ETag 相同（支持 `*`、逗号分隔的多个值和 `W/` 前缀）时返回 `304`，没有 body，带同样的 `ETag`、`Cache-Control` 和 `Vary`。前端的请求一律是 `cache: "no-store"`，不发 `If-None-Match`。
- ETag 由这些算出：缓存版本（`backend/app/services/demo_service/replay_response_cache.py` 的 `PUBLIC_REPLAY_CACHE_VERSION`，公开投影、读取时补的默认值或序列化变化时提升）、`REPLAY_CONTRACT_VERSION`、地图配置的指纹（读取时会按地图配置刷新地图信息、重投影旧回放）、比赛的回放存储引用，以及公开的视频状态（回放里的视频信息，加上私有视频对象此刻是否还在：视频就绪时每次请求都向存储确认一次）。每次接受新回放（解析完成、后台回放升级、每次视频写入）都存到一个新的 `artifact://` 引用，存储不会覆盖已有的引用，同一个引用的内容不再改变；所以引用或视频状态一变 ETag 就变。
- API 进程里有一份按总字节数限定的 LRU 缓存，存最近返回的回放响应（gzip 后的字节）。命中时不读存储、不重算：客户端接受 gzip 时原样返回并带 `Content-Encoding: gzip`（`JsonGzipMiddleware` 不会再压一次），否则解压后返回。没命中时照常计算，再存进缓存。大小由 `REPLAY_RESPONSE_CACHE_MB` 控制（默认 64）。production 只跑一个 uvicorn 进程，所有请求共用这一份；它只在内存里，API 重启即清空。实测（Mirage 样例，本地 Docker）没命中时服务端每次要处理约 1.1 秒（读存储、补默认值、投影、序列化、gzip），以前刷新页面也要重来一遍；命中缓存时约 8 毫秒（仍然要传约 1.8 MB 的 gzip 回放），`304` 约 4 毫秒。
- 只缓存 `artifact://` 引用的回放。开发环境遗留的 `local://` 引用（原地改写，内容会变）和 `REPLAY_RESPONSE_CACHE_MB=0` 时，照旧每次读取、计算，不带 `ETag`。
- 视频状态备忘：缓存旁边按回放引用记着回放里的视频信息（最多 4096 条，删除比赛时一起清掉）。同一个 `artifact://` 引用的内容不会变，所以比赛库 `GET /demos` 的每一行、`GET /demos/{id}/video`、`GET /demos/{id}/render/jobs` 和每次媒体 Range 请求，对每个引用只读一次回放，之后直接用备忘（视频写入会换新引用）。`local://` 引用照旧每次读取；视频写入路径在行锁下重新读取回放，不经过备忘。它与 `REPLAY_RESPONSE_CACHE_MB` 无关，设为 `0` 时备忘照样工作。实测（进程内，sqlite + 本地存储，5 场比赛，每份回放约 23 MiB）：`list_demos` 以前每次轮询约 2.1 秒，现在第一次约 2.2 秒（每份回放读一次；API 启动时的预热已经替最近的比赛读过），之后每次约 5 毫秒。
- 预热：API 进程里一个后台线程（`backend/app/services/demo_service/replay_warmer.py`）提前把响应算好放进这份缓存，走的就是路由没命中时的同一段代码，所以存进去的字节和 `ETag` 与请求自己算出来的完全一样。触发时机：API 启动时最近完成的 `REPLAY_WARM_RECENT` 场真实、未归档的比赛（默认 10，模拟比赛不占名额，按 `completed_at` 倒序，最旧的先算，最新的最后成为最近使用）；worker 在解析完成、回放升级提交之后，把比赛 id 发到 Redis 频道 `REPLAY_READY_CHANNEL`（默认 `cs2:replay-ready`，只是一次 best-effort 的 publish，失败不影响任务），预热线程订阅这个频道；每次视频写入（手动 MP4、标定、render worker 回调、render 状态变化）提交之后，API 进程里直接排队，worker 里同样发到频道。队列去重，最多排 32 场，超出丢最早的；一次只算一场，每场用自己的短数据库会话。只预热存在、已完成、`artifact://` 引用的比赛；缓存关闭、已有当前 `ETag` 的项、刚删除的比赛都跳过。Redis 不可用时只记一行警告，按 1 秒起、最长 60 秒的退避重连，API 照常服务。`REPLAY_WARM_ENABLED=0` 关闭预热线程，worker 也不再发布。预热不改变路由的检查顺序：owner、`404`、`409` 仍然先查。实测（进程内，合成回放，gzip 后约 1.8 MB）：没预热时第一次 GET 约 755 毫秒，后台预热一场约 720 毫秒，预热后的第一次 GET 约 24 毫秒（中位数，含进程内传输）。
- 删除：`DELETE /demos/{id}` 和 `DELETE /auth/account` 提交后，API 进程立即清掉相应比赛的缓存项，刚删除的比赛 id 也不再接受写入缓存（删除前已经开始读回放的请求不会在删除后把它存进去）。运维命令 `app.cli.delete_data` 和 worker 在别的进程里删除，清不到 API 进程的内存；但路由先查数据库，比赛不在就返回 `404`，缓存里的内容不会再被返回，之后被 LRU 挤出或在 API 重启时丢弃。见 [data_deletion_v1](data_deletion_v1.md#并发与加固)。

### Replay contract（`replay_contract_v5`）

`GET /demos/{demo_id}/replay` 返回 `contractVersion`、`mapName`、`mapMetadata`（含 `transform` 与 `worldUnitsPerPercent: {x, y}`，一个雷达百分点对应的世界单位）、`tickRate`、`video`、`rounds`、`players`、`frames`（每帧每名玩家 `x/y` 雷达百分比、`z` 世界高度、`hp`、`alive`、`hasBomb`）、`events`、`diagnostics`，以及 v2 新增的 `playerStates`、`utility`，v3 新增的 `inputs`（见下方[按键记录](#按键记录inputsv3)），v4 在每颗投掷物上新增的可选 `throwOrigin`（见下方[出手站位](#出手站位throworiginv4)）和 v5 新增的 `shots`（见下方[开枪记录](#开枪记录shotsv5)）。公开响应里玩家、`bombState` 和事件的 `z` 取整到 0.1 世界单位（和道具轨迹点一样），存储的回放和分析器仍用原值；存储回放里给分析器用的 `kills`/`deaths` 列表不在公开响应里，击杀看 `type: "kill"` 的事件。v2 的两项：

```json
{
  "playerStates": {
    "76561198000000001": [
      {"tick": 1000, "money": 800, "armor": 0, "helmet": false, "defuser": false,
       "weapon": "Glock-18", "grenades": [], "equipValue": 200},
      {"tick": 1104, "money": 100, "armor": 100, "helmet": false, "defuser": false,
       "weapon": "Glock-18", "grenades": ["flash", "flash"], "equipValue": 900}
    ]
  },
  "utility": [
    {"id": "utility-smoke-633-10280", "type": "smoke",
     "throwerId": "76561198000000001", "throwerName": "torzsi", "throwerSide": "CT",
     "roundNumber": 1, "throwTick": 10280, "detonateTick": 10584, "endTick": 11996,
     "points": [{"tick": 10280, "x": 62.51, "y": 47.3, "z": 32.2}, {"tick": 10584, "x": 29.0, "y": 64.02, "z": -166.0}]}
  ]
}
```

- `playerStates`：按玩家 id（与 `frames[].players[].id` 相同）存装备/经济的**变化点**，只有字段变化才新增一条，按 tick 升序；时刻 t 的状态 = 最后一条 `tick ≤ t`。每条是完整快照；某字段缺失表示这场 demo 没有该数据，不是 0。`weapon` 是 demo 里的武器显示名（≤32 字符，死亡或空手为 `null`）；`grenades` 每颗一项，取值 `smoke`/`flash`/`he`/`molotov`（燃烧瓶与燃烧弹）/`decoy`。在帧的采样 tick 上取样，不写进每一帧。
- `utility`：每颗投掷物一条，`id` 确定（`utility-{type}-{实体id}-{throwTick}`）。`points` 与帧同一雷达百分比坐标（0–100，`z` 为世界高度），飞行中约每 4 tick 一个点，首尾必留，停在引爆处，每颗最多 120 点；最后一点即落点。`detonateTick` 取对应引爆事件（燃烧取 `inferno_startburn`），找不到时取最后移动的 tick；`endTick` 为效果结束（烟 `smokegrenade_expired`、火 `inferno_expire`，缺失时按烟 18 秒、火 7 秒；空中爆掉的燃烧瓶、闪光、手雷、诱饵弹 = `detonateTick`），且不晚于下一回合的 `startTick`（回合重置会清掉烟和火）。回合结束 10 秒以后才投出的道具（回合之间的暂停、重开）不属于任何回合，不收录。这两条在解析时和每次读取回放时都会执行，早先存下的回放读出来也一样。`throwerSide` 按比分摘要同一条阵营规则取该回合的阵营。
- 抽取失败（例如 demo 没有投掷物数据）只让对应字段为空，不算解析失败；v2 回放缺数据时 `diagnostics.degradedFields` 含 `utility` / `playerStates`，`diagnostics.utilityCount`、`diagnostics.playerStateCount` 给出条数。
- 旧回放照常加载：v1 读出 `playerStates: {}`、`utility: []`，v1 和 v2 都读出 `inputs: {}`，v1–v3 的投掷物没有 `throwOrigin`，v1–v4 读出 `shots: {}`，前端隐藏依赖它们的部分；worker 空闲时在后台把回放早于当前契约版本的已完成比赛重新解析成 v5（状态保持 `completed`；这一步不重新分析，建议和评价原样保留，建议之后由后台的建议重算按 `COACHING_RULES_VERSION` 单独更新，见下方“建议事件的结构化上下文”）。v3 只加了按键记录、v4 只加了出手站位，规则分析都没有变，所以这两次升级不需要重算建议。v5 的开枪记录供两条射击规则使用，规则版本同时升到 `coaching_rules_v3`：建议重算要等回放升级完成才运行，所以旧比赛先重新解析出 `shots`，再由重算用新回放算出射击类建议。
- 数据都来自上传的 `.dem`，与位置数据同属一类，随比赛一起删除。v2 时 Mirage 样例从 23.2 MB 增至 25.6 MB（+10%）；v3 的按键记录再加约 1.1 MB（增至 26.7 MB，+4%）；v4 的出手站位再加约 55 KB（+0.2%）；v5 的开枪记录再加 46–56 KB（四场样例，+0.2%；gzip 后 +10–12 KB）。

#### 按键记录（`inputs`，v3）

```json
{
  "inputs": {
    "76561198998266210": [
      [77752, 1032], [77805, 1024], [77831, 1536], [77832, 512], [77837, 513],
      [77841, 1537], [77842, 1536], [77849, 1029], [77855, 1541], [77857, 1540],
      [77858, 1028], [77866, 1029], [77870, 1028], [77872, 1024], [77874, 0]
    ]
  }
}
```

（节选：Mirage 样例里 xelex 第 10 回合阵亡前不到两秒，从 77752 按住 W+D 到 77874 阵亡。）

- 按玩家 id（与 `frames[].players[].id` 相同）存按键状态的**变化点**：每项是 `[tick, mask]` 两个整数，按 tick 升序，相邻两项的 `mask` 不同；时刻 t 的按键 = 最后一条 `tick ≤ t` 的 `mask`，第一条之前没有数据。第一条在该玩家第一回合里的第一个 tick，最后一回合结束后不再记录。阵亡后到下一回合开始前 `mask` 一直是 0（死后的指令操控的是观战视角），所以阵亡时还按着键会出现 `[死亡 tick, 0]`。逐 tick 记录，不按帧采样，一两 tick 的短按（急停、点射）也保留；整场 Mirage 样例每名玩家约 8,400 个变化点。
- `mask` 来自 demo 的 `usercmd_buttonstate_1`（Source 2 的按键位掩码，直接解码原始位，不用 demoparser2 自带的按键名），只保留界面显示的 9 个位：

  | 位 | 十进制 | 含义 |
  | --- | --- | --- |
  | `ATTACK` | 1 | 开火（左键） |
  | `JUMP` | 2 | 跳（空格） |
  | `DUCK` | 4 | 蹲（Ctrl） |
  | `FORWARD` | 8 | 前（W） |
  | `BACK` | 16 | 后（S） |
  | `MOVELEFT` | 512 | 左（A） |
  | `MOVERIGHT` | 1024 | 右（D） |
  | `ATTACK2` | 2048 | 右键 |
  | `SPEED` | 65536（`0x10000`） | 静步（Shift） |

  其他位（使用 32、换弹 8192 等）在解析时去掉，不存储。
- **每场 demo 不一定有**：只在 BLAST.tv 的 GOTV demo 上验证过；匹配、FACEIT、第一人称 POV 等来源可能没有 usercmd 数据。没有数据或抽取失败时 `inputs: {}`，不算解析失败；`diagnostics.inputSource` 有数据时为 `"usercmd"`，否则为 `null`，`diagnostics.inputPlayerCount` 是有按键记录的玩家数。前端的按键面板在当前玩家没有记录时隐藏。
- 存储前的归一化：只收整数、按 tick 排序、合并相邻的相同 `mask`、去掉不显示的位，坏的条目丢弃；每名玩家最多 60,000 条、最多 64 名玩家，达到上限的记录会截断，并在 `diagnostics.degradedFields` 里标出 `inputsCapped`；存下的 `inputs` 不是对象时读出 `{}`，`degradedFields` 含 `inputs`。v3 回放没有按键数据不算降级，只表现为 `inputSource: null`。

#### 出手站位（`throwOrigin`，v4）

```json
{
  "id": "utility-smoke-431-77300", "type": "smoke",
  "throwerId": "76561198193174134", "throwerName": "xertioN", "throwerSide": "T",
  "roundNumber": 10, "throwTick": 77300, "detonateTick": 77636, "endTick": 78718,
  "points": [{"tick": 77300, "x": 92.85, "y": 39.06, "z": -53.0}, {"tick": 77636, "x": 43.96, "y": 49.35, "z": 50.0}],
  "throwOrigin": {"x": 1377.26, "y": -115.65, "z": -132.61, "pitch": -20.33, "yaw": 163.28,
                  "speed": 245.0, "airborne": true}
}
```

（节选：Mirage 样例里 xertioN 第 10 回合的烟，边跑边跳着扔出，`points` 共 85 点，这里只列首尾。）

- `utility[]` 里每颗投掷物可以带 `throwOrigin`：投掷者出手时的站位，供前端"道具投掷分析"生成复制站位指令（`setpos x y z; setang pitch yaw 0`）。取投掷者在 `throwTick - 1`（投掷物出现前的最后一个 tick）那一行的数据，这一行没有时取 `throwTick` 那一行；投掷者按 SteamID 匹配（与 `throwerId` 相同）。
- `x/y/z` 是脚下的位置，`pitch/yaw` 是视角，都是**世界单位 / 度**，不是雷达百分比，保留 2 位小数；`pitch` 限制在 −90..90，`yaw` 归到 (−180, 180]。旧坐标变换的重投影（`legacyRadarEdgePositions`）不动它。
- `speed`（可选）：出手时的水平速度（单位/秒，1 位小数），用 `throwTick - 1` 和 `throwTick` 两行的水平位移乘 tick 率算出，两行都有时才有。没有用 demoparser2 的 `velocity_X/Y`：它们比位置晚一个 tick，而且只有前两个 tick 也一起解析时才有值。
- `airborne`（可选）：出手时是否在空中（demo 的 `is_airborne`）。滚轮跳不进 `usercmd_buttonstate_1`，所以跳投要看这一项，按键记录里不一定有跳。
- 解析时只多一次 `parse_ticks`（每颗投掷物两个 tick；Mirage 样例 508 颗、约 0.2 秒，解析的峰值内存不变）。读不到这些属性时这颗投掷物就没有 `throwOrigin`，不算解析失败；`diagnostics.throwOriginCount` 是带 `throwOrigin` 的投掷物数，为 0 不算降级（`degradedFields` 不标）。归一化时 `x/y/z/pitch/yaw` 有一个不是有限数字就整项丢掉，`speed` 不是有限的非负数、`airborne` 不是布尔值时只丢那一项。前端没有 `throwOrigin` 时不显示站位指令。

#### 开枪记录（`shots`，v5）

```json
{
  "shots": {
    "76561198998266210": [
      [10806, 226, 1, "glock"], [10816, 230, 1, "glock"], [10825, 232, 1, "glock"],
      [10835, 164, 0, "glock"], [10998, 49, 0, "glock"]
    ]
  }
}
```

（Mirage 样例里 xelex 的前 5 枪：第一回合手枪局的格洛克，前三枪是在空中开的。格洛克不在射击规则判定的枪里。四场样例每场 2,023–2,341 枪。）

- 按玩家 id（与 `frames[].players[].id` 相同：十进制 SteamID64，没有时用昵称）存每一枪，每项是 `[tick, speed, flags, weapon]`，按 tick 升序。热身和回合之间的枪也收录，规则只看回合进行中的枪：
  - `tick`：开枪的 tick（整数）。
  - `speed`：开枪那一刻的水平移动速度，单位/秒，取整，限制在 0–1000。取 `weapon_fire` 事件附带的玩家属性 `velocity_X/Y`，`speed = round(hypot(vx, vy))`。
  - `flags`：位掩码，`1` = 开枪时在空中（`is_airborne`）；其余位暂不使用，为 0。读不到 `is_airborne` 时退回只读速度，这时所有枪的 `flags` 都是 0。
  - `weapon`：武器键名，即 demo 里的武器名去掉 `weapon_` 前缀（`[a-z0-9_]{1,32}`，例如 `ak47`、`m4a1_silencer`、`deagle`）。它不是显示名，`playerStates` 里的 `weapon` 才是显示名。
- 只收枪械：刀（`knife*`、`bayonet`）、投掷物（`smokegrenade`、`flashbang`、`hegrenade`、`molotov`、`incgrenade`、`decoy`）、`c4` 和电击枪 `taser` 不收。速度不是有限数字的行丢掉（四场样例每场约 2 枪）。
- 与 `throwOrigin.speed` 的区别：`throwOrigin` 按 tick 解析，那里的 `velocity_X/Y` 比位置晚一个 tick，而且只有前两个 tick 也一起解析时才有值，所以改用两行位置计算；`shots` 用的是事件解析时附带的 `velocity_X/Y`，四场样例里几乎每一枪都有值。
- 解析时只多一次 `parse_event("weapon_fire", …)`（四场样例每场多 0.7–1.8 秒）。抽取失败或一枪都没有时 `shots: {}`，不算解析失败；`diagnostics.shotSource` 有数据时为 `"weapon_fire"`，否则为 `null`，`diagnostics.shotCount` 是收录的总枪数。
- 存储前的归一化：不是四项、`tick`/`speed`/`flags` 不是整数、`tick` 或 `flags` 为负、`weapon` 不是枪械键名的条目丢掉；`speed` 限制在 0–1000，`flags` 只留已知位；按 tick 排序，完全相同的条目去重；每名玩家最多 20,000 条、最多 64 名玩家，有玩家达到上限时截断，并在 `diagnostics.degradedFields` 里标出 `shotsCapped`。存下的 `shots` 不是对象时读出 `{}`，`degradedFields` 含 `shots`；v5 回放没有开枪记录不算降级，只表现为 `shotSource: null`。
- 只给两条射击规则（`moving_shots`、`no_counter_strafe`，见下方“建议事件的结构化上下文”）用，复盘界面暂不直接显示。数据来自上传的 `.dem`，随比赛一起删除。

### 建议事件的结构化上下文（`structured_context_json`）

`GET /demos/{demo_id}/coaching` 的每条事件带 `structured_context_json`：通用字段（`ruleId`、`involvedPlayerIds`、`evidenceTicks`、`targetPlayerId`、`action`、`limitation`，用到解析事件时还有 `relatedEventIds`）加上各规则自己的依据。规则版本 `coaching_rules_v2`（`backend/app/analysis/version.py` 的 `COACHING_RULES_VERSION`）新增了下列字段。它们都是**可选**的：旧版规则生成、还没重算的事件没有这些字段，数据不足时也会省略（不猜），读取方按缺失处理。

| 字段 | 出现在 | 含义 |
| --- | --- | --- |
| `extraReasons` | 阵亡卡片 | 同一次阵亡的附加原因，见下文。不参与事件 id 的生成 |
| `impact` | 阵亡卡片 | 这次阵亡对回合的影响，见下文 |
| `weapon` | 阵亡卡片 | 击杀用的武器，原样取回放里该击杀事件存的字符串 |
| `attackerName` | 阵亡卡片 | 击杀者昵称（`untraded_death` 原本就有；`isolated_entry` 卡片现在也带） |
| `durationSeconds` | `poor_spacing`（`spacingType: "stacked"`） | 两人站位过近持续的秒数 |
| `stackedMultikill` | `poor_spacing`（`spacingType: "stacked"`） | `true`：没有持续满 3 秒，但这两人在 3 秒内被同一名敌人先后击杀，因此触发；同时带 `multikillAttackerId`（那名敌人的 id），有名字时还有 `multikillAttackerName`。已经持续满 3 秒的卡片不带这三个字段 |
| `tBuyKind` | `weak_utility_before_execute` | T 方这一回合的经济类型（`pistol` / `full` / `force` / `half` / `eco`），与回合条的经济类型是同一判定（`backend/app/analysis/round_economy.py` 移植自 `frontend/lib/round-economy.ts`）；判定不出来时省略该字段。T 方整回合一个道具都没用时，这条规则现在也会报，只有 ECO 回合不报 |

**阵亡卡片**：每次阵亡最多一张卡。有 `untraded_death` 时它就是这张卡；没有时，`isolated_entry` 是这张卡。同一次阵亡按击杀事件的 id（`relatedEventIds`）对应。

`extraReasons` 每项都带 `ruleId` 和 `tick`：

```json
[
  {"ruleId": "poor_spacing", "spacingType": "too_far", "distance": 1430, "durationSeconds": 4.5, "tick": 51200},
  {"ruleId": "isolated_entry", "distance": 1120, "tick": 51840}
]
```

- `poor_spacing` / `too_far`：阵亡者有一段站位过远，并且这段和阵亡前 5 秒有重叠。站位过远指：冻结时间结束 15 秒之后的采样里，他是本方离队友最远的人，离最近的存活队友不少于 `poor_spacing_max_distance`，连续至少 3 秒；T、CT 都算。`distance` 是这段开始时他与最近存活队友的距离（世界单位，取整），`durationSeconds` 是这段的长度，`tick` 是这段的开始。站位过远不再单独出卡片，只作为阵亡卡片的附加原因。
- `isolated_entry`：同一次阵亡同时满足孤立进场时，它并入 `untraded_death` 卡片，不再单独出卡片。

`impact`：

```json
{
  "roundLost": true,
  "firstDeath": true,
  "aliveBefore": {"own": 4, "enemy": 4},
  "aliveAfter": {"own": 3, "enemy": 4},
  "manDisadvantage": true
}
```

- `roundLost`：阵亡者一方输掉了这一回合；回合胜方未知时为 `null`。回合没有 `winnerReason`（例如 demo 在这一回合结束前截断）时视为胜方未知，即使回放里的 `winnerSide` 被补成了 `CT`。
- `firstDeath`：这一回合的第一个阵亡。
- `aliveBefore`：阵亡 tick 之前最后一帧里双方的存活人数（`own` 是阵亡者一方）；`aliveAfter` 为 `own` 减一、`enemy` 不变。
- `manDisadvantage`：阵亡前人数不少于对方，阵亡后少于对方。

旧比赛在重算之前，或重算失败、尝试次数用完的比赛，仍可能有已不再生成的事件：`late_post_plant_utility`、单独的 `poor_spacing`（`spacingType: "too_far"`）等，前端照常显示。

**射击规则（`coaching_rules_v3`）**：规则版本 `coaching_rules_v3` 新增 `moving_shots`（移动射击）和 `no_counter_strafe`（第一枪没急停），类别 `mechanics`、严重程度 `low`、`evidenceSource: "recorded_shots"`，判定依据是回放 v5 的 `shots` 和 `damage` 事件（缺任何一样都不出这两类建议）。规则定义和阈值见 [coaching_feedback_v1](coaching_feedback_v1.md#v3两条射击规则)。`evidenceTicks` 是这一串里移动中开枪的 tick；`involvedPlayerIds` 是开枪者，已知时加上被打中的人和击杀者。卡片从第一枪前 0.5 秒（不早于冻结时间结束）到最后一枪后 0.25 秒。每名玩家整场最多 3 张 `no_counter_strafe`（阵亡的优先，其次第一枪更快的）。上下文字段：

| 字段 | 含义 |
| --- | --- |
| `weapon` | 武器键名，与 `shots` 里的相同（如 `ak47`）。注意阵亡卡片的 `weapon` 是击杀事件里存的字符串，两者不是同一种取值 |
| `weaponLabel` | 武器显示名：AK-47、M4A4、M4A1-S、Galil AR、FAMAS、AUG、SG 553、AWP、SSG 08、SCAR-20、G3SG1、Desert Eagle、R8 Revolver |
| `accurateSpeed` | 这把枪的稳定线（单位/秒）：最大移动速度的 34 %，取整 |
| `speed` | `no_counter_strafe`：第一枪的速度；`moving_shots`：移动中各枪里的最高速度 |
| `shotCount` / `movingShotCount` | 这一串的枪数 / 其中移动中开的枪数 |
| `airborne` | `no_counter_strafe`：第一枪是否在空中；`moving_shots`：移动中的枪里有没有在空中开的 |
| `hit` | 这一串里有没有一枪打中人（开枪后 2 tick 内有开枪者用枪造成的 `damage`） |
| `died` | 最后一枪之后 2 秒内阵亡；有击杀者名字时另带 `attackerName` |
| `side` | 开枪者这一回合的阵营（`T` / `CT`；判定不出时省略） |
| `keysAtShot` | 第一枪时按着的移动键，按 W A S D 的顺序，例如 `["A"]`，可能为空 |
| `counterStrafe` | 第一枪前 0.15 秒内有没有反向急停：这段时间里按下某个移动键时，它的反方向键在这段时间里按过。`keysAtShot` 和 `counterStrafe` 只在按键记录覆盖第一枪时才有，否则两项都省略 |
| `occurrencesInRound` | 这一回合里这名玩家符合这两条规则之一的串数（每名玩家每回合最多一张卡） |

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
