# CS2 Demo AI Coach — Rules-Based 2D Beta V1

[![CI](https://github.com/luc1ferxx/CS2/actions/workflows/ci.yml/badge.svg)](https://github.com/luc1ferxx/CS2/actions/workflows/ci.yml)

网站型 CS2 demo 复盘与规则教练原型：上传 `.dem`，后端异步解析，立刻得到战术回放、回合复盘、时间轴和确定性教练建议。真实第一人称视频是**可选的异步增强**，不是使用前提。

**"AI Coach" 是产品名，不是实现方式。** 本仓库不调用 OpenAI 或任何 LLM，coaching 结果全部来自 `backend/app/analysis/rules.py` 的 8 条确定性规则。

- 当前进展与优先级：[项目进展（2026-09-13）](docs/project_status_2026-09-13.md)
- 完整路由与 curl：[API Reference](docs/api_reference_v1.md)
- 完整环境变量：[Configuration Reference](docs/configuration_reference_v1.md)

## 产品流程

```text
user uploads .dem
  -> backend parses rounds, players, sampled positions, kills, deaths, bomb/utility events
  -> rules analyzer creates deterministic coaching events
  -> tactical replay / round review / timeline / coaching 立即可用
  -> user clicks an event or tick
  -> 有已渲染片段则同步播放第一人称视频，否则继续用战术回放
  -> user may request a render_clip job for a short selected range
```

浏览器不能直接播放 `.dem`——它是游戏状态与事件记录，不是视频流。真实第一人称画面必须由我们控制的 GPU worker 离线渲染成 mp4 后写回 replay contract。

## 架构

```text
frontend (Next.js 15 App Router)
  -> FastAPI API
    -> PostgreSQL:  accounts, external identities, encrypted Steam connections/matches,
                    owner-scoped demos, demo_jobs, coaching_events
    -> Redis queue: parse/render job dispatch
    -> artifact storage: uploads, replay blobs, summaries, videos
  -> worker process (mock_parse / real_parse / mock_render / render_clip)
  -> standalone render-worker (fake / manual / opt-in CSDM),外部进程,token-gated API
```

PostgreSQL 只存可索引的元数据和 backend-neutral logical reference。Replay frames、parser event contract、video metadata 都在 replay JSON artifact 里；`.dem`、replay artifact、视频始终留在 private artifact storage，**不进 PostgreSQL**。

Artifact 出入口统一走 `backend/app/services/storage/` 的 provider-neutral contract。Logical reference 绑定 lifecycle state、artifact kind、opaque owner、demo 和随机 artifact id；客户端 filename 不参与 physical object key。Development/test 用 local adapter，production 必须用 private S3-compatible adapter（`ARTIFACT_STORAGE_BACKEND=local` 在 production 直接启动失败）。生命周期、错误分类与验收合同见 [`docs/object_storage_safe_artifact_intake_v1.md`](docs/object_storage_safe_artifact_intake_v1.md)。

## 身份与 owner 边界

Production 显式设置 `AUTH_PROVIDER=steam`：Steam OpenID 2.0 验证 SteamID64 → `accounts`/`external_identities` 映射 → Redis-backed opaque `HttpOnly` session cookie。所有 user API 从可信 session 派生稳定 `owner_id`，private media 在**字节交付时**再次验证 session、owner 和 artifact path。

- `accounts` 持有 opaque `owner_v1_...`；`external_identities` 强制 `(provider, subject)` 与 `(owner_id, provider)` 唯一，没有隐式 profile 合并。
- Match-history 授权与 OpenID 分离：Game Authentication Code 和 sharing code 服务端 AES-256-GCM 加密，永不回传浏览器。
- 本地开发可显式 `AUTH_MODE=development|test`，用 `X-Dev-User-Id: owner-a` 模拟 owner；该 header 在 production 被拒绝，绝不作为 fallback。

细节见 [`steam_auth_accounts_v1.md`](docs/steam_auth_accounts_v1.md)、[`steam_match_sync_v1.md`](docs/steam_match_sync_v1.md)、[`production_auth_owner_private_media_v1.md`](docs/production_auth_owner_private_media_v1.md)。

## 项目结构

```text
frontend/     app/ 路由页 · components/ replay|coaching|auth|steam|upload · lib/ 客户端与 helper · public/maps/ radar 资源
backend/      app/api/ 路由 · app/analysis/ 规则 · app/parser/ demoparser2 适配与归一化 · app/services/ 领域服务 · app/workers/worker.py
render-worker/  runner.py CLI · adapters/ fake|manual|CSDM
docs/         合同、验收记录与阶段目标
```

## 本地运行

已配置好录制环境的 Windows 机器，双击根目录 **`Start CS2 Coach.cmd`**，或：

```powershell
& .\scripts\start-local.ps1        # -SkipRenderer 只起网页；-PreflightOnly 只校验本机配置
```

其他环境（不含本机录制）：

```bash
docker compose up --build
```

- Frontend http://localhost:3000 · Dashboard `/dashboard`
- API http://localhost:8000 · Health `/health` · Diagnostics `/diagnostics`（仅 development/test，production 返回 `404`）

Mac 上开发 Windows 桌面版见 [`macos_development_windows_release_v1.md`](docs/macos_development_windows_release_v1.md)。

### 验证

```bash
./scripts/verify.sh      # backend compile/tests · render-worker · ruff · mypy · frontend tests/lint/typecheck/build
./scripts/rc_check.sh    # verify.sh + Docker build/up + /health + /diagnostics + cloud preview smoke
```

`verify.sh` 与 `.github/workflows/ci.yml` 跑同一组检查，且**不会在第一个失败处中断**，末尾汇总所有失败项。它优先使用 `.venv/`，也可 `PYTHON=/path/to/python ./scripts/verify.sh`。

单独运行：

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
cd frontend && node lib/demo-library.test.mjs      # 每个 lib/*.test.mjs 都可直接跑
cd frontend && npm test                             # 组件/页面测试（Vitest + Testing Library + jsdom）

PYTHONPATH=backend .venv/bin/python -m unittest discover backend/tests
.venv/bin/python -m ruff check .        # 规则与豁免理由见 ruff.toml
.venv/bin/python -m mypy                # 范围与 per-module debt 见 mypy.ini
```

RC 手动浏览器 QA 清单见 [`release_candidate_qa_v1.md`](docs/release_candidate_qa_v1.md)；部署准备见 [`deployment_readiness_v1.md`](docs/deployment_readiness_v1.md)。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| Demo Library `/dashboard` | 搜索、状态/地图筛选、排序、重命名、软归档；仅在有活动任务时轮询；ingestion phase、attempts、失败原因与 owner-scoped retry |
| Safe Artifact Intake | 公开上传只接受 `.dem`；先流式写入 private quarantine 并同时计算真实长度与 SHA-256，验证后 promotion 为 owner/demo-bound accepted artifact；只有 accepted source 能创建 parser job |
| Replay contract | 回合、玩家、采样帧、击杀/死亡、compact parser events、地图与视频 metadata、contract diagnostics |
| 复盘工作区 | 视频或战术回放、播放控制、时间轴、回合选择、个人建议同步到同一个 tick/round state；点击建议定位画面并优先复用覆盖该时刻的已保存 POV 视频 |
| Personal review | 默认匹配 `xelex`，可保存昵称或 Steam ID、切换比赛中其他玩家 |
| Rules-based coaching | 8 条确定性规则，无 LLM、无不透明 AI 文案 |
| Private media | `/demos/{id}/media/video` 与 `/demos/{id}/render/jobs/{job_id}/media/video`；GET/HEAD/Range 逐字节校验 owner，无 public static mount |
| Steam 集成 | OpenID 2.0 登录；`GetNextMatchSharingCode` 每次最多发现 20 场；只展示真实 discovery/status，不伪造地图、比分、玩家 |
| Render clip | 围绕 coaching event 或当前 tick 创建短 POV clip job；token-gated manifest claim → media upload → terminal-safe callback |

### 明确不做

- 不提供密码、Steam Guard、团队或通用账号管理 UI；浏览器不处理 provider token。
- 不实现社区 replay CDN / share-code 解码，不抓取 Steam 私有网页，不模拟 Game Coordinator。
- 不在 API 或 parser worker 容器里启动 CS2、Steam、OBS、ffmpeg。
- 网站不控制用户电脑、不读本地文件、不录屏；录制工具只在操作者显式启用的渲染机运行。
- 不把用户上传 MP4 当作主路径；不把大帧数据、raw dataframe、视频或 `.dem` 塞进 PostgreSQL。
- 不调用 OpenAI 或任何 LLM。

## 地图与坐标

支持 Dust II、Mirage、Inferno、Ancient、Nuke、Anubis 六张图的 radar。**其中只有 Dust II 和 Nuke 使用真实 CS2 overview transform（`calibrated: true`）；Mirage、Inferno、Ancient、Anubis 是手估 bounds 矩形（`confidence: "approximate"`）。** Nuke 支持上下层并跟随所选玩家楼层。

Normalizer 把世界坐标转成 **radar 百分比（0–100）** 写入 replay frames（这是渲染契约），`z` 保留原始 world units。规则引擎不直接比较百分比：每个地图配置发布 `worldUnitsPerPercent`（Dust2 radar 跨度 4506 world units，Nuke 是 7168），距离函数先换算成 **world units** 再与阈值比较，因此同一个阈值在每张图代表同样的真实距离。阈值按 Dust2 尺度换算而来，Dust2 行为不变。未在上表中的地图退回**按本场比赛实际站位范围**动态取 bounds，此时尺度由该场比赛推出，仍随比赛变化。校准计划见 [`map_coverage_calibration_v2_goal.md`](docs/map_coverage_calibration_v2_goal.md)。

Radar 资源来自 [rabume/cs2-dma-radar](https://github.com/rabume/cs2-dma-radar)，其 README 将素材归属于 Lexogrine 的 CS2 React HUD 与 boltgolt 的 Boltobserv。

## Render worker 边界

默认 `RENDER_WORKER_MODE=fallback`：没有连接外部 worker 时，render clip job 会被明确标记失败（`RENDER_WORKER_UNAVAILABLE`），不会静默挂起。`external` 模式下由操作者显式启用的独立 Windows GPU worker 轮询 durable DB 队列，使用 CSDM + HLAE + FFmpeg 渲染并回传，FFprobe 校验后入库。

本机已完成 xelex 20 秒真实片段验收，见 [`local_xelex_render_acceptance_2026-09-07.md`](docs/local_xelex_render_acceptance_2026-09-07.md)。Manual MP4 绑定仅用于开发和 QA，必须人工校准 tick/video offset，且只代表它实际覆盖的 tick range。

## 配置

完整表格（77 项）见 [Configuration Reference](docs/configuration_reference_v1.md)。最常用的：

| Name | Default | Used by |
| --- | --- | --- |
| `AUTH_MODE` | 未设置（必填） | `development` / `test` / `production` |
| `AUTH_PROVIDER` | 未设置（production 必填） | `steam`，或兼容路径 `oidc` |
| `FRONTEND_PUBLIC_URL` / `BACKEND_PUBLIC_URL` | `http://localhost:3000` / `:8000` | production 必须同源 HTTPS |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | 浏览器 API/media URL |
| `DATABASE_URL` / `REDIS_URL` | 本地默认 | API, worker |
| `ARTIFACT_STORAGE_BACKEND` | `local` | production 必须 `s3` |
| `OBJECT_STORAGE_BUCKET` | 未设置 | production 必填,private bucket |
| `STEAM_WEB_API_KEY` | 未设置 | server-only,production 必填,绝不暴露给前端/worker |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | 签入的开发 key（非生产用） | 32 随机字节的 URL-safe base64；每次启动都校验,production 仍用开发 key 会 fail closed |
| `STEAM_DEMO_PROVIDER` | `disabled` | 本 build 拒绝除 `disabled` 外的一切取值 |
| `RENDER_WORKER_MODE` | `fallback` | `fallback` / `external` |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | production 必须改 |
| `RENDER_CLIPS_ENABLED` | `0` | 仅 production：为 `1` 时才开放生成/重试短片，否则相关路由返回 `404` |
| `MAX_DEMO_UPLOAD_BYTES` | `1073741824` | 实际流式字节上限 |
| `DEV_USER_ID` | `dev-user` | 仅 development/test |

Production 启动会 fail closed 地要求：显式 identity provider、HTTPS + `__Host-` cookie + 单一 origin + 精确 CORS、真实 server-only Steam Web API key、非开发用的随机加密 key、非默认 render token、private S3-compatible 存储。Demo source/download 配置只属于 API——parser/render worker 永远拿不到 Steam/OIDC 浏览器密钥或 provider secret。

**production 凭证绝不能进入 `NEXT_PUBLIC_*`、源码、日志、提交的 env 文件或浏览器响应。** 真实 `.dem`、demo 归档、视频、replay cache、本地 `.env`、安装的工具和 QA cache 必须留在 git-ignored 目录；把真实对局 demo 当作不可信且可能涉及隐私的数据，只使用你有权本地保存的样本，不要提交。

## 当前限制

- **规则阈值的实际有效性未经真人验证。** 水平距离已统一为 world units，同一阈值在各图代表同样的真实距离；但阈值取值本身仍来自 Dust2 手调，没有人验证过这些建议是否真的有用。未支持的地图只能按单场比赛的站位范围推出尺度，approximate 地图的 bounds 也是手估的。
- **自动 Demo 下载端到端不可用。** Valve 没有公开个人比赛 Demo 下载接口，仓库也没有正式许可 Provider，`demo_source_provider_from_settings` 只能返回 disabled provider。下载器、并发限额与 import service 的下游分支已实现并有测试，但在本 build 中无法到达。手动 `.dem` 上传是唯一可靠入口。
- **解析仍是单 worker 串行；恢复是自动的，但有分钟级延迟。** 队列已从裸 `brpop` 换成 `BRPOPLPUSH` + 每消费者 processing list + 60 秒租约：worker 被 SIGKILL 后在途消息不再消失，由下一个 worker 的 reaper 推回队列并把 DB 行一并翻回 `queued`（本地实测 67 秒内自动跑到 `completed`，全程不需要手动改库）。Redis 没有持久化，所以真正的兜底是 DB 对账——超期的 `processing` 行被回收，`queued` 却没有对应消息的行被重新投递，累计 `PARSE_MAX_ATTEMPTS` 次仍失败则落 `failed` + `PARSE_ABANDONED`，此时重试按钮恢复可用。解析本身跑在子进程里（默认 20 分钟超时、4 GB `RLIMIT_DATA`），`demoparser2` 的原生崩溃只损失当前这个 demo，不再带走 worker。**但队列语义只是让多 worker 并存变得安全，compose 仍然只跑 1 个 worker、单线程逐个解析**：一个大 demo 解析期间其它任务照旧排队——横向扩容条件已具备，本轮未开启。恢复延迟的下界是租约 TTL（60 秒）；Redis 整体丢失时退化到 `PARSE_RECLAIM_AFTER_SECONDS`（默认 30 分钟）。
- **CI 不解析真实 `.dem`。** `backend/tests/test_demo_parser.py` 在 `demoparser2` 库边界打桩；真实文件校验只在可选的 `SAMPLE_DEMO_PATH` smoke 里，不在 CI 门禁内。
- `/health` 在 DB 和 Redis 全挂时仍返回 HTTP 200（body 为 `status: degraded`），编排器探针看不到失败。
- Parser frame 是采样数据，不是完整 tick density；`demoparser2` 对不同 demo 的 event family 与字段可用性不稳定，bomb/utility events 是 best-effort，缺失时相关 count、quick jump 或 event-backed 规则可能为空。
- 没有经济、装备快照、line-of-sight、utility trajectory 等高级战术上下文。
- Render clip 只验收过本机单台 Windows worker 上的 xelex 片段；其它地图、Demo/游戏版本兼容性与生产 GPU 集群未验收。
- **无人认领的 render clip 按挂钟超时，不看渲染机死活。** 队列上没有 worker 认领的短片任务在 `RENDER_CLIP_QUEUE_TIMEOUT_SECONDS`（默认 30 分钟）后落 `failed` + `RENDER_QUEUE_TIMED_OUT`，用户可以直接重试——在此之前它停在 `queued`，而 `queued` 既不可重试、又会被去重逻辑原样交回，是个用户无法自救的死胡同。判定只用等待时长，**不查 `render_worker` 心跳**：渲染机在线但连续 30 分钟排不上队的话，排队中的任务同样会被判死（重试即可，不丢数据）。阈值必须大于最长的一次正常渲染排队时间。
- Ingestion snapshot、diagnostics 和 regression fixtures 是 compact QA aid，不是生产 telemetry、日志平台或 parser trace storage。
- Steam OpenID 的真实 HTTPS callback、publisher-key 资格、Game Authentication Code 行为与限流仍需一次真实部署 smoke。

## 下一步

当前目标是让其他人能下载安装 Windows 桌面版，日常开发在 Mac 完成——优先完成 [桌面版安装发行](docs/desktop_distribution_v1.md)，由 Windows 构建环境产出安装包，并在无开发环境的机器上验收安装、导入与复盘。之后继续验证建议的实际帮助、减少重复提醒、改善排序并扩充地图校准；完整计划见 [项目进展](docs/project_status_2026-09-13.md)。

Steam Demo 导入适配器保持 fail closed：只有 Valve 明确授权的 partner endpoint 或正式许可 Provider 才能在后续独立审阅中注册，任何社区 share-code/CDN 路径默认关闭且标为 unsupported，下载内容必须走现有 artifact intake。手动 `.dem` 上传始终保留为兜底。

产品方向不变：**核心复盘体验必须在 `.dem` 解析完成后立即可用；真实第一人称片段是异步增强，不是使用网站的前置条件。**
