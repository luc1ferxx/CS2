# CS2 AI 教练项目终审报告

**审阅范围**：`/Users/jyxc-dz-0100616/Desktop/CS2`（只读，未改动任何文件）
**日期**：2026-08-07

## 1. 结论先行

这个仓库真正能在真实 `.dem` 文件上端到端跑通的 advertised 产品，大约只有 **30–35%**。 Ware 的项目 README 标题写着 "AI 教练"，但代码库里 **零 LLM 集成**（已 grep 验证），`docs/rules_2d_beta_launch_v1.md` 更是明确把 LLM/`ai_generating`/GPU 列为 Phase 0  deliberately  deferred 的非目标。所以"AI 教练"这个品牌标签跑在了产品前面——这不是隐藏缺陷，是文档自己定义的边界，但也是必须首先纠正的品牌对齐问题。

三个你必须先知道的事实：

1. **规则引擎有 8 个探测器，大约 3–4 个产出可信，2 个（trade 检测、isolated_entry）临床级错误，1 组（post-plant 三条规则）在生产环境是死代码。** trade 检测是纯径向距离（`rules.py:819`），isolated_entry 把"任意第一个 T 阵亡"当成"entry 孤立死亡"（`rules.py:145`）。这两个是规则引擎最常被触发的两条，搞错方向的代价是 IGL 不再信任整个产品。
2. ** flagship 的"视频切片"功能不存在。** `worker.py:123-137` 对每个 `render_clip` job 直接 fail `RENDER_WORKER_UNAVAILABLE`；`render-worker/runner.py:230` 写死 `FakeVideoAdapter`。`docker-compose.yml` 里根本没有 `render` 服务。用户点"Generate Clip"产出的不是 CS2 录像。
3. **真正的瓶颈在规则引擎上游——parser/data foundation，不在规则本身。** tick 数据是 2D 的（Z 在 `demo_parser.py:454-459` 被丢弃），无 viewangles/velocity/hitgroup/econ/utility-landing/flash-blind，采样约 4s 间隔，仓库里**没有真实 `.dem` fixture**，所有 parser 测试都通过 `sys.modules` swap 劫持 demoparser2。不先补数据层，规则写得再漂亮都是空中楼阁。

Auth / storage 子系统的成熟度被审计低估了——那不是"过度防御与威胁模型不匹配"，而是这个仓库里**真正达到生产级、经过对抗测试的部分**。

## 2. 仓库现状

### 架构图（简化）

```
Next.js frontend ──HTTP──▶ FastAPI backend
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
      parser/normalizer   demo_service.py    worker.py (brpop)
      (demoparser2)       (2581行 God-class)  │
            │                 │                 ▼
            ▼                 ▼          render-worker (未部署)
      replay blob (S3/local)   artifact store (sha256 内容寻址)
      ↓ 整个 match JSON        ↓ 隔离/传染/原子晋升
      rules.py (8 detectors)   SQLite (dev) / Postgres 16 (prod)
```

### 成熟度 honesty table

| 子系统 | 真实就绪度 | 关键阻塞 |
|---|---|---|
| Upload + parse + normalize 真实 `.dem`/`.zip` | **works** | 无（`upload_service.py:18` 接受 `.dem` + `.zip`，`demo_parser.py:166-211` 真提取 zip） |
| 8 条规则 over 真实解析数据 | **partial (~3-4/8)** | trade/isolated 临床错误；post-plant 生产死代码 |
| Steam match discovery + download | **adapter 存在但 provider 被禁用** | `demo_source_provider.py:69` 返回 `DisabledDemoSourceProvider`；`config.py:508-510` 强制 `STEAM_DEMO_PROVIDER=disabled` 才启动 |
| Render clip / video | **0%** | `worker.py:123-137` 直接 fail |
| Auth (Steam OpenID + OIDC) | **production-grade** | Steam OpenID 限流基于可伪造 client IP (`steam_auth_service.py:104`) |
| Storage (artifact/quarantine/promote) | **production-grade, 测试充分** | `LocalStorageService` 遗留并行系统 (`storage.py:2087-2338`)；S3 路径只在 fake 下测过 |
| Owner-boundary + SSRF 加固 | **production-grade** | 无 |
| Coaching 叙事 / LLM / mistake-impact-recommendation 散文 | **0%** | mock 里的 triple 只是 `mock_replay_service.py:181-252` 静态 seed，真实规则不产出 |
| Trends / benchmarks / player history / feedback / drills | **0%** | 无表、无记忆 |
| CI / frontend 测试门禁 / 真实 .dem 集成测试 | **0%** | `verify.sh` 只跑单测；`.mjs` 测试 orphaned；`cloud_preview_smoke.py` 存在但不在任何 gate |

审计在两件事上**过于苛刻**：auth/storage 子系统的防御深度（那是对的，不是"与威胁模型不匹配"）；规则引擎的确定性架构（文档明确定义为 rules-only V1，那就是产品，不是 cop-out）。审计在**三件事上过于宽容**：`.zip` 实际上被 shipped 产品接受（审计说"只接受 .dem"是错的）；前端 UI**已经诚实标注**"Mock playback shell — not real CS2 video"（`FirstPersonReplay.tsx:188`），审计提议的"UI 必须停止伪装"方案针对的问题代码里并不存在）；"dev-only owner harness"被审计自己的修正结论推翻（真实 ship state 是 Steam/OIDC 生产级账号系统）。

## 3. 必须先修（已通过 verifying 的真实缺陷）

按"是否阻止上线"排序，不是按 audit severity。

### 3.1 `[critical]` Trade 检测是纯径向距离，无 LOS/ travel-time/ sightline validity — `rules.py:819`

**失败场景**：`_has_trade` (rules.py:788-833) 用 `same_area_distance=12.0` 单位径向窗口，检查 death tick +5s 内是否有同侧击杀。两个方向都错： Mirage 地图上 A 点阵亡，5s 内 B 点发生的任何同侧击杀会被算成 trade（假 trade 掩盖真实失误）；队友在 11u 外正确枪位补枪但超过 12u 被漏判（假 untraded 责怪 positioning 正确的队友）。

**修复**：在没有 nav-mesh 之前 ship 有界版本：(1) 校验 trade-killer 与 death 位置的距离在 [2u, 18u]；（2）要求 killer 的击杀确实让 T↔CT 状态翻转；（3）把 `same_area_distance` 从 12.0 降到 ~8u（64 tick）/ ~10u（128 tick）。在 `structured_context_json` 里诚实标注 `sightlineVerified:false`。

### 3.2 `[critical]` Render 流水线没有自动化 CS2 渲染路径 — `worker.py:123-137` / `render-worker/runner.py`

**失败场景**：用户点 UI "Generate Tick Clip" → `create_render_clip_job` → enqueue → worker `brpop` → `process_render_clip_job` (worker.py:123-137) → 立即 fail `RENDER_WORKER_NOT_CONNECTED_ERROR`。`docker-compose.yml:116` 的 `worker` 服务跑的是 `app.workers.worker`（不是 render-worker/runner.py），**天生就不能渲染**；`docker-compose.preview.yml` 干脆删掉了 worker 服务。产出的"failed job"是 code 71103，UI 必须展示 `demo_service.py:66-68` 的常量文案"GPU worker not connected for render_clip..."，而不是 generic error。Test：前端状态机必须对 `RENDER_WORKER_UNAVAILABLE_ERROR_CODE` 实际常量有契约测试，不是 mock。

**修复**：(a) 短期——确保 UI 诚实地把这个 job 标记为"需要 operator-side render"，而不是 feature failure（前端其实已经半做到了，`FirstPersonReplay.tsx:188` 标注了 "Mock playback shell"）；(b) 长期——build real adapter（HLAE + headless CS2 + ffmpeg），多周 GPU 端项目。**(a) 是半天工作量，(b) 是独立的 Stage 6 workstream。**

### 3.3 `[critical]` 仓库没有真实 `.dem`；parser 在 demoparser2 边界被 mock — `test_demo_parser.py`

**失败场景**：`find . -name '*.dem'` 在整个仓库（除 build 目录）返回 **空**。`test_demo_parser.py:164` 用 `patch.dict("sys.modules", {"demoparser2": fake_module})` 整个替换 demoparser2。`_build_kills` **完全没测**，`_build_rounds`/`_build_frames` 只针对作者手写的 fake record shape 单测。`_sample_ticks` 数学（`demo_parser.py:266-270` 的 prop-set 递减阶梯）只在 cooperative stub 下跑过。任何對著真实 demoparser2 0.41.0 record shape 的回归（`user_steamid`/`attacker_steamid`/`total_rounds_played`/`X/Y`/`is_alive`/`has_bomb`/事件名如 `smokegrenade_detonate`）会**整张测试池绿灯通过**。

**修复**：提交一个 30-90s 公开 MM `.dem`（<5MB）到 `backend/tests/fixtures/real_demo/` + 一个单比赛 `.zip`（覆盖提取路径）。加 `test_integration_parse_to_coaching` 跑**真实** `parse_demo_file(FIXTURE_PATH)`（不 patch）→ `normalize_parser_output` → `analyze_replay`，断言：tickRate 是合法 float、rounds count > 0 且 winnerSide 已设、frames 有非 None x/y、≥1 kill event、≥1 `bomb_planted`。保留 FakeDemoParser 测试（便宜地跑控制流），但**增量**加真实边界。`.gitignore` 忽略 `.dem`/`.zip`/`.mp4`，但 `docs/sample_demo_fixture_v1_goal.md:7` 显式禁止 commit 大 `.dem`——这是**政策决定**，使得真实 parse 测量在流程上就不可能。值得挑战。

### 3.4 `[high]` `docker-compose.yml` 把已发布的确定性 dev AES key 作为 runtime 默认值 — `:34-40, 92, 147`

**失败场景**：审计低估了这一条。`docker-compose.yml` 两个文件都用 `${VAR:-default}` 形式内嵌：`STEAM_CREDENTIAL_ENCRYPTION_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=`、`RENDER_WORKER_TOKEN=dev-render-worker-token`、`AUTH_COOKIE_SECURE=0`（以及 `AUTH_MODE=development` 是唯一不依赖环境变量的）。执行 `docker compose up`（最常见新手/本地/preview 错误）产生：**完整 API+worker+frontend+postgres+redis 栈，AUTH_MODE=development，dev key 生效**。production guard（`config.py:500-505`）只在 `auth_mode == "production"` 时触发——所以 dev 模式下生成的密文**确实能用发布 key 解密**。 blast radius 不同：任何能访问容器端口的人。审计把 `.env.example`（medium）和 `docker-compose.yml`（未提）分开看，错过了真正的脚注。

**修复**：把 `docker-compose.yml` 的 `${VAR:-default}` 全改成 `${VAR:?}`（preview.yml 已经这么做了）；`.env.example` x2 把字面量换成 placeholder 提示（`=...generate 32 URL-safe base64 bytes...`）。合并成**一条** `[medium/security]` 发现。

### 3.5 `[high]` isolated_entry 把任意首个 T 阵亡当 entry 尝试 — `rules.py:120-203`

**失败场景**：`find_isolated_entries` 选 `min(deaths, key=...)`（rules.py:137）——**一局里最早的单个 T 阵亡**——然后如果最近队友 > `isolated_teammate_distance=22u` 就发"Entry died isolated from trade support"。完全没检查 bombsite 接近度、entry timing window（freeze-end 后 15s 内）、victim 是否真 entry fragger。审计已验证 verbatim。

**修复**：gate on (a) victim 在 per-map bombsite POI 距离内，或 (b) parser `isEntryAttempt` signal（若未来加），或 (c) victim 在 round-freeze-end +8s 内且离最近 bombsite entry <30u。否则前把事件改名为 `first_t_death_untraded`，severity 从 high 降到 medium。**这是单点最毁信任的事件**——IGL 读"你的 entry 孤立死亡"关于一个从未前顶的玩家，他不会回来。

### 3.6 `[high]` post-plant 三条规则在生产是死代码 — `_bomb_state` (demo_parser.py:711-715) 只返回 `"carried"`

**失败场景**：`find_post_plant_spread_issues` / `find_post_plant_spacing_with_bomb_event` / `find_retake_desyncs` 三个规则都 gate on `_planted_bomb_position(frame)` 要求 `bombState.status == "planted"`。但 `_bomb_state` (demo_parser.py:711-715) **只返回 `"carried"`**——per-frame bombState 从不消费 `bomb_planted` 事件（事件在 `_build_bomb_events` 585-607 正确 emit，但 per-frame state 不认）。所以三条 post-plant 规则**在生产永远不会触发**。结合 3.1 的 trade 错误，retake  coached 内容是双倍缺的。

**修复**：Stage A（parser，1 周内）——修 `_bomb_state` 真在 `bomb_planted` 事件触发时转 `planted`，让三条规则复活。这是 parser 层修复，不要请 LLM。

### 3.7 `[high/missing-capability]` parser 缺 aim/utility/econ telemetry — 几乎所有 coaching domain 不可达

已确认缺：viewangles/velocity（`demo_parser.py:454-459` 丢弃 Z 且不取 view_angles）、hitgroup（`player_hurt` other= 列表无，demo_parser.py:60-64）、smoke_expired/inferno_expired/flashbang_exploded/player_blind（parse_demo_file 只订阅 6 个事件族，demo_parser.py:28-163）、item_equip/item_purchase/player_money（`_merge_players` 402-430 只出 id/name/side）、round_announcement/last_round_half。`winnerReason` 始终 None（`round_end` 订阅只要 `total_rounds_played`，demo_parser.py:76-80）。

**修复优先级**（§5 路线图详述）：Stage A 修 bomb-state + 加 tickRate；Stage B 加 view_angles + vel_* + hitgroup + winnerReason；Stage C 加 smoke_expired/inferno_expired/player_blind/item_* /player_money/round_announce。

## 4. 优化项（高杠杆重构 & 删除）

### 4.1 拆分 `demo_service.py`（2581 行 God-class）

审计建议的分解边界是对的，我复核过结构。目标模块：

| 新模块 | 大致 LOC | 拥有的方法 |
|---|---|---|
| `demo_ingest.py` | ~250 | `prepare_real_demo`、dispatch、`claim_parse_job` payload prep |
| `parse_lifecycle.py` | ~450 | `claim_parse_job`/`mark_parse_analyzing`/`complete_parse_job`/`fail_parse_job`/`retry_parse_job`/`demo_ingestion_status` + **SteamMatch parser-dispatch state machine**（约 6 处重复更新块移到一个 `SteamMatchParseState` shell class）|
| `render_lifecycle.py` | ~350 | `create_mock_render_job`/`create_render_clip_job`/`claim_render_clip_job`/`apply_render_worker_result`/`fail_render_clip_job`/`transition_mock_render_job` |
| `replay_blob.py` | ~300 | `write_replay_blob`/`load_replay_blob`/`public_replay_contract`/`public_video_status`/`get_video_status` + `_PendingReplayUpdate` state machine |
| `video_registry.py` | ~200 | `_private_video_storage_key`/`open_private_video`/`attach_manual_video`/calibration |
| `demo_listing.py` | ~250 | `list_demos` + `demo_list_item` + search/sort + **加 `limit(200)` cap** |
| `demo_service.py` 本体 | ~400 | 薄 facade 组合以上 |

`api/demos.py` 和 `worker.py` 的 17 个调用点最小改动。

**状态（2026-09-18）：已实施。** `demo_service.py`（当时 3,466 行）拆为 `backend/app/services/demo_service/` 包：`__init__.py` 是薄 facade（`DemoService` 公共 API 不变，24 个调用方零改动），职责拆到 `demo_library.py`、`demo_ingest.py`、`parse_lifecycle.py`、`render_lifecycle.py`、`render_worker_media.py`、`replay_blob.py`、`video_registry.py`，共享部分在 `constants.py`、`errors.py`、`_helpers.py`、`projection.py`、`steam_match.py`（含本节要求的 `SteamMatchParseState`，六处 SteamMatch 更新块归一）。组件间调用经 `self._service.<component>.<method>` 显式暴露，拆分时统计的耦合最重的一条是 render → replay（32 处调用），即 §4.3 的问题在结构上可见了。该包不再有 mypy 豁免（原 24 个错误全部清零）。`storage.py` 同样拆为 `storage/` 包（contract / errors / local / s3 / factory / legacy），`LocalStorageService` 保留在 `legacy.py`，§4.2 的删减未做。`list_demos` 的 `limit(200)` 上限是产品行为变化，未在此实施。

### 4.2 删/简的代码（估计净减 ~650 LOC）

| 目标 | 验证 LOC | 动作 |
|---|---|---|
| `LocalStorageService`（storage.py:2087-2338） | ~252 | 三个 concern 折叠进 `LocalArtifactStore` + URL-rewrite helper；删类 + cat-keyed plumbing |
| `_SeekableStreamWindow`（storage.py:1364-1410）| ~47 + 测试 | 删。`io.BufferedReader` 已足够；S3 `put_object` 已 streaming |
| `artifact_intake._ArtifactStore` Protocol（artifact_intake.py:61-123）| ~62 | 与 `ArtifactStore`（storage.py:271）近重复，降级为 TypeAlias |
| mock 手写 `coaching_events`（mock_replay_service.py:181-252）| ~72 | 换成 `analyze_replay(mock_fixture)` 调用——mock 走与 real 同一条流水线 |
| 重复 helper（utc_now/_job_metadata/_aware_datetime/_optional_str 等）| ~140 跨 6 文件 | 提到 `services/_common.py`，删本地副本（注意 `_aware_datetime` 在 diagnostics.py:472-475 **没 None guard**，共享版本必须是 None-safe）|
| SteamMatch parse-state 重复块（demo_service.py 内 6 处 + steam_demo_import_service.py:417-463 镜像）| ~80 | 单一 `SteamMatchParseState` owner |

### 4.3 不再重写整个 replay blob per video mutation

`_prepare_replay_video_update`（demo_service.py:1843）读整个 blob→换 4 个 video 字段→重写整个 blob。6 个 caller 都这么干。**修复**：(1) 在 `demos` 表加 `video_status` JSON 列（仿 `coaching_event_count` 模式），`complete_parse_job` 和 `apply_replay_worker_result` 就地更新；(2) crash-consistency：把 `previous_replay_ref` 写入 `Demo.metadata_json`（与 commit 同 txn），让 orphan delete 可恢复。这一项同时解决 rewrite cliff + orphan window + `list_demos` N×`load_replay_blob` 三个审计发现。

### 4.4 加 reaper + dispatch 幂等性到 worker

`worker.py:198-218` `brpop(timeout=5)` 无条件消费；`dispatch_parse_job`（demo_service.py:486-506）`client.lpush` 无存在性检查；`STALE_PARSE_AFTER_SECONDS`（demo_service.py:76）计算并送到前端但**永不消费**（grep 验证）。SIGKILL 后 job 静默丢失， stuck demo 不可重试（`retry_parse_job:591-593` 拒绝 status≠failed）。**修复**：(1) `run_worker` loop 顶加 reaper branch，扫描 active 表里 `started_at < now - STALE_PARSE_AFTER_SECONDS` 的 job，increment attempts，达 max_attempts 进 dead_letter；(2) `SETNX cs2:dispatch:{job_id}.lock` TTL 守卫 lpush；(3) `DemoJob` 加 `attempts`/`max_attempts` 列 + 复合索引 `(demo_id, job_type, status, created_at, id)`。

### 4.5 进程 mandate 改写（治理 docs 膨胀的根源）

AGENTS.md:79 "For every change, run the relevant verification commands above" + README:171 "one-click verify.sh" + `rules_2d_beta_launch_v1.md` 13-gate 共同构成**默认每改一次 full-suite** 的流程。这是 ~86KB 流程文档 > crisp capability claims 的根源。**改成两档**：Per-edit = `compileall` + touched module 的单测 + frontend typecheck (~2 min)；RC = `verify.sh` + `rc_check.sh` + Postgres-16 migration run + 一个 real-.dem 集成测试 + coverage 60% 红线。

## 5. 距离"真正的 CS2 AI 教练"还差什么

按层级说，**数据层是唯一真正的 blocker**。

### 5.1 Data foundation（最优先，规划详见外部研究 §2）

所有 coaching query 今天都是 N 次完整 blob 下载 + N 次 Python JSON 解析，**无索引**。目标 schema（§2.3 lens 已给）：9 张 OLTP 表 `tick_frames` / `kill_events` / `damage_events` / `flashes` / `smokes` / `infernos` / `he_grenades` / `bomb_events` / `loadouts` / `purchases` / `shots` / `rounds` / `player_match_stats` / `player_identities`，dual-write 在 parse 同一 txn 完成；blob 缩到 ~300-700KB，只承担 replay/video 渲染。MR12 demo 预估 ~1.5M kill+damage 行 for power user with 300 demos；`player_match_stats` ~3000 rows/user 让 "ADR over last 50 matches" 在 <10ms。

### 5.2 Metrics layer（CS2 教练真正该算的东西）

光有 telemetry 表不等于教练。**真正该算的 CS2 指标**（行业定义）：

- **ADR / KAST / Hs% / K-D differential**：HLTV Rating 2.0/2.1 标准四件。公式公开（hltv.org/rating）。
- **Trade 窗口（ms）**：death tick 到最近同侧有效击杀的 tick delta，需要 LOS + nav-mesh。**当前 5.0s 固定窗 + 纯径向 = 最严重错误**。
- **Entry 成功率 / opening duel win rate**：round 首个击杀的 killer 存活率。
- **Flash-assist conversion**：blind window 内 follow-up kill 的比率（需 flashbang_exploded + player_blind 重建 blind window）。
- ** Econ tier / force-buy EV / loss-bonus 管理**：需 item_purchase/player_money，当前完全缺失。
- **1vX clutch EV**：alive-counts 在 kill tick 的可计算（数据 Plane 现有）。
- **Crossfire 识别**：需两个 CT 不同位置 + LOS（当前 parser 无 LOS）。
- ** Aim 指标**：headshot rate、first-bullet accuracy、 spray control、counter-strafing（vel sign-flip timing）、crosshair placement——全部需 viewangles/velocity/inaccuracy，当前全缺。

**awpy（pnxenopoulos/awpy）** 是基于 demoparser2 的 Python 层，已 bundle nav visibility 计算——是接手本仓库 parser 的正确默认选择（比 demoinfocs-golang 更 Python 亲和；比 demoparser2 多 nav-mesh LOS）。

### 5.3 AI/LLM 层（100% 缺席）

明确说不 build 的阶段（rules_2d_beta_launch_v1.md:220-228 已 gate），但**架构该预留**：

- **分工铁律**：所有数值（count/avg/dist/rate）由 Python/SQL 算，模型禁止"心算"。模型只做：(a) 已确认 finding 的 triple 散文（mistake/impact/recommendation）；(b) 基于预计算分数的 ranking 而非 prose ranking；(c) retrieval-grounded Q&A；(d) schema-constrained drill generation。
- **模型选型**：Haiku 做 tool-grounded Q&A + per-finding narrative（$0.25/$1.25 per MTok）；Sonnet 做 prioritization；Opus 做 clip critique（defer）。**禁止单 turn 多模型"为了准确"——破坏 cache breakpoint**。
- **工具设计**：`get_round_summary` / `get_kill_feed` / `get_position_heatmap` / `get_econ_state` / `get_timeline` / `get_finding` / `search_findings` / `explain_finding` / `get_player_stats`，`tool_choice: auto`，structured `coaching_report` tool `strict: true`。
- **Prompt caching**：`ReplayAIContext` 包（~3-8k tokens）用 `cache_control: {"type": "ephemeral"}` 锚定，follow-up turn 读 cache 0.1× input。**per-demo 估算 ~$0.12**（Haiku-hauled）。
- ** grounding & 验证**：每条 claim 必须带 `(demoId, roundNumber, tick)` 引用，backend `CoachOutputValidator` 重.assert 与 telemetry 一致；**独立 LLM judge**（不同 session，只看 packet 不看推理）标 `S/P/U`，`%supported` 为 KPI，红线 `mean_unsupported > 0.5/demo`。
- **Eval**：ground truth 来自 certified CS2 coach Likert 评分（Biol Sport 2025 协议）+ rule-engine oracle（30 个 seeded scenario）+ faithfulness judge。**禁止 self-judge**。
- **成本模型**（per demo，所有 surface）：Q&A ~$0.06、prioritization ~$0.05、8 capped narratives ~$0.02、judge ~$0.08、actionability judge ~$0.01 = **~$0.21/demo**（cache 后 ~$0.12）。

**行业空白是真实存在的**：Leetify "Personal Coach" / Scope.gg "map performance tips" / Aimlabs "AI Powered coach" 都是 heuristic/task-based tip engine，**没有 shipping 的、LLM-grounded、逐 tick 证据引用的 CS2 demo 教练产品**。这个空白就是楔子——但前提是 data foundation 先修好。

### 5.4 Product/UX gap

- mock replay service `mock_replay_service.py` 默认路径上是**手写 curated events**，比 real 路径更"精心"。`source='mock'` 被 `test_normalizer.py:62` 锁死在 real-parser-input fixture 上。**修**：mock 跑 `analyze_replay` 同一条流水线（§4.2）。
- 6 张 siege map（`backend/app/parsers/map_config.py`）缺 `.nav` 文件。Ship 6 个 `.nav` 给 awpy 用能直接修复 trade 检测（audit #1 正确性缺陷）——这是**单点最高 leverage 的数据资产**。
- Steam match discovery 宣传为可用功能，`demo_source_provider.py:69` 实际 gated off。UI/文档必须诚实标注。

## 6. 路线图

**Stage 0 — Stop lying, unblock correctness（2-4 周，solo）**
- Frontend 全部 "AI Coach" 改为 "Deterministic tactical review"（CoachingPanel.tsx:94 已经在用这标签）。
- 实现计划 `CS2_DEMO_AI_COACH_IMPLEMENTATION_PLAN.md`（17KB，repo root）顶部加 deprecation banner 指向 `docs/rules_2d_beta_launch_v1.md:240-256`。
- `.env.example` x2 + `docker-compose.yml` 字面量 secret 换 placeholder。
- 修两条规则：trade detection（§3.1）、isolated_entry（§3.5，gate on entry posture else 改名降级）。
- post-plant spread 规则 retitle 为 `postplant_stack_held` 加 positive 事件；retake_desync 加 suppression（distinct angle approaches / CT trade-kill window 内不 fire）。
- Milestone：真实 `.dem` 上传 → 零 fabricated rules-events + 零 "AI"散文。

**Stage 1 — Make real .dem pipeline the default（4-6 周，solo）**
- 删 mock 手写 coaching events，让 mock 跑同一条 `analyze_replay`。
- 修 `normalizer.py` 真实 flag → `source="real"`/`"mock"`。
- 至少让 `render-worker/runner.py` 在本地 GPU box 可跑（哪怕真 adapter 没 build）。
- Milestone：非技术朋友上传 `.dem` → 看到真实 rules events → 能 scrub replay。

**Stage 2 — Parser breadth（4-8 周，solo）**
- Stage A（1w）：修 `_bomb_state`（`demo_parser.py:711-715`）让它在 `bomb_planted` 时转 `planted`；加 `tickRate` 到 contract ReplayContext。
- Stage B（2w）：`parse_ticks` props 加 `view_angles` + `vel_*`；`player_hurt` other= 加 `hitgroup`；`round_end` 订阅加 `winnerReason`。
- Stage C（3w）：订阅 `smoke_expired` / `inferno_expired` / `flashbang_exploded` / `player_blind` / `item_equip` / `item_purchase` / `player_money` / `round_announce_last_round_half`。
- Ship 6 `.nav` 文件 + awpy 接入。
- Milestone：untraded_death 能报 body-part、weak_utility 能评分 landed-on-angle、isolated_entry 能在 eco round suppress。

**Stage 3 — Aggregate stats + baselines + feedback（3-4 周）**
- 加 `player_match_stats_fact` / `player_baselines` / `coaching_feedback`；`players_json` free-text → `player_identities` table（SteamID64 主键）。
- Milestone：demo 页显示 "your ADR this month vs your 20-match average"——第一块真正 incumbent-style 产品 surface。

**Stage 4 — LLM narrative layer（4-6 周，小团队）**
- Haiku tool-using agent + structured `coaching_report` tool + cached packet + faithfulness judge。
- Milestone："Critique this match" 返回 6-finding  prose review，带可验证 tick 引用，在 held-out gold set 上可评估。

**Stage 5 — Trend-aware coaching（3-4 周）**
- `NightlyPatternJob` 聚合 `(player, map, side, rule_id)` 跨 last 30 matches  firing  → "patterns you repeat" prefix。
- Milestone："your post-plant A-stack without checking cat has now fired 4 times in 12 matches"。

**Stage 6 — Real automated render（并行，看保留数据）**
- `real_cs2.py` adapter（HLAE + headless CS2 + ffmpeg）或老实 relabel "Generate Clip"。**只在用户保留数据显示 video review 是 dominate session path 时 build**——不要 speculatively build。

**本季度（90 天）执行序**：
1. Week 1-2：Stage 0 brand/rule-label 修复
2. Week 3-5：demo_service 分解 + `_common.py` helper lift + `list_demos` cap + 反范式 `video_status` 列
3. Week 6-7：reaper + dispatch 幂等 + 复合索引
4. Week 7-8：真实 `.dem` 集成测试 + Postgres-16 CI migration + MinIO S3 smoke
5. Week 8-10：mock 流水线 decouple + bomb-state fix
6. Week 11-13：`LocalStorageService` 删除 + `_SeekableStreamWindow` 删除

## 7. 保持不动的部分

- **auth 子系统整体**（`auth*` / `secure_downloader` / `steam_*`）：production-grade，经过对抗测试。**不要**让任何 launch blocker rewrite 碰它。限流基于 client IP 的 finding（`steam_auth_service.py:104`）real 但低 impact，defer。
- **storage 子系统 sha256+hmac.compare_digest 内容寻址 + quarantine→promote state machine**（storage.py:540, 1131, 1751）：是仓库最好的 artifact 安全设计，**保留 verbatim**。
- **replay_contract.py** 的 `_replay_diagnostics`（239-272）+ 结构化 `Replay*` dataclass：是所有子系统（parser/rules/frontend/future LLM）共享的最佳 artifact。扩展它，**不要绕开它**。
- **map_config.py** 6-map POI 配置：nav-mesh LOS 接入面积极小。
- **analyzer.py:23-41** 8-rule dispatcher（dedupe/cap/severity-sort）：是 LLM agent 想要的 curated-shortlist selector 的正确 shape。**不要再把它当产品；把它当 selector，让 agent 消费输出**。
- AGENTS.md 的"mock 阶段禁止 real auth/LLM/render"约束：单仓库最佳约束，保留。
- `_has_trade` 的 killer-position 机制**不是独立 bug**——是 trade predicate 的修复 shape，不要"修"位置检查，按 §3.1 修 predicate 整体。
- Hand-rolled migrations **non-transactional / no down path**：审计的修正结论对——orphan-crash 是 intentionally fail-closed（test_database_migrations.py:264-270），concurrent deploys serialize（ advisory lock 跨整个 init_db body 持有）。**不要**加 down-migration scope creep。

## 8. 开放问题（只能 Owner 决定）

1. **客户是谁、GTM 是什么？** 审计从未问。mock-only 默认 + 半成品 render + rules-only coaching 需要 GTM 故事；没有它，half 的 missing-capability 发现（trends/benchmarks/LLM narratives）没有优先级锚点。
2. **团队里有真正 CS2 教练领域专家吗？** "magic numbers with no CS2 grounding"（审计 low/maintainability）背后如果**没 expert 验证过**，这是 personnel gap 不是 calibration gap。
3. **是否愿意 commit 真实 `.dem` 到 fixtures？** `docs/sample_demo_fixture_v1_goal.md:7` 政策禁止 commit 大 `.dem`，使得真实 parse 测量在流程上不可能。这是 Process decision 值得挑战。
4. **Stale flag（STALE_PARSE_AFTER_SECONDS）要接 reaper 还是直接删？** 当前是"observability without remediation"——要么接线 reaper（§4.4），要么从 API 移除这个字段，别让 operator 以为被监控。
5. **`LocalStorageService` 一次折叠还是逐步淘汰？** 高价值维持性工作但 touches 每个 video-ingest 路径。建议在 demo_service 分解**之后**做（§4.1），让消费者只剩 `replay_blob.py` + `video_registry.py`。

## 来源（绝对路径锚定到已 verifying reads）

Subsystem 源码：
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/analysis/rules.py`（1122 行；_event 830-870；ReplayContext 680-716；_has_trade 788-833；find_isolated_entries 120-203；find_poor_spacing 206-289；find_post_plant_spread_issues 292-361；find_retake_desyncs 610+；find_untraded_deaths 57-114）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/analysis/analyzer.py`（41 行；analyze_replay 23-41 跑 8 detectors 然后 dedupe/cap）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/parser/demo_parser.py`（803 行；parse_ticks prop_sets 261；_build_frames 433-459 丢 Z；_build_kills 完全未测；_bomb_state 711-715 只返回 carried；_merge_players 402-430 只出 id/name/side；_build_bomb_events 585-607）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/parser/normalizer.py`（317 行；`source="mock"` hardcode 在 45；`url=None` 在 40）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/services/demo_service.py`（2581 行；list_demos 217-277 无 .limit() + per-row storage；rewrite-on-read 1843-1860 / _prepare_replay_video_update；orphan delete 868-873 在 commit-failure handler 外；dispatch_parse_job 486-506 无幂等；STALE_PARSE_AFTER_SECONDS 76）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/workers/worker.py`（271 行；process_render_clip_job 123-137 hard-fail；run_worker brpop 198；except Exception 214 抓不住 SIGINT/SIGTERM）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/services/storage.py`（2338 行；LocalStorageService 2087-2338；LocArtifactStore 506-1361；S3ArtifactStore 1411-end；sha256+hmac 540/1131/1751）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/core/config.py`（685 行；DEVELOPMENT_STEAM_CREDENTIAL_ENCRYPTION_KEY 21；production guards 289-290 / 500-505 / 637-641；database_url localhost 172）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/migrations/runner.py`（366 行；non-transactional upgrade 359；advisory lock 用 separate connection）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/core/database.py`（SCHEMA_UPGRADE_LOCK_ID + schema_upgrade_lock 50-69 on separate engine.connect()）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/services/demo_source_provider.py`（74 行；DisabledDemoSourceProvider 71；config gating 508-510）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/services/mock_replay_service.py`（_coaching_events 181-252 是 target triple；硬编码受害于 default path）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/app/services/upload_service.py:18`（ALLOWED_DEMO_UPLOAD_EXTENSIONS = {".dem", ".zip"}——**.zip shipped 真接受**，审计错）
- `/Users/jyxc-dz-0100616/Desktop/CS2/frontend/components/replay/FirstPersonReplay.tsx:188`（"Mock playback shell — not real CS2 video"——**UI 已诚实标注**，审计错）
- `/Users/jyxc-dz-0100616/Desktop/CS2/docker-compose.yml:34-40, 92, 147`（默认值 dev key + dev-render-worker-token + AUTH_COOKIE_SECURE=0 + AUTH_MODE=development——runtime 脚注，审计未充分评级）
- `/Users/jyxc-dz-0100616/Desktop/CS2/.env.example`（root:29 + backend:17 dev key；root:61 + backend:46 AUTH_COOKIE_SECURE=0；root:94 + backend:77 dev RENDER_WORKER_TOKEN）
- `/Users/jyxc-dz-0100616/Desktop/CS2/README.md:1,25,41`（"Rules-Based 2D Beta V1"、no-LLM 明示）
- `/Users/jyxc-dz-0100616/Desktop/CS2/CS2_DEMO_AI_COACH_IMPLEMENTATION_PLAN.md`（17KB；ai_generating 在 40 和 403 两处 stale）
- `/Users/jyxc-dz-0100616/Desktop/CS2/docs/rules_2d_beta_launch_v1.md:240-256`（冲突解决表； authoritative Phase-0 doc）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/tests/test_normalizer.py:62`（lock in `source=='mock'` mislabel on real-parser fixture）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/tests/test_demo_parser.py:164`（sys.modules swap 劫持 demoparser2）
- `/Users/jyxc-dz-0100616/Desktop/CS2/backend/Dockerfile`（无 render-worker source mount，verified）
- `/Users/jyxc-dz-0100616/Desktop/CS2/scripts/verify.sh`（21 行）/ `rc_check.sh`（106 行，存在但无 Actions CI）
- `.github/workflows/` 不存在（审计确认）

External（2026 年状态）：
- demoinfocs-golang v5（markus-wa/demoinfocs-golang）：CS2 生产级 Go parser；Player.ViewDirectionX/Y、GrenadeProjectile.TrajectoryEntry{Tick,FrameID,Time,Position}、events.Kill 含 PenetratedObjects/IsHeadShot/AssistedFlash/ThroughSmoke/Distance、events.BulletDamage（CS2-only，22/07/2024 更新后才存在）
- demoparser2 0.41.4（LaihoE/demoparser2）：Rust 核心，query-style，ticks/smoke_started/expired/flashbang_exploded/inferno_started/expired/round_announce_last_round_half/player_blind/weapon_fire
- awpy（pnxenopoulos/awpy）：基于 demoparser2，Python Polars，**有 nav visibility 计算**——接手本仓库 parser 的正确默认
- Leetify（免费+Premium ~$5-7/mo 未在 live 页核实）：HLTV 2.1、ADR、KAST、aim/HS、Personal Coach Premium 文本 tips（rule-based，非 LLM）
- Scope.gg（免费+Premium 价格未渲染）：ADR、TTK、HS%、flash effectiveness、map performance tips（heuristic）
- Refrag（$5.4-$79/mo verified 2026）：训练服务器 telemetry，不吃外部 demo
- Aimlabs（AI Powered coach，membership 价格未渲染）：task-based，非 demo-driven
- HLTV/BLAST：pro broadcast stats，非 consumer coaching
- 行业空白确认：**无 shipping 的 LLM-grounded CS2 demo 教练产品**——是本项目的真实楔子窗口

**Bottom line**：审计整体结构 sound、最强 finding 成立；但有**三条 material 错误**（`.zip` shipped 真接受；UI 已诚实标注 render skeleton；"dev-only owner harness"被审计自己的 auth 修正推翻）和**两条 under-rating**（docker-compose runtime dev-key 默认应升级到 medium/security；post-commit orphan 在内容寻址 blob 上是 data-consistency  hole 应升到 high/data-loss）需在出版前纠正。规则引擎本身**可救**（修 trade/isolation/post-plant 三条 + parser 复活 bomb-state），但**上游 parser/data foundation 是真正的 blocker**——零真实 .dem、2D-only telemetry、无 aim/econ/utility-landing。Auth/storage 是仓库**最成熟、最不该重写**的部分。

---

## 生成信息（provenance）

本报告由 `cs2-coach-audit` 多智能体只读审计工作流产出，未改动仓库任何文件。

| 项 | 值 |
|---|---|
| 运行 ID | `wf_4bd7cdb3-3e3` |
| 完成时间 | 2026-08-07T18:39:20.714Z |
| 审计基线 commit | `bb378f5` (`main`, 审计时的 HEAD) |
| 子智能体数 | 107 |
| 总 token | 5,851,876 |
| 运行时长 | 2.69 小时 |
| 读取子系统 | 10 |
| findings 经对抗验证保留 | 69 |
| findings 被证伪剔除 | 16 |
| 外部研究报告 | 4 |
| gap 分析视角 | 4 |

报告正文为工作流原始输出，未经人工编辑。所有 `file:line` 引用锚定到基线 `bb378f5`,
后续 commit 可能造成行号漂移。
