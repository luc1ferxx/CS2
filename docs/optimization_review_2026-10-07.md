# 项目优化审查（2026-10-07）

> **处理状态（2026-10-07）**：第 2 节（立即做，2.1–2.5g）已全部完成并随本报告一起提交；第 3 节（内测前，3.1–3.8d）已在随后的"部署工具链"改动中完成，第 7 节第 1、2 条（部署预检阶段、每次解析的峰值内存）随之落地；第 4 节（内测后）尚未处理；第 5 节为已否决项，不处理。
> 第 3 节仍留的尾巴：(1) 3.6 第 (2) 步只做了解析子进程的独立 `parser` 账户，api 和 worker 容器本身仍以 root 运行（要先迁移命名卷属主，记为后续）；(2) 告警地址、healthchecks.io 式 dead-man 检查和外部拨测要站长在 VPS 上自己配置；(3) 只能在 VPS 上跑的路径（对 Postgres 的迁移预检、worker 95 秒稳定等待、镜像打标签与清理、`backup.sh --db-only` 的真实 dump）只用合成输入在本机验证过，第一次真实部署时要盯一遍；(4) 真实 demo 清单只在本地 `verify.sh` 和 `rc_check.sh` 里比对，CI 没有 `.dem`。

- 基线：`main` @ `f1ad41f`（2026-10-04），工作树干净；除本报告外未改动任何文件。
- 方法：先在本机跑完整验证门禁并测量真实 demo 解析；再用 10 个维度的审查智能体各自读代码提出候选，每条候选由 2 名独立复核者分别从"事实/边界/是否已知"和"对内测阶段的价值"两个角度试图推翻，意见分歧时第三名裁决；最后由一名"完整性批评者"查漏并补充候选，补充项同样复核。共 172 个子智能体（全部 Opus 5.5），64 条候选，37 条通过（合并重复后 32 条），27 条被否决（见第五节）。
- 每条通过项都带 `文件:行号`（HEAD `f1ad41f`），并标注：**新** = 任何文档都没记录；**已知需提前** = README/旧审计已提过，但这次有新的证据或更便宜的做法。
- 这是评估，不是改动。按"立即 / 内测前 / 内测后"分组，组内按影响 × 省力排序。

## 0. 结论先行

1. **代码健康状况很好。** 门禁全绿；应用层安全（cookie、CSRF、Origin、私有媒体、SSRF、Steam OpenID）复核后没有发现问题；前端每帧渲染路径完全符合 AGENTS.md 自己定的规则（二分查找、按回合索引、memo、稳定回调）；旧审计（2026-08-07）里的多数"必须先修"项已经修掉（见第六节）。
2. **只有一条高影响问题**：`GET /demos` 为每一行已完成的比赛读取并重新归一化整份 19 MiB 回放（约 0.35 s/场，生产 R2 上还要加 3 次 HEAD + 1 次 GET），而比赛库在有任务进行时每 1.8 s 轮询一次。旧审计提过（§4.3），但当时的方案要加迁移；现在 `replay_response_cache` 里已经有按不可变 key 的 video memo，复用它是约 15 行的 S 级改动。
3. **其余问题集中在三处**：（a）部署/恢复工具链（回滚跨迁移必崩、健康检查看不到 worker、零告警、镜像不打标签、调优变量没透传）；（b）内测成功指标的采集面（评价提示被隐藏、会话 1 小时强制过期打断复盘）；（c）文档与代码漂移（AGENTS.md 把已上线的 Steam 会话写成"禁止添加"，README 两处与 HEAD 不符）。
4. **已否决但值得知道**：rules.py 拆包、Demo Detail 页面拆分、列式帧编码、浏览器端 ETag/304、什么都加告警的 Prometheus 方案，都在当前"一台 VPS、几位朋友"的规模下被判定为收益不足或方案本身有误（第五节给出理由）。

## 1. 本机测量（判断依据）

| 项目 | 数值 |
| --- | --- |
| 后端 unittest | 1152 通过（25 skipped），97 s |
| render-worker unittest | 54 通过，1 s |
| ruff / mypy | 通过 / 通过（112 文件，7 模块 ignore_errors） |
| 前端 lint / tsc / Vitest / build | 20 s / 3 s / 39 文件 423 通过 20 s / 19 s |
| First Load JS | `/dashboard` 139 kB，`/demos/[demoId]` 205 kB（页面 77.9 kB） |
| 真实 demo（Ancient，276 MB，16 回合） | parse 7.8 s，normalize 2.0 s，analyze 0.7 s，134 条建议 |
| 回放 JSON | 19.0 MiB 原始（frames 16.1 / playerStates 0.94 / utility 0.73 / inputs 0.61 / events 0.44），gzip 1.3 MiB |
| 帧采样 | 每 16 tick（0.25 s）一帧 + 事件 tick 补帧；每帧每名玩家重复携带 `id`、`name`、`side`；`z` 未取整（17 位小数），`x/y` 为 2 位 |
| 若改列式帧编码（仅测算） | frames 16.1 → 5.6 MiB 原始，gzip 0.81 → 0.57 MiB |

建议数分布（Ancient）：untraded_death 61、poor_spacing 29、no_counter_strafe 17、moving_shots 13、retake_desync 8、weak_utility_before_execute 6；isolated_entry 和 post-plant 规则 0 条；没有 high 严重度。

## 2. 立即做（S 级，收益直接，互不依赖）

### 2.1 比赛库每行读整份回放 — **高影响，已知需提前**

- `backend/app/services/demo_service/replay_blob.py:159`：`get_video_status` 调 `load_replay_blob`，即完整读取 artifact、SHA-256、`json.loads`、`normalize_replay_contract`。
- 调用方：`demo_library.py:80`（每一行）、`render_lifecycle.py:302`（`/render/jobs`，渲染中每 3 s 轮询）、`demos.py:273`（`/video`）、`video_registry.py:153`（每个媒体 Range 请求，即每次视频 seek）。
- 实测：5 场已完成比赛的 sqlite + 本地存储，`list_demos` 1.2–1.8 s/次；把这次读取 stub 掉后 5 ms。比赛库在有解析/渲染任务时每 1.8 s 轮询（`frontend/app/dashboard/page.tsx:280`），单进程 uvicorn 会被占满。生产用 R2（`deploy/env.production.example:64-72`）时每行至少 3 次 HEAD + 1 次 19 MiB GET。
- 做法：在 `get_video_status` 里，当 `replay_storage_key` 通过 `is_cacheable_replay_key`（不可变 `artifact://`）时，命中则返回 `replay_response_cache.video_for(key)` 的 deepcopy，未命中则加载一次后 `remember_video(...)`（`replay_response_cache.py:208-224`，已有 4096 上限、删除驱逐、启动预热）。`local://` 旧 key 保持原路径。在 `test_demo_library.py` 加一条测试：两次 `list_demos` 对每场至多触发一次 `load_replay_blob`。注意延迟导入避免循环引用；写路径（`prepare_replay_video_update`）保持加锁的权威加载。
- 可选后续（M）：旧审计的反规范化 `video_status` 列，让冷启动也便宜。

### 2.2 公开回放投影：`z` 取整 + 删掉重复的 kills/deaths — **中影响，新**

- `backend/app/parser/normalizer.py:340`：`{"z": float(value)}` 原样输出 demoparser2 的 double；`x/y` 已取整 2 位，道具 `z` 已取整 0.1（`utility_tracks.py:723`）。玩家 z 占 frames 原始体积约 2.2 MiB，高熵导致 gzip 压不动。
- `backend/app/services/demo_service/projection.py:80-89`：`kills` 和 `deaths` 是同一份列表（`demo_parser.py:204` 设 `"deaths": kills`）输出两遍；`types/replay.ts` 未声明、前端从 `events` 算击杀、`api_reference_v1.md:300` 也没列。
- 实测（当前 v5 回放，与第 1 节同一基线）：仅 z 取整到 0.1，回放从 18.98 → 17.64 MiB 原始，gzip（level 5，回放缓存所用级别）1.337 → 1.079 MiB，即 **-19%**；64 MiB 回放缓存可多装约 1/4 场次。再去掉 kills/deaths 的额外收益是在一份 v1 QA 回放（17.4 MB / 956 KB gzip）上测的：两项合计 956 → 687 KB（-28%），绝对节省约 270 KB，可叠加到 v5 上但比例不能直接相加。
- 做法：只改投影（`_public_replay_frame` 的 players 与 bombState、`_public_replay_event` 的 z 取 1 位小数；`_public_replay_contract` 去掉 kills/deaths），bump `PUBLIC_REPLAY_CACHE_VERSION`，更新投影测试与 API 参考。不动存储 blob 和分析器输入，无需 `REPLAY_CONTRACT_VERSION` / `COACHING_RULES_VERSION`；旧比赛不重解析即受益。`_world_z` 的存储侧取整是可选项，因为 `rules.py:1418/1915` 读 z 做高度判断，要先对 4 场本地 demo 比对建议输出。

### 2.3 会话固定 1 小时过期，到期整页卸载 — **中影响，新**

- `backend/app/core/config.py:181` 默认 3600，`deploy/env.production.example:55` 也是 3600；`auth_service.py:173-231` SETEX + cookie Max-Age 一次写入，从不续期。任意一次 401（评价点击、轮询）让 `AuthBoundary.tsx:16-19` 用「登录已过期」卡片替换整个复盘页，已加载的回放和乐观评价丢失，用户被送回 steamcommunity.com（国内直连常不可达）。
- 系统其他部分已按长会话设计：Caddy 给上传 2 h、`demo-upload.ts:272` 有"登录已过期，上传已暂停"文案、`OWNER_REVOCATION_TTL_SECONDS = 86_400 + 300`、每个请求都重查邀请名单。
- 做法（S，仅配置）：`AUTH_SESSION_TTL_SECONDS` 设 43200–86400，并同步 `frontend/app/privacy/page.tsx:74,105`、`README.md:216,223`、`configuration_reference_v1.md:53`（AGENTS.md 要求同一改动内更新）。更好（S–M）：在 `SessionCsrfMiddleware` 里做滑动续期（剩余不足一半且 `issuedAt` < 24 h 时重新 SETEX 并刷新 cookie，`issuedAt` 不变以保住撤销逻辑），加续期/绝对上限/撤销三条测试。可选：Demo Detail 收到 401 时显示内联重新登录条而不是卸载页面。

### 2.4 评价提示被隐藏，三个评价按钮无解释 — **中影响，新（关系内测唯一成功指标）**

- `frontend/components/coaching/CoachingPanel.tsx:205`：`已评价 x/n` 只在 `.visually-hidden` 里；提交 d0c1984 同时删掉了可见的「对你有帮助吗」，`product.css:459` 的 `.coaching-feedback > span:first-child` 现在匹配不到任何元素。三个按钮是无边框的 `--ink-3` 灰字（`CoachingEventCard.tsx:143-157`），没有 title 说明「无关」（事实没错但不值得复盘）与「判断不足」（证据不够）的区别，这个区别只在 `docs/coaching_feedback_v1.md:11-13`，而该文档 30-31 行还说提示和进度是可见的。测试用 `getByText('已评价 0/7')` 无法区分可见性。
- 这是你在 d0c1984 / 19b512d 里有意做的"安静"设计，所以建议折中而不是回退：保持安静样式，但在工具栏放一个紧凑的 `已评价 x/n`（或"重点 5 条已评 a/5"），给三个按钮加 `title`/`aria-description` 写上两条定义，让按钮对比度够读成控件；测试改为断言不在 `.visually-hidden` 内；同步更新 `coaching_feedback_v1.md:30-31`。

### 2.5 其余 S 级

| # | 问题 | 位置 | 做法 |
| --- | --- | --- | --- |
| 2.5a | 前端运行时镜像用 Node 20（2026-04-30 EOL），CI 用 Node 22；镜像以 root 运行且含全部 devDependencies | `frontend/Dockerfile.preview:1`、`frontend/Dockerfile:1` | 先改成 `node:22-alpine`（两行，`npm run build` 校验）；multi-stage + `output: "standalone"` + `USER node` 作为后续可选 |
| 2.5b | README 两处与 HEAD 不符：声称浏览器 304 复验（实际 `private, no-store`，前端 `cache: "no-store"` 且不发 If-None-Match）；声称 Mirage/Inferno/Ancient/Anubis 手估（ff6759f 起六张图全部 `calibrated: True`） | `README.md:29`、`README.md:236` | 两行改写；这条过时说法已经扩散到本次审查的"已知限制"清单里 |
| 2.5c | AGENTS.md 把已上线的生产认证写成未来工作，禁止添加会话/OAuth；第 5 行仍称"mock MVP"；第 164 行引用已不存在的 `services/storage.py` | `AGENTS.md:5,142,164,176` | 改写成与 HEAD 一致的"认证边界"段：开发用 `DEV_USER_ID`/`X-Dev-User-Id`，生产用 Steam OpenID 会话 + `STEAM_LOGIN_ALLOWLIST`，不加密码登录或其他 IdP |
| 2.5d | AGENTS.md 验证命令缺 ruff/mypy（CI 两个必过 job），且 14 处用 Windows 上不存在的 `python3`；可用的 Windows 配方只在 git 排除的 `.claude/skills/cs2-verify` | `AGENTS.md:62,91` | 第 62 行写明 `verify.sh` 镜像 ci.yml 含 ruff/mypy 并自选 venv；定义 `$PY`，把本地 skill 里的分表搬进来 |
| 2.5e | 射击类建议卡显示英文武器名（后端 `SHOT_WEAPON_LABELS`），同一面板的阵亡卡用中文表；Deagle/FAMAS/Galil/R8 两表不一致 | `frontend/lib/coaching-review.ts:588,163`；`rules.py:112-126` | 前端统一用 `weaponName(context.weapon) ?? factText(context.weaponLabel)`；补 deagle/famas/galilar 用例；无需规则版本 bump |
| 2.5f | 时间轴 low/info 标记 `#77663a` 对 `#14273a` 面板为 2.7:1，低于 WCAG 1.4.11 的 3:1；标记是可聚焦按钮，填充色是唯一边界 | `frontend/app/product.css:955`、`globals.css:49` | 低严重度标记单独用 `#8a7642`（3.44:1），或统一用 `--accent` 以高度区分严重度；不要改 `--accent-line` 本身（还画 chip 边框） |
| 2.5g | 死代码：`RoundSelector.tsx`（英文文案，d0ab758 起无引用）、`playbackActionLabel`/`PLAYBACK_LABELS`（demo-library.ts:528-536）、`coachingLocation`/`coachingRuleLabel`/`roundClock`（只被自己的测试引用，且时钟格式 `00:11` 与 `formatRoundTime` 的 `0:11` 不一致）、后端 `demo_upload_path`/`video_upload_path`（upload_service.py:78-85） | 见左 | 删除约 150 行连同测试；保留 `createDemoUpload` 和 `/uploads/demo`（curl 用） |

## 3. 内测前（对应 README 路线图第 1 项：部署/回滚、监控、备份、解析器隔离）

### 3.1 跨迁移回滚必定崩溃，而文档说"一般能忽略" — **中影响，已知需提前**

- `backend/app/migrations/runner.py:532-537` 遇到数据库里有未知迁移版本就抛 `RuntimeError`（有意为之，`test_database_migrations.py:294` 固定）；`core/database.py:42` 只重试 `OperationalError`，所以 API 和 worker 启动即死，`restart: unless-stopped` 变成崩溃循环。
- `scripts/deploy/deploy.sh:72` 写"may fail"，`docs/vps_deploy_v1.md:242` 写"旧代码一般能忽略多出来的表和列"，`:229` 把部署前 dump 标为"可选"。f1ad41f 本身就加了迁移 2026100401，09-26 到 10-04 共 3 个迁移。部署后的夜间备份已含新版本，所以唯一可用的恢复点是那份可能被跳过的部署前 dump。
- 做法（约 40 行 bash）：(1) 回滚预检：`git checkout` 前比对 `select version from app_schema_migrations` 与目标提交的 `runner.py` 里的版本号，数据库有目标不认识的版本就在动服务前退出并给出"先 `restore.sh --into-production` 再回滚，或向前修复"的提示；(2) 部署模式：拉下来的 `runner.py` 新增版本时，`compose up` 前自动跑 `backup.sh`，把 dump 名写进 `.deploy-history`；(3) `next_step()` 在本次应用了迁移时打印"先恢复再回滚"而不是裸 `--rollback`；(4) 修正 deploy.sh:8-11/72 与 vps_deploy_v1.md:229/242/263。
- 关联：批评者指出被否决的"备份只查目录"候选中"部署前 dump"这一部分应随本条一起回来。

### 3.2 生产健康检查看不到解析 worker — **中影响，新**

- `backend/app/main.py:182-186` 的 `/health` 只查 DB、Redis 和几个配置值；worker 心跳（Redis key，TTL 120 s）只有 `/diagnostics` 报告，而它在生产返回 404（`diagnostics.py:218-219`）。worker 服务没有 compose healthcheck；`deploy.sh:120` 只等 `/health`；`prod_smoke.sh` 只做匿名检查；`vps_deploy_v1.md:259` 推荐的外部探针也只探 `/health`。
- 后果：worker 导入失败/OOM 循环/挂起时，部署照样打印 Deployed，探针保持绿色，所有上传永远 queued（重派发和回收都在 worker 里跑）。
- 做法：加生产可用的 `GET /health/worker`（心跳 alive → 200，否则 503，和 `/health` 一样粗粒度）；加进 `deploy/Caddyfile:67` 的 `@api` 路径列表；deploy.sh 在 `/health` 之后再等 `/health/worker` 最多 60 s，失败打印 `compose logs --tail 60 worker`；prod_smoke.sh 同样检查；worker 加 compose healthcheck（检查心跳 key 存在）；外部探针同时探两个。不要把 worker 并进 `/health`：caddy/frontend 的 `depends_on` 依赖它，否则 worker 挂会把整站拖下。

### 3.3 完全没有告警通道 — **中影响，已知需提前**

- 静默失败清单：所有服务 `restart: unless-stopped` 下的重启循环；`BACKUP FAILED` 只进 journald；磁盘被 Postgres + Redis AOF + upload-staging（最多 6 GiB）+ 镜像 + 14 份 dump 共用但无人看；worker 成功日志 `Completed job X`（`worker.py:719`）无耗时，失败日志只有异常类名（`:787`），`PARSE_OUT_OF_MEMORY`/`PARSE_TIMED_OUT`/`STORAGE_READ_FAILED` 在 `dc logs` 里长一样。
- 做法（最小可用，不上 Prometheus）：(1) `scripts/deploy/watch.sh` + systemd timer 每 5 分钟：探 `/health` 与 `/health/worker`、比对各容器 `RestartCount`、`df` 超 85%、最新 `pg-*.dump` 超 26 h；状态变化时向 `ALERT_WEBHOOK_URL`（ntfy / Telegram / Server酱）发一行，并 ping 一个 healthchecks.io 式的 dead-man URL；(2) worker 每个任务结束输出一行 JSON 日志（`event, jobId, type, outcome, errorCode, attempt, sourceBytes, downloadS, parseS, normalizeS, analyzeS, coachingEvents, peakRssMiB`），只含不透明 id，不含文件名/owner/路径；(3) `env.production.example` 与 `vps_deploy_v1.md` 补说明。
- 价值复核的反对意见值得记下：若只想花 1 小时，先做 `backup.sh:136` 后面加一行 `curl $BACKUP_PING_URL` 和外部 `/health` 探针，能拿到约一半价值。

### 3.4 镜像不打标签、从不清理，回滚要重新构建且依赖不可复现 — **中影响，新**

- `scripts/deploy/deploy.sh:100` 在部署和 `--rollback` 两种模式都执行 `compose build --pull`；三个服务没有 `image:` 标签，所以每次构建覆盖上一份镜像；`scripts/deploy/` 没有任何 `docker image prune`/`builder prune`。前端镜像单阶段含 493 MB `node_modules` + 245 MB `.next`。
- `backend/Dockerfile:9` 用无锁、无哈希的 `requirements.txt`；demoparser2 0.42.0 对 numpy/polars/pyarrow 不设上界，本机 venv 已解析到 numpy 2.5.3。回滚 = 用今天的依赖重建旧代码，恰好在需要已知良好状态时可能失败；同一提交两次部署解析行为也可能悄悄不同。
- 做法：(1) `uv pip compile --generate-hashes` 生成 `requirements.lock`，Dockerfile 与 CI 都 `--require-hashes` 安装（S，先做）；(2) 镜像按提交打标签 `cs2coach-backend:${DEPLOY_SHA}`（api/worker 共用一份）、`cs2coach-frontend:${DEPLOY_SHA}`，`--rollback <sha>` 两个标签都在时跳过构建直接 `up -d`；(3) 冒烟通过后保留最近 3 个 SHA，`docker image prune -f` + `builder prune --keep-storage 5GB`；配合 3.3 的磁盘检查。

### 3.5 19 个调优变量从未透传进容器；worker 无内存上限 — **低影响，新（但与 3.3/3.7 相关）**

- 对比 `config.py` 全部 `os.getenv` 名与三个 compose 文件：`PARSE_TIMEOUT_SECONDS`、`PARSE_MEMORY_LIMIT_BYTES`、`PARSE_LEASE_*`、`PARSE_RECLAIM/REDISPATCH_*`、`PARSE_MAX_ATTEMPTS`、`REPLAY_UPGRADE_*`、`COACHING_RECOMPUTE_*`、`REPLAY_RESPONSE_CACHE_MB`、`REPLAY_WARM_*`、`REPLAY_READY_CHANNEL`、`RENDER_CLIP_QUEUE_TIMEOUT_SECONDS/SINGLE_CONSUMER` 不在任何 `environment:` 块，也没有 `env_file:`；`--env-file` 只喂 `${}` 插值，所以写进 `.env.production` 静默无效（`docker-compose.yml:136-167`）。`configuration_reference_v1.md:118-126` 却把它们当 worker 旋钮介绍。无服务设 `mem_limit`，4 GiB 的 RLIMIT_DATA 上限在 4 GB VPS 上等于没有，大解析会让整机 swap。
- 做法：compose 里加 `x-worker-tuning` 锚点透传这些变量（用代码默认值）；`docker-compose.prod.yml` 给 worker 设 `mem_limit`/`memswap_limit`（如 `${WORKER_MEM_LIMIT:-3g}`），让超限在 worker cgroup 内被 SIGKILL 并记成 `PARSE_OUT_OF_MEMORY`；`env.production.example` 加 VPS 规格注释块；加一条解析 compose YAML 的单元测试，`config.py` 的 env 名既没透传也不在白名单就失败。

### 3.6 解析子进程继承 worker 全部密钥并以 root 运行 — **中影响，已知需提前**

- `backend/app/workers/worker.py:137,160` 和 `match_summary_backfill.py:61,86` 把 `dict(os.environ)` 原样传给子进程：含 `DATABASE_URL`（生产真实密码）、`OBJECT_STORAGE_SECRET_ACCESS_KEY`、`RENDER_WORKER_TOKEN`；`backend/Dockerfile` 无 `USER`。demoparser2 是读取不可信字节的原生 Rust 扩展。已验证 `env -i PATH=... PYTHONPATH=. python -c 'import app.workers.parse_child'` 能干净导入，`app/parser` 下无任何代码读 settings 或 os.environ。README:249 只列了 CPU/磁盘/输出限制，没提密钥继承和 root。
- 做法：(1, S) 子进程环境改白名单（PATH、PYTHONPATH、OPENBLAS_NUM_THREADS、LANG、TMPDIR、HOME），加测试断言没有 `OBJECT_STORAGE_*/DATABASE_*/RENDER_*/STEAM_*/REDIS_*` 到达 Popen；(2, M) `Popen(..., user='parser', group='parser', extra_groups=[])` 以独立低权限 uid 运行，先 chown 工作区并让源文件可读；api 的 Dockerfile 也加 `USER`，一次性 chown 命名卷。价值复核的保留意见：只做 (1) 挡不住同 uid 的 RCE 读 `/proc/<ppid>/environ`，所以 (1) 是减少暴露面，(2) 才是隔离。

### 3.7 Redis 无密码 + 扁平网络 — **低影响，新**

- 任何 compose 文件都没有 `networks:`；`docker-compose.prod.yml:53` 的 Redis 只有 `--appendonly yes`。会话是 `setex(sha256(token), JSON{ownerId, steamId, issuedAt})`（`auth_service.py:200-203`），未签名，所以能写 Redis 就能为任意用户造一个永不过期、`issuedAt` 在未来（绕过 `revoke_owner_sessions`）的会话。面向公网的 Next.js 容器（已核实无服务端 API 调用）和 3.6 的解析子进程都能直连 Redis。
- 做法：prod 加 `backend` 网络（`internal: true`）只放 postgres/redis；api/worker 同时接 `backend` 与默认网络（需要出站到 R2/Steam）；caddy/frontend 只在默认网络；Redis 加 `--requirepass`（新 `REDIS_PASSWORD`），生产配置校验拒绝空密码；不把密码传给解析子进程。

### 3.8 其余内测前项

| # | 问题 | 位置 | 做法 |
| --- | --- | --- | --- |
| 3.8a | 内存超限被报成"解析程序意外中断"并提示重试（重试必然同样失败）；没有任何地方记录一次解析的峰值内存 | `worker.py:230`（只把 -9 映射到 `PARSE_OUT_OF_MEMORY`，demoparser2 分配失败是 SIGABRT -6）；`parse_child.py:107` 注释仍说 RLIMIT_AS | 子进程 stderr 重定向到工作区文件，退出后读尾部 2 KiB 回放到 worker 日志；-6 且尾部含 Rust 的 `memory allocation of` 时归为 OOM；用 `os.wait4` 的 `ru_maxrss` 记峰值到任务 metadata 和 3.3 的 `job_done` 行；补 -6 的 POSIX 测试。3.5 要选 `PARSE_MEMORY_LIMIT_BYTES` 的值全靠这个数据 |
| 3.8b | 没有测试跑过 `parse_child.main` 或 worker→子进程的 argv 契约；两处手拼 argv（`worker.py:148-157`、`match_summary_backfill.py:72-85`） | `backend/app/workers/parse_child.py:76` | 新增 `test_parse_child_process.py`：真实 `run_parse_subprocess`（不 patch Popen）喂 16 字节垃圾文件应得到子进程自己的分类码而不是 `PARSER_UNEXPECTED`；4 字节文件应 `INVALID_DEMO`；team-names 模式走 exit-3 路径；子进程导入链不得带入 sqlalchemy/redis。实测一次往返 0.11 s |
| 3.8c | 唯一的真实 demo 测试 `REPLAY_V2_SAMPLE_CHECK=1` 没有任何命令会打开，且只查 v2–v5 附加字段；两处解析器降级（`_parse_tick_records` 8 级 prop 回退、事件丢失玩家位置）静默不报——复核者在 Ancient 上实测把 player_death/hurt 强制走空 prop 变体，154 次击杀 + 501 次伤害全部丢失 x/y，现有测试和分析器输出照样通过 | `backend/tests/test_sample_demo_contract_v2.py:52` | 提交 `backend/tests/fixtures/real_demo_manifest.json`（按文件大小 + sha256 前缀键入，只存聚合数：地图、回合/帧/事件数、各事件族计数、道具/状态/按键/射击计数、每规则建议数、规范化分析输出的 sha256；不含内容，符合 `sample_demo_fixture_v1.md:16`）；`verify.sh` 发现仓库根有 `*.dem` 就自动跑（约 10 s/场）；`rc_check.sh` 的 `SAMPLE_DEMO_PATH` 分支也跑；AGENTS.md 写明 demoparser2 升级、解析/归一化改动、规则版本 bump 要重新生成 manifest。已验证 `PYTHONHASHSEED` 1 和 2 输出逐字节一致 |
| 3.8d | CI 没有 shell/compose 校验：`scripts/deploy/` 约 880 行 bash 只在生产 VPS 上跑，带 shellcheck 指令却无人执行；生产 compose 形状（三文件叠加）在 5 次提交里改了 4 次且从未真实部署 | `.github/workflows/ci.yml:150` | 加独立 job：`bash -n` + `shellcheck -x scripts/*.sh scripts/deploy/*.sh`（已有警告记入 `.shellcheckrc`）+ `docker compose --env-file deploy/env.production.example -f ... config -q`；`verify.sh` 镜像为可跳过步骤。本机已确认全部脚本 `bash -n` 通过，门禁起步即绿 |

## 4. 内测后 / 视数据再定

| # | 问题 | 位置 | 做法 / 为什么不急 |
| --- | --- | --- | --- |
| 4.1 | `package.json` 锁 React 18.3.1，但 Next 15 App Router 每个页面实际运行 vendored React 19.2 canary（已在 `.next/static/chunks/4bd1b696-*.js` 核实）；423 条 Vitest 跑在另一个 React 大版本上 | `frontend/package.json:17` | 升到 react/react-dom/@types 19（迁移面：0 个裸 `useRef()`，1 处 `JSX.Element`）。复核者已用 vendored 19.2 跑完全部测试无差异，所以今天不藏问题，只是对齐 |
| 4.2 | 无依赖安全通告监控；`fastapi==0.115.6` 把 Starlette 封在 0.41.x；ESLint 8 自 2024-10 EOL | `backend/requirements.txt:1`、`package.json:29` | 先在 GitHub 仓库设置里打开 Dependabot alerts（零文件），再考虑 `.github/dependabot.yml` 与非阻塞 `pip-audit`/`npm audit` job；安排一次 FastAPI 升级（97 s 后端套件兜底）。具体 CVE 需联网确认 |
| 4.3 | 前端请求无超时、不可中止：一个挂起的请求让 `usePoll` 永久停住（`use-poll.ts:46-55` 的 `running` 守卫），启动回放包挂起时页面停在「正在载入回放…」骨架且无重试；离开页面不取消 1.3 MiB 下载与 19 MiB 解析 | `frontend/lib/api.ts:171` | `AbortSignal.any([init.signal, AbortSignal.timeout(ms)])`（状态/列表 20 s、回放 90 s、变更 30 s），`TimeoutError` 归为 network 走现有退避与「重新载入」；启动 effect 用 AbortController。README 已知的"Redis 客户端无超时"是后端一半，这是前端一半 |
| 4.4 | `/auth/me` 任意非 401 失败（含部署重启时 Caddy 的 502）都显示「网络连接中断，请检查网络」，不自动重试；退出登录失败也会把有效会话替换成同一屏 | `frontend/components/auth/AuthProvider.tsx:57`、`AuthBoundary.tsx:94-97`、`DemoLoadState.tsx:98` | 按 `requestFailureKind` 选文案（5xx 用「服务暂时不可用（可能正在更新），正在自动重试…」），error 态下用 `usePoll` 2/5/15/30 s 退避并监听 `online`；退出失败保持已登录态并内联报错 |
| 4.5 | 没有面板级错误边界：任一可选面板抛错就卸载整个复盘页；`demos/[demoId]/error.tsx` 丢掉 `error.digest` 且没有页脚（违反"每页都有隐私说明链接"） | `frontend/app/demos/[demoId]/error.tsx:9,31` | 先修 error.tsx（加 `SiteFooter`、显示错误代码）；面板边界只包真正在面板内读数据的组件（UtilityFinder、ThrowAnalysis、KeyboardOverlay、ClipLibrary、RenderOperatorPanel），EconomyPanel/Scoreboard 的数据在页面级 useMemo 里，包了也没用 |
| 4.6 | 单字符快捷键 k/n/p/[/]/? 全局生效且无法关闭（WCAG 2.1.4 A 级） | `frontend/app/demos/[demoId]/page.tsx:1007-1022` | 在 `?` 面板加「启用单键快捷键」开关存 localStorage，或只在焦点位于复盘区/时间轴时响应字母键 |
| 4.7 | 配置参考与 API 参考自称"完整清单"但已漂移：缺 `MEDIA_URL_BASE`、`RENDER_CLIP_SINGLE_CONSUMER`（默认开，第二台渲染机会偷走第一台的任务，任何文档都没提）；缺 `GET /render/worker`（前端 `api.ts:391` 在调）；根 `.env.example` 缺 15 个设置 | `docs/configuration_reference_v1.md:3`、`docs/api_reference_v1.md:3` | `backend/tests/test_docs_sync.py`（<1 s）：正则抽 `config.py` 的 env 名断言都在参考与 `.env.example`；导入 `app.main` 断言每个 `APIRoute` 路径都在 API 参考；然后修掉当前漂移 |
| 4.8 | README 当作"上线门槛"和"进展"链接的两份文档落后约 50 个提交：上线计划的"当前仓库事实"列仍写模拟认证、本地存储、无租约、无备份；`project_status_2026-09-13` 写 487 条测试（现 1152）、把一键启动当主路径 | `docs/rules_2d_beta_launch_v1.md:93`、`docs/project_status_2026-09-13.md:29` | 上线计划的 Production Gaps 表加 `Status @ <sha>` 列（done/partial/open + 链接）；README:9 的"进展"改指 README 路线图锚点；状态文档去掉会随每次提交过期的测试计数。注意上线计划自述是冻结的 Phase 0 记录（基线 235371e），所以加状态列比改正文合适 |
| 4.9 | AGENTS.md 41.9 KB（约 1 万 token）每会话注入：命令区 13.6 KB 基本重复 `api_reference_v1.md`；Product Direction 20.5 KB 约 25 段无标题，6 段超 1000 字符；第 148 行一段 2373 字符的 v2–v5 契约历史与 api_reference 298-408 重复；"永不违反"的不变量埋在版本叙述里 | `AGENTS.md:148` | 开头加 10–12 行「Never break」清单；Product Direction 加 `###` 小标题；逐版本契约历史挪到 api_reference 并留链接；加一条"新细节写到哪"的规则防再膨胀。两个必须留在 AGENTS.md 的东西：DELETE 安全警告（第 35/45 行，保护本地真实 demo）、cs2-verify 的本地配方。复核者另提醒：Codex 默认只读约 32 KiB 的 AGENTS.md（未在本机确认），超出部分可能被截断 |
| 4.10 | 前端回放 fixture 全部手写（`replay-v2.ts:1` 仍写"until real v2 replays exist"，契约已到 v5）；没有一份后端生成的公开投影被两侧测试；v2–v5 字段靠"缺失就隐藏"的设计，键名改了前端功能会无声消失（mock demo 没有 inputs/throwOrigin，冒烟脚本不查这些字段） | `frontend/lib/test-fixtures/replay-v2.ts:1` | 复用 `fixtures/match-rules` 的双侧模式：后端测试用合成解析输出跑 normalize → `_public_replay_contract`，和提交的 `fixtures/replay-contract/public-v5.json` 比对（带 `--update`）；前端 node 测试加载同一份 JSON 跑 player-state/utility/throw-analysis/inputs/diagnostics 各 helper 断言非空；类型化 import 让 `tsc` 抓类型漂移 |
| 4.11 | S3 存储后端 import 本地后端调用其私有静态方法 6 处（`_validate_write_limits`/`_normalize_now`/`_normalize_range`），封顶摘要循环几乎逐字重复 | `backend/app/services/storage/s3.py:38,155,161,260,356,461,697` | 三个 helper 挪到 `_boundary.py`（两者已共同继承 `_ArtifactReferenceBoundary`），加 `_consume_capped(stream, chunk_size, max_bytes, sink)`；约 40 行净改动，现有存储测试覆盖。纯整洁，用户无感 |
| 4.12 | 解析器对小事件族做约 17 次完整 demo 扫描（`_parse_event_records` 14 次 + utility_tracks 的 expire 事件），每次 0.13–0.15 s，合计约 2.6 s（占 7.8 s 解析的 1/3，比全部 `parse_ticks` 0.96 s 还多）；一次 `parse_events(17 个名字, 并集 props)` 0.14 s 得到相同行 | `backend/app/parser/demo_parser.py:69-145`、`utility_tracks.py:88-90` | 加 `_parse_event_batch`，异常时回退到现有逐事件链（测试 fake 不实现 `parse_events`）；weapon_fire 单独保留（RLIMIT_DATA 注释）；合并前对 4 场本地 demo 逐字节比对归一化输出、检查峰值 RSS。复核者在 Nuke/Mirage 上复现（3.3 s → 0.28 s，峰值内存 592 → 500 MB）。价值一票反对：用户上传 300 MB 要几分钟，省 2.6 s 感知不到；等回放升级重解析成为常态再做 |
| 4.13 | 列式帧编码（本机测算：frames 原始 16.1 → 5.6 MiB，gzip 0.81 → 0.57 MiB，浏览器 `JSON.parse` 约 115k 个对象 → 10 个数组） | `projection.py:224-231` | 被否决为 L 级：要改契约、所有帧消费者和解码器；2.2 的 z 取整已拿到约 63% 的 gzip 收益。只有用户抱怨打开慢（Node 下 19 MiB `JSON.parse` 66 ms、31 MiB 堆，桌面上可忽略）再考虑 |

## 5. 已核查、不建议现在做（27 条被否决候选，按主题）

每条都经过至少两名复核者读代码确认事实后，以"这个规模下收益不足"或"方案前提有误"否决。列出来是为了避免下次再花时间重新发现。

- **后端性能**：`get_map_config` 每次位置换算 deepcopy（约 0.6 s/场，占归一化 45%）——事实成立，一行可修，但每场只跑一次；`rules.py` 拆包 + 共享上下文——分析器总共 0.7 s，共享只省 0.03 s，且 113 次提交只有 9 次碰过这个文件。
- **前端每帧**：rAF 不限速每次刷新都 commit——d3f05d8 已在 4 倍 CPU 节流下测得每帧 6 ms，AGENTS.md 明确选择"让每帧便宜"而不是限速；渲染状态轮询每 3 s 替换 `replay` 对象触发 memo 级联——生产 `RENDER_CLIPS_ENABLED=0`，这条路径根本不跑。
- **结构**：Demo Detail 页面 40 个 useState/42 useCallback/28 useMemo 的"上帝组件"拆分——提议的 hook 边界与实际状态耦合不符（`loadReplay` 同时写渲染态和页面位置态），强拆会制造并行状态，违反 AGENTS.md 的共享 tick/round 原则；规则 id 在约 15 处手工镜像——唯一一次加规则（e82b94d）一次提交全部更新且有逐规则测试，漂移只会退化成"其他建议"标签；coercion/clock helper 多份略有差异——每处差异都是其调用点有意为之；校准提示依赖匹配一句英文——前提（手估地图）已过时。
- **反馈闭环**（这组最值得读）：跨 owner 评价汇总 CLI——一条 psql（`coaching_feedback` join `coaching_events` 按 `structured_context_json->>'ruleId'`、verdict 分组）就够，建议把这条 SQL 写进 `coaching_feedback_v1.md:146` 旁边；评价行不存 rule_id/规则版本——版本 bump 前用同一条查询存一份快照即可，孤儿评价保留是文档化设计；离线 what-if 工具——路线图第 1 项未完成、内测没开始、还没有评价数据可打分；「本场最值得回看」实际等于"所有阵亡卡优先"（模拟 50 个 top-5 槽位 46 个给了 untraded_death）——是文档化的有意定义，评价协议要求测试者评完每一张；评价 note 字段 API 存但 UI 不收——要验证的假设（短暂站位、开局 15 s 内提醒）都能从已存事件属性算出；"看过/跳转过"信号——卡片不点也能读完评价，信号无法区分"没看"与"看了没评"；`weak_utility_before_execute` 只给下包者——卡片已写"这是团队上下文，不能归责于下包者"。
- **运维**：备份只校验 TOC、失败无通知、部署前 dump 从不执行——前两点是已知路线图项，第三点并入 3.1；worker 作为 PID 1 无 SIGTERM 处理、部署时 10 s 后 SIGKILL 进行中的解析——`worker.py:737` 写明硬杀就是租约回收器的设计路径，README:238 接受分钟级恢复；Redis AOF 保留登录记录与 IP 哈希"数月"——估算 worker 空闲时每天写 7–9 MB，64 MB 自动重写约一到三周触发，/privacy 说法不准但量级不对；无 CSP、API 响应缺安全头——生产 API 只返回 JSON（docs/openapi 已关），mp4 已带 nosniff + CORP。
- **文档**：把根目录的 OpenAI 实施方案移走——AGENTS.md 三处、README 两处已禁 LLM，100 个提交没有任何源码受它误导，上线计划已把它定为历史；批量归档 16–27 份 `_goal.md`/验收记录——README:303 已标"阶段目标（历史记录）"，且后端 schema 注释仍引用其中几份。
- **测试**：严格样本冒烟只查 `completed`——两次真实解析器回归（demoparser2 0.41.0、RLIMIT_DATA 溢出）都表现为 failed，现有 `status == "failed"` 分支已抓住；规则/摘要版本 bump 只靠手改字面量——历史上两次都正确 bump，漏 bump 的代价是后台几分钟重算，提议的 sha256 固定会在地图重校准时误报。
- **上传**：分片 PUT 无超时——上传端是 Windows 游戏 PC，TCP 几十秒内放弃死连接，HTTP/3 另有保护，看门狗达不到承诺的"一分钟内重试"。
- **API**：浏览器端用 ETag/304——三条受益路径中两条实际拿不到 304（失败态无回放、409 后重解析 key 变了），模块级 JS 缓存刷新即丢；`/replay` 无 schema、投影白名单与 TS 类型手工同步——所谓漂移（`shots`、`kills/deaths`）都是有意的，`replayV2()` fixture 已被 `tsc` 按 `ReplayData` 检查。

## 6. 旧审计（`docs/cs2_coach_audit_v1.md`，2026-08-07）各项在 HEAD 的状态

| 旧审计项 | 现状 |
| --- | --- |
| §4.1 拆分 2581 行 `demo_service.py` | 已完成（`demo_service/` 包，mypy 全检） |
| §3.4 `docker-compose.yml` 内嵌 dev AES key / render token | 已处理：`docker-compose.prod.yml:47` 与 preview 用 `${VAR:?}` 覆盖，`config.py` 在生产拒绝 dev key 与 token。基础文件仍带默认值，但只影响本地开发 |
| §3.1 trade 检测纯径向距离 | 已改：`find_untraded_deaths` 现在的定义是 `same_killer`（`rules.py:233,260`）：记录中的击杀者本人在 5 s 窗口内被队友击杀才算补枪，不再用径向距离窗口；`same_area_distance`（现为 540，`rules.py:31`）只服务其他规则 |
| 地图校准只有 Dust2/Nuke | 已改（ff6759f，六张图 `calibrated: True`）；README:236 过时（见 2.5b） |
| §3.3 无真实 `.dem` fixture、parser 在 demoparser2 边界被 mock | 部分：`test_sample_demo_contract_v2.py` 存在但无人打开（见 3.8c）；`test_demo_parser.py:235` 仍 patch `sys.modules` |
| §4.3 `list_demos` N × `load_replay_blob` | **未修**（本报告 2.1） |
| §4.4 reaper + dispatch 幂等 | 已有：60 s 租约回收、5 分钟重派发、30 分钟 DB 回收、`pg_advisory_lock` 迁移 |
| §3.2 渲染流水线 | 生产有意关闭（`RENDER_CLIPS_ENABLED=0`），不再是上线阻塞 |

## 7. 批评者补充的横切观察

- `deploy.sh` 被 3.1、3.2、3.4、3.5、3.8d 五条同时触及。缺的是一个**在动任何运行中服务之前就失败**的预检阶段：回滚目标的迁移兼容性、新迁移前自动 dump、镜像标签存在；启动后检查覆盖 `/health` 与 `/health/worker`。建议把这五条当一个"部署工具链"PR 做。
- 3.3 的 `job_done` 日志、3.5 的 worker `mem_limit`、4.12 的"检查峰值 RSS"、3.8a 的 SIGABRT 分类都需要**每次解析的峰值内存**，今天没有任何地方记录它。先做 3.8a 的 `ru_maxrss` 记录。
- Postgres 从不在 CI 跑：`database.py:50/114/237`、`runner.py:255`、`deletion_service.py:525`、`upload_quota.py:226` 六处 `dialect.name` 分支只在手动 Docker RC 门禁里执行，`test_upload_quota` 用 MagicMock 伪造方言。批评者没有立项，因为 `rc_check.sh` 会在 Postgres 上跑 `init_db`；如果要补，只需一个模块在 `TEST_DATABASE_URL` 服务容器上调真实 `init_db()`。
- 后端热路径审查者的未立项观察：`match_side_rules` 每场解析跑 3 次（归一化的 `_with_thrower_sides`、回合经济、比分摘要，各约 0.25 s），可以共享一次结果；解析子进程到 worker 的 JSON 交接 23.6 MiB、dumps+loads 0.37 s，正常；三个空闲补算循环每 30 s 限速、每轮最多认领一场，正常；`owner_id`、`status`、`demo_jobs.demo_id` 都有索引。
- 批评者检查过但没立项的区域：`render-worker/`（路径校验、超时、无重定向打开器都正常，生产关闭）、`scripts/maps`、`app/cli/delete_data.py`（默认 dry-run）、删除 outbox（退避封顶 3600 s）、上传暂存盘空间守卫。Steam 导入服务带约 4.1k 行测试但 provider 只能是 disabled，生产比赛库仍显示收集 Game Authentication Code 的 Steam 面板——文档已标可选，若想减少 VPS 上的机密，是数据最小化候选。

## 附：复核统计

| 维度 | 候选 | 通过 |
| --- | --- | --- |
| 后端热路径 | 4 | 3 |
| 前端每帧 | 2 | 0（代码合规） |
| 结构/可维护性 | 8 | 3 |
| 测试 | 5 | 3 |
| 运维/可靠性 | 6 | 4 |
| 安全/依赖 | 8 | 6 |
| 文档/DX | 8 | 6 |
| 产品反馈闭环 | 8 | 1 |
| API 契约/数据 | 5 | 2 |
| 前端 UX 韧性 | 6 | 5 |
| 完整性批评者补充 | 4 | 4 |
| 合计 | 64 | 37（合并重复后 32） |
