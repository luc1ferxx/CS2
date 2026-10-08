# Configuration Reference V1

本文件是环境变量的完整清单。README 只保留跑起来所需的最小配置，需要逐项确认时看这里。

相关合同文档：

- Steam/account：`steam_auth_accounts_v1.md`
- 比赛授权、加密、同步状态与删除策略：`steam_match_sync_v1.md`
- Demo Provider、下载和 intake 边界：`steam_demo_import_v1.md`
- 共享 owner/private media 合同：`production_auth_owner_private_media_v1.md`
- Runtime deployment 说明：`deployment_readiness_v1.md`
- Cloud Preview runbook：`cloud_preview_deploy_v1.md`

## Production fail-closed 要求

Production 必须显式提供下列各项，否则 startup validation 失败关闭，不会降级启动：

- 一个受支持的 identity provider（`AUTH_PROVIDER=steam`，或显式选择的兼容 `oidc`）
- 完整的 HTTPS / `__Host-` cookie / single-origin / CORS 合同
- 真实的 server-only Steam Web API key
- `AUTH_PROVIDER=steam` 时显式的 `STEAM_LOGIN_ALLOWLIST`（受邀 Steam ID64 列表，或 `*` 明确开放给所有 Steam 账号）
- 随机的非开发用 Steam credential encryption key
- `STEAM_DEMO_PROVIDER=disabled`，且 experimental CDN 开关为关闭
- 非默认的 render-worker credential
- private S3-compatible artifact 配置（`ARTIFACT_STORAGE_BACKEND=s3`）

Steam realm/callback 由 `BACKEND_PUBLIC_URL` 推导；OIDC 字段只在 `AUTH_PROVIDER=oidc` 时必需。

Demo source/download 配置只属于 API。parser/render queue worker 校验的是更窄的 auth/storage 合同，永远不接收 Steam/OIDC browser secrets、match-history credentials、source-provider 配置或 provider secrets。

Production credentials 绝不能进入 `NEXT_PUBLIC_*`、源码、日志、签入的 env 文件或浏览器响应。

## Frontend

| Name | Default | Used by |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | frontend browser API/media URL |
| `NEXT_PUBLIC_AUTH_PROVIDER` | `steam` | public frontend login label/path; must match server `AUTH_PROVIDER` |
| `NEXT_PUBLIC_BETA_CONTACT_URL` | unset | optional `mailto:` or form URL shown as "申请内测资格" on the not-invited sign-in page; inlined at frontend build time (preview passes it as a build arg) |
| `NEXT_PUBLIC_PRIVACY_CONTACT` | unset | contact shown on the public `/privacy` page for privacy questions and removal requests: an email becomes a `mailto:` link, an `http(s)://` URL a link, anything else plain text; required for the VPS production deploy (`scripts/deploy/deploy.sh` refuses to run without it); unset (local/preview builds) the page says there is no public contact; inlined at frontend build time (preview passes it as a build arg) |
| `NEXT_PUBLIC_DATA_REGION` | unset | optional server region shown on `/privacy` (e.g. `日本东京`); unset shows "海外 VPS，具体地区由站长部署时选定"; inlined at frontend build time (preview passes it as a build arg) |

## 身份、会话与 owner 边界

| Name | Default | Used by |
| --- | --- | --- |
| `AUTH_MODE` | unset (required) | backend identity mode: `development`, `test`, or `production` |
| `AUTH_PROVIDER` | unset (required in production) | production browser identity provider: `steam` or compatibility `oidc`; local Compose sets `steam` |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | server-trusted frontend redirect origin; must equal the HTTPS API origin in production |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | backend public origin; same exact origin as frontend in production |
| `AUTH_COOKIE_SECURE` | `false` | must be enabled in production |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | opaque session cookie name |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | 会话的空闲窗口（Redis 记录和浏览器 cookie 的 `Max-Age`）：production 下请求解析到有效会话、且剩余不到一半时，`SessionCsrfMiddleware` 把它续回这个长度（只改 `expiresAt`，`issuedAt` 不变，撤销逻辑照旧），并重发同属性的 cookie；不超过 `AUTH_SESSION_MAX_AGE_SECONDS`。分片上传的 part `PUT` 和 `/render-worker/` 不续期。production 要求 1–86400 |
| `AUTH_SESSION_MAX_AGE_SECONDS` | `86400` | 会话从登录起的绝对上限：续期永远不越过它，到点必须重新登录。production 要求不小于 `AUTH_SESSION_TTL_SECONDS`、不大于 86400（账户删除写的撤销标记只保留 86400 s 加时钟偏差） |
| `AUTH_LOGIN_TTL_SECONDS` | `300` | bounded one-time login attempt lifetime |
| `AUTH_CLOCK_SKEW_SECONDS` | `30` | bounded identity timestamp leeway |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | backend API; production requires only the exact `FRONTEND_PUBLIC_URL` origin |
| `DEV_USER_ID` | `dev-user` | development/test owner harness only |

### Steam OpenID

| Name | Default | Used by |
| --- | --- | --- |
| `STEAM_AUTH_STATE_COOKIE_NAME` | `__Host-cs2_steam_state` | short-lived Steam state cookie name |
| `STEAM_OPENID_NONCE_TTL_SECONDS` | `600` | Steam assertion freshness/replay reservation window |
| `STEAM_WEB_API_KEY` | unset | server-only GetPlayerSummaries + match-history publisher key; required in production, never frontend/worker-visible |
| `STEAM_LOGIN_ALLOWLIST` | unset | API-only invite gate: comma-separated individual Steam ID64s (at most 1000, unique), or `*` for every Steam account. Required when `AUTH_MODE=production` and `AUTH_PROVIDER=steam`; with `AUTH_PROVIDER=oidc` only empty or `*` is accepted. An uninvited callback redirects to `/auth/callback?error=not_invited` before any account is created, and removing an ID ends that user's live sessions (sessions without a recorded Steam ID fail closed and sign in again). To change it, redeploy the API container so it re-reads the environment (`docker compose -f docker-compose.yml -f docker-compose.preview.yml up -d`); a plain `docker compose restart` keeps the old value. Development/test validate the format but never enforce it |

### OIDC 兼容路径

只在 `AUTH_PROVIDER=oidc` 时需要。

| Name | Default | Used by |
| --- | --- | --- |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID` | unset | required only when `AUTH_PROVIDER=oidc` |
| `OIDC_AUTHORIZATION_ENDPOINT`, `OIDC_TOKEN_ENDPOINT`, `OIDC_JWKS_URL`, `OIDC_REDIRECT_URI` | unset | legacy-compatible OIDC endpoints, required only when selected |
| `OIDC_CLIENT_SECRET`, `OIDC_ALLOWED_ALGORITHMS`, `AUTH_STATE_COOKIE_NAME` | unset / existing defaults | server-only OIDC compatibility configuration |

## Steam 比赛同步

| Name | Default | Used by |
| --- | --- | --- |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | checked-in development key (non-production) | URL-safe base64 of 32 random bytes; never unset, because `config.py` falls back to `DEVELOPMENT_STEAM_CREDENTIAL_ENCRYPTION_KEY` and validates the value on every startup. Production startup fails closed while that development key is still in place. Never logged or returned |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION` | `dev-v1` | bounded active AES-GCM key version stored with ciphertext; production should set its own version |
| `STEAM_SYNC_MAX_MATCHES` | `20` | hard maximum new sharing codes per Sync-now request |
| `STEAM_SYNC_TIMEOUT_SECONDS` | `5` | bounded Valve request timeout |
| `STEAM_SYNC_RETRY_BASE_SECONDS` | `30` | first persisted transient backoff |
| `STEAM_SYNC_RETRY_MAX_SECONDS` | `3600` | maximum persisted transient backoff |
| `STEAM_SCHEDULED_SYNC_ENABLED` | `false` | independent fail-closed gate; V1 has no scheduler and rejects true |

## Steam Demo 导入（本 build 默认且仅允许 disabled）

下列变量为未来获正式许可的 Provider 预留。本 build 的 provider 选择器拒绝 `disabled` 以外的任何值，这些边界值不会被实际使用。

| Name | Default | Used by |
| --- | --- | --- |
| `STEAM_DEMO_PROVIDER` | `disabled` | API-only Demo source selector; this build rejects every value except `disabled` |
| `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED` | `false` | unsupported experiment guard; `true` always fails startup |
| `STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS` | unset | reserved exact lowercase DNS allowlist for a separately reviewed licensed provider |
| `STEAM_DEMO_DOWNLOAD_MAX_BYTES` | `536870912` | bounded raw `.dem` transfer; hard maximum 1 GiB |
| `STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS` | `3` | independently revalidated redirect-hop limit |
| `STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS` | `5`, `10`, `60` | bounded future licensed-provider transfer timeouts |
| `STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY`, `STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY` | `4`, `1` | Redis-coordinated API download budgets |
| `STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS` | `120` | expiring concurrency lease; must cover total timeout |

## 数据库与队列

| Name | Default | Used by |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach` | API, worker |
| `REDIS_URL` | `redis://localhost:6379/0` | API, worker |
| `REDIS_QUEUE_NAME` | `cs2-demo-jobs` | API, worker |
| `REPLAY_READY_CHANNEL` | `cs2:replay-ready` | API, worker; Redis pub/sub 频道：worker 在解析完成、回放升级、视频写入提交后发布比赛 id，API 的回放缓存预热线程订阅它（见 `REPLAY_WARM_ENABLED`）。两边必须一致；只传比赛 id |

Docker Compose 在容器内使用 service 名（`postgres`、`redis`），面向浏览器则使用 host URL（`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`）。本地 Compose 必须显式使用 `AUTH_MODE=development`，可以使用 local adapter 以及明确标注为非生产的 Steam 加密 key。

### 解析任务的超时、租约与回收

| Name | Default | Used by |
| --- | --- | --- |
| `PARSE_TIMEOUT_SECONDS` | `1200` | worker; 单个解析子进程的挂钟上限，超时按 `PARSE_TIMED_OUT` 落 failed |
| `PARSE_MEMORY_LIMIT_BYTES` | `4294967296` | worker; 子进程 `RLIMIT_DATA` 上限（仅 POSIX 生效）；`0` 表示不限，其余取值不得低于 2 GiB |
| `PARSE_LEASE_TTL_SECONDS` | `60` | worker; 租约 TTL，即 worker 猝死后在途消息被其它 worker 回收前的最长等待 |
| `PARSE_LEASE_RENEW_SECONDS` | `15` | worker; 解析期间续租与写心跳的间隔，必须小于 TTL |
| `PARSE_RECLAIM_AFTER_SECONDS` | `1800` | worker; DB 对账回收卡住的 `processing` 行的年龄阈值，必须大于 `PARSE_TIMEOUT_SECONDS` |
| `PARSE_REDISPATCH_AFTER_SECONDS` | `300` | worker; DB 对账重投「行是 `queued` 但队列里没有消息」的年龄阈值 |
| `PARSE_MAX_ATTEMPTS` | `3` | worker; 回收次数达到上限后落 `failed` + `PARSE_ABANDONED`，用户可重试 |

这几个值互相牵制，`validate_worker_runtime_configuration()` 在启动时强制校验，配错直接 fail closed 而不是等到解析时才暴露：续租间隔必须短于租约 TTL；回收阈值必须**大于**解析超时，否则一次合法的长解析会在跑到一半时被对账判死、同一个 demo 被解析两遍。内存上限用 `RLIMIT_DATA` 而不是 `RLIMIT_AS`——解析一个 386 MB 的 demo 常驻内存峰值约 0.95 GiB，保留地址空间却高达 10.2 GiB（Rust 分配器预留的 arena），拿地址空间当尺子会让子进程在 `import` 阶段就被打死。同理，低于 2 GiB 的上限连解释器和原生依赖都装不下，所以被启动校验拒绝。

### 已完成比赛的后台补算

worker 空闲时（队列里有任务就让出）依次跑这两项，每项每轮最多处理一场比赛。状态记在这场比赛最新解析任务的元数据里；两项都不改比赛的 `status`、`completed_at`、`updated_at`，也不写上传账本。

| Name | Default | Used by |
| --- | --- | --- |
| `REPLAY_UPGRADE_ENABLED` | `true` | worker; 回放早于当前 `REPLAY_CONTRACT_VERSION`、存有源 `.dem` 的已完成真实解析比赛，从存储的 `.dem` 重新解析一次（`app/workers/replay_upgrade.py`）；只换回放，不重新分析建议 |
| `REPLAY_UPGRADE_MAX_ATTEMPTS` | `3` | worker; 回放升级的尝试上限，用完后这场比赛保留旧回放 |
| `REPLAY_UPGRADE_RETRY_SECONDS` | `900` | worker; 回放升级失败后的重试间隔基数，每失败一次翻倍 |
| `COACHING_RECOMPUTE_ENABLED` | `true` | worker; 建议早于当前 `COACHING_RULES_VERSION`（`backend/app/analysis/version.py`）的已完成真实解析比赛（含 Steam 导入，不含示例比赛），用已存的回放重新跑规则分析、替换建议（`app/workers/coaching_recompute.py`）；回放升级还没完成（且没用完次数）的比赛先等升级 |
| `COACHING_RECOMPUTE_MAX_ATTEMPTS` | `3` | worker; 建议重算的尝试上限（进程中途退出、没跑完的那次也算），用完后这场比赛保留旧建议；上限按规则版本计，提升 `COACHING_RULES_VERSION` 后重新计数 |
| `COACHING_RECOMPUTE_RETRY_SECONDS` | `900` | worker; 建议重算失败后的重试间隔基数，每失败一次翻倍 |

## Artifact storage 与上传上限

| Name | Default | Used by |
| --- | --- | --- |
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
| `UPLOAD_CHUNK_BYTES` | `1048576` | bounded upload/read chunk size（intake 固定的 1 MiB 读缓冲，与下面分片上传的 part 无关） |
| `UPLOAD_STAGING_ROOT` | `/data/upload-staging` | API, worker; 分片上传会话暂存分片的本地目录（`backend/app/services/storage/staging.py`），与 `ARTIFACT_STORAGE_BACKEND` 无关，production 下也在 API 主机的本地磁盘上。Compose 把命名卷 `upload-staging` 同时挂进 api 和 worker（worker 的每小时维护要清扫它）。production 必须是绝对路径 |
| `UPLOAD_PART_BYTES` | `8388608` | API; 分片大小（每次 `PUT` 一片）。`4 MiB`..`32 MiB`，必须是 1 MiB 的整数倍；分片 `PUT` 的请求体上限是它加 64 KiB。Caddy 对分片路径另设 `34MB` 的边缘上限 |
| `UPLOAD_MAX_PARALLEL_PARTS` | `4` | API; 每个会话同时进行的分片 `PUT` 上限（`1`..`6`），超出返回 `503` `INTAKE_BUSY`；同时作为 `maxParallelParts` 告诉浏览器 |
| `UPLOAD_PART_POOL` | `8` | API; 全站同时读取的分片数（`UPLOAD_MAX_PARALLEL_PARTS`..`32`，且 `UPLOAD_PART_POOL × UPLOAD_PART_BYTES` ≤ 256 MiB，这是分片占用的内存上限），满了在读 body 之前返回 `503` `INTAKE_BUSY`（`Retry-After: 2`）。和整文件 intake 的单个上传槽相互独立 |
| `UPLOAD_SESSION_TTL_SECONDS` | `86400` | API; 会话从创建算起的硬过期时间（`3600`..`604800`，不续期）。过期后分片 `PUT` 和 `complete` 返回 `404`，清扫删除行和暂存分片。`/privacy` 写明未完成的上传最多保留 24 小时：调大之前先改隐私页 |
| `UPLOAD_SESSION_GLOBAL_LIMIT` | `6` | API; 全站同时打开（`open` / `completing`）的会话上限（`1`..`64`），满了建会话返回 `503` `upload_capacity_busy`。暂存盘最多约占这个数 × `MAX_DEMO_UPLOAD_BYTES` |
| `UPLOAD_STAGING_MIN_FREE_BYTES` | `5368709120` | API; 建会话时暂存盘至少要剩 `size` + 这么多字节（`0`..`1 TiB`），否则返回 `503` `upload_storage_full`。Postgres 和 Redis 通常在同一块盘上，这个余量就是留给它们的。开发模式同样生效：Docker Desktop 的虚拟磁盘剩余不到 5 GiB 时，本地上传也会得到 `upload_storage_full`，可以在本地 `.env` 里调小 |
| `REPLAY_RESPONSE_CACHE_MB` | `64` | API; 进程内存里缓存 `GET /demos/{demo_id}/replay` 算好的 gzip 响应（按总字节数 LRU），并给响应加 `ETag`，`If-None-Match` 命中时返回 `304`；`0` 关闭缓存、`ETag` 和 `304`。旁边按条数限定（4096）的视频状态备忘不受它影响：比赛库、`/video`、`/render/jobs` 和媒体 Range 请求读 `artifact://` 回放的视频信息时，每个引用只读一次回放。目前的 Compose 文件没有把它转发进 `api` 容器，容器里用默认值 |
| `REPLAY_WARM_ENABLED` | `true` | API, worker; API 进程里一个后台线程提前把回放响应算好放进上面的缓存（`backend/app/services/demo_service/replay_warmer.py`）：解析完成、回放升级、视频写入之后，以及 API 启动时最近完成的几场，第一次打开就直接命中缓存。`0` 时 API 不启动预热线程，worker 也不再发布通知；`REPLAY_RESPONSE_CACHE_MB=0` 时同样不预热。Compose 没有转发，容器里用默认值 |
| `REPLAY_WARM_RECENT` | `10` | API; API 启动时预热的「最近完成」比赛场数（按 `completed_at` 倒序，只算真实、未归档、`artifact://` 回放的比赛），`0` 表示启动时不预热。队列最多同时排 32 场，超出丢最早的。Compose 没有转发，容器里用默认值 |
| `DEMO_UPLOAD_DAILY_LIMIT` | `10` | API; 仅 production：每个 owner 在滚动 24 小时内（按上传账本 `upload_ledger` 计数：每次上传或 Steam 导入写一行，24 小时后清理；归档和永久删除的比赛都照样算）最多新建的 demo 数，超出时 `POST /uploads/demo` 返回 `429` `upload_daily_limit`，`Retry-After` 为窗口内对应那次上传移出窗口的秒数。范围 `0`..`1000`，`0` 表示不限 |
| `DEMO_ACTIVE_PARSE_LIMIT` | `2` | API; 仅 production：每个 owner 同时处于 `queued`/`parsing`/`analyzing` 的 demo 上限，上传和解析重试超出时返回 `429` `active_parse_limit`（`Retry-After: 60`）。范围 `0`..`100`，`0` 表示不限 |
| `PARSE_QUEUE_GLOBAL_LIMIT` | `50` | API; 仅 production：所有 owner 合计处于上述状态的 demo 上限（按数据库计数，不看 Redis 队列长度），上传和解析重试超出时返回 `503` `parse_queue_full`（`Retry-After: 60`）。范围 `0`..`100000`，`0` 表示不限 |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker |

本地 artifact 默认落在 `ARTIFACT_STORAGE_ROOT=/data` 之下。

上传额度只在 production 生效，development/test 从不检查（范围校验在所有模式下都跑）。`POST /uploads/demo` 在读取 body 之前先由 multipart middleware 按 session owner 预检一次，route 在建 demo 之前再权威检查一次；预检本身出错时 fail closed，返回 `503` `upload_quota_unavailable`（`Retry-After: 30`）。分片上传在建会话时预检一次（只作提示），权威检查在 `complete` 的提交事务里。拒绝响应的 body 是 `{"detail": {"code", "message", "retryAfterSeconds"}}`，同时带 `Retry-After` 头。worker 不读取这三项。

分片上传的七个 `UPLOAD_*` 设置在所有模式下都做范围校验（API 和 worker 启动时）。全站会话上限和暂存盘余量是资源限制，不是额度，开发模式也生效。

## Render clip 与 render worker

| Name | Default | Used by |
| --- | --- | --- |
| `MAX_RENDER_CLIP_SECONDS` | `60` | backend API |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker |
| `RENDER_WORKER_MODE` | `fallback` | API, worker; `external` 时短片任务保留在队列等待独立 worker 领取 |
| `RENDER_CLIP_QUEUE_TIMEOUT_SECONDS` | `1800` | worker; 无人认领的 `render_clip` 任务在队列上等待的上限，超时按 `RENDER_QUEUE_TIMED_OUT` 落 failed，用户可重试 |
| `RENDER_CLIPS_ENABLED` | `0` | API; 仅 production 生效：为 `1` 时才开放 `POST /demos/{demo_id}/render/clip` 与 `.../render/jobs/{job_id}/retry`，否则返回 `404`，`/auth/me` 的 `capabilities.renderClips` 为 false。development/test 始终开放。没有部署外部 GPU worker（`RENDER_WORKER_MODE=external`）时保持 `0` |
| `API_BASE_URL` | `http://localhost:8000` | render-worker runner |
| `WORK_DIR` | `.render-worker-work` | render-worker runner |
| `POLL_INTERVAL_SECONDS` | `5` | render-worker runner |
| `RENDER_ADAPTER` | `fake` | render-worker runner adapter 选择；`csdm` 需操作者在指定 GPU 机器显式启用 |
| `DEV_FAKE_VIDEO_PATH` | unset | render-worker fake adapter |
| `CS2_INSTALL_DIR` | unset | render-worker manual adapter |
| `STEAM_USER_DATA_DIR` | unset | render-worker manual adapter |
| `CS2_MANUAL_OUTPUT_FILENAME` | `{job_id}.mp4` | render-worker manual adapter |

## 冒烟测试样本

| Name | Default | Used by |
| --- | --- | --- |
| `SAMPLE_DEMO_PATH` | unset | optional smoke sample upload |
| `SAMPLE_DEMO_NAME` | unset | optional display name for smoke sample |
| `REQUIRE_SAMPLE_DEMO` | `0` | make smoke fail when no sample is configured |
