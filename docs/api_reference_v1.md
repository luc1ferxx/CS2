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
- `GET /uploads/quota`（当前 owner 的上传额度，每日已用次数来自上传账本，删除比赛不退还：`{dailyLimit, dailyUsed, dailyResetSeconds, activeLimit, activeCount, maxUploadBytes}`；development/test 下各 limit 为 `null`；`Cache-Control: private, no-store`；仅供提示，上传时仍以 `POST /uploads/demo` 的检查为准，不报告全站 `PARSE_QUEUE_GLOBAL_LIMIT`）
- `POST /uploads/mock`（仅 development/test；production 返回 `404`）
- `POST /uploads/demo`（production 受上传额度限制：`503` `parse_queue_full`、`429` `active_parse_limit` / `upload_daily_limit`、`503` `upload_quota_unavailable`）

### Replay and coaching

- `GET /demos/{demo_id}/replay`（紧凑 JSON；客户端发送 `Accept-Encoding: gzip` 时 gzip 压缩。JSON 响应 ≥1 KB 时都按此压缩，媒体、Range 与流式响应不压缩）
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

### Replay contract（`replay_contract_v4`）

`GET /demos/{demo_id}/replay` 返回 `contractVersion`、`mapName`、`mapMetadata`（含 `transform` 与 `worldUnitsPerPercent: {x, y}`，一个雷达百分点对应的世界单位）、`tickRate`、`video`、`rounds`、`players`、`frames`（每帧每名玩家 `x/y` 雷达百分比、`z` 世界高度、`hp`、`alive`、`hasBomb`）、`events`、`diagnostics`，以及 v2 新增的 `playerStates`、`utility`，v3 新增的 `inputs`（见下方[按键记录](#按键记录inputsv3)）和 v4 在每颗投掷物上新增的可选 `throwOrigin`（见下方[出手站位](#出手站位throworiginv4)）。v2 的两项：

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
- 旧回放照常加载：v1 读出 `playerStates: {}`、`utility: []`，v1 和 v2 都读出 `inputs: {}`，v1–v3 的投掷物没有 `throwOrigin`，前端隐藏依赖它们的部分；worker 空闲时在后台把回放早于当前契约版本的已完成比赛重新解析成 v4（状态保持 `completed`；这一步不重新分析，建议和评价原样保留，建议之后由后台的建议重算按 `COACHING_RULES_VERSION` 单独更新，见下方“建议事件的结构化上下文”）。v3 只加了按键记录、v4 只加了出手站位，规则分析都没有变，所以这两次升级不需要重算建议。
- 数据都来自上传的 `.dem`，与位置数据同属一类，随比赛一起删除。v2 时 Mirage 样例从 23.2 MB 增至 25.6 MB（+10%）；v3 的按键记录再加约 1.1 MB（增至 26.7 MB，+4%）；v4 的出手站位再加约 55 KB（+0.2%）。

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
