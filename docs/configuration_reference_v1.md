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

## 身份、会话与 owner 边界

| Name | Default | Used by |
| --- | --- | --- |
| `AUTH_MODE` | unset (required) | backend identity mode: `development`, `test`, or `production` |
| `AUTH_PROVIDER` | unset (required in production) | production browser identity provider: `steam` or compatibility `oidc`; local Compose sets `steam` |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | server-trusted frontend redirect origin; must equal the HTTPS API origin in production |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | backend public origin; same exact origin as frontend in production |
| `AUTH_COOKIE_SECURE` | `false` | must be enabled in production |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | opaque session cookie name |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | bounded Redis/browser session lifetime |
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

Docker Compose 在容器内使用 service 名（`postgres`、`redis`），面向浏览器则使用 host URL（`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`）。本地 Compose 必须显式使用 `AUTH_MODE=development`，可以使用 local adapter 以及明确标注为非生产的 Steam 加密 key。

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
| `UPLOAD_CHUNK_BYTES` | `1048576` | bounded upload/read chunk size |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker |

本地 artifact 默认落在 `ARTIFACT_STORAGE_ROOT=/data` 之下。

## Render clip 与 render worker

| Name | Default | Used by |
| --- | --- | --- |
| `MAX_RENDER_CLIP_SECONDS` | `60` | backend API |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker |
| `RENDER_WORKER_MODE` | `fallback` | API, worker; `external` 时短片任务保留在队列等待独立 worker 领取 |
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
