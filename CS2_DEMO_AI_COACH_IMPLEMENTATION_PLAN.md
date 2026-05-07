# CS2 Demo AI 教练网站工程实施方案

## 背景与目标

本项目目标是构建一个工业级网站应用，用于解析 CS2 `.dem` demo 文件，生成 2D replay 数据和结构化 coaching events，并通过 AI 将规则引擎发现的问题改写成自然语言教练建议。

系统设计重点：

- 产品形态是网站应用，不是桌面软件。
- `.dem` 不是视频，第一版前端使用解析后的 tick 数据做 2D replay viewer。
- demo 文件可能很大，上传、解析、分析、AI 改写必须异步处理。
- 核心分析由规则引擎完成，LLM 不是核心分析器。
- LLM 只读取结构化 coaching context，不直接读取完整 demo。
- 架构需要按支持至少 20 万注册玩家的工业级标准设计。

推荐技术栈：

- Frontend: Next.js + TypeScript
- Backend: FastAPI / Python
- Parser: demoparser2 或 awpy
- DB: PostgreSQL
- Queue: Redis + Celery/RQ
- Object Storage: S3 / Cloudflare R2
- Cache: Redis
- AI: OpenAI API
- Deployment: Docker + cloud deployment

## 1. 产品 MVP 范围

### MVP 包含

- 用户注册、登录、登出
- 上传 `.dem` 或 `.zip`
- 上传后创建解析任务，异步处理
- demo 解析状态展示：
  - `uploaded`
  - `queued`
  - `parsing`
  - `analyzing`
  - `ai_generating`
  - `completed`
  - `failed`
- 解析 demo 基础数据：
  - 地图
  - 队伍
  - 玩家
  - 回合
  - tick 时间轴
  - 击杀、死亡、助攻
  - 炸弹事件
  - 投掷物事件
  - 经济与装备快照
  - 玩家 2D 坐标
- 规则引擎生成 coaching events
- OpenAI API 将结构化事件改写为教练语言
- 前端 2D replay viewer
- 回合选择、时间轴、倍速、暂停/播放
- 右侧根据当前 tick 实时显示建议
- 点击建议跳转到对应 tick
- 用户只能访问自己的 demo

### MVP 不包含

- 真实视频识别
- CS2 客户端内嵌播放
- 3D demo viewer
- 实时比赛分析
- 职业级战术库推荐
- 团队协作空间
- 付费订阅系统
- 反作弊检测

## 2. 用户流程

1. 用户进入网站。
2. 用户注册或登录。
3. 登录后进入 Dashboard。
4. 用户点击上传 demo。
5. 前端请求后端创建 upload session。
6. 后端返回 S3/R2 presigned upload URL。
7. 前端将 `.dem` 或 `.zip` 直传到对象存储。
8. 前端通知后端上传完成。
9. 后端创建 parse job，推入 Redis Queue。
10. 用户在 Dashboard 看到 demo 状态：排队中、解析中、分析中、生成建议中。
11. Worker 下载 demo，解压、校验、解析。
12. Worker 生成 replay 数据和 coaching events。
13. Worker 调用 LLM 改写建议文案。
14. demo 状态变为 `completed`。
15. 用户打开 demo 页面。
16. 左侧播放 2D replay。
17. 右侧建议根据当前 tick 自动更新。
18. 用户点击建议后，replay 跳转到对应时间点。

## 3. 前端页面结构

建议使用 Next.js App Router。

```text
frontend/
  app/
    page.tsx                         // 登录前首页，可极简
    login/page.tsx
    register/page.tsx
    dashboard/page.tsx               // demo 列表、上传入口
    demos/[demoId]/page.tsx          // replay + coaching 主页面
    settings/page.tsx
```

### Dashboard 页面

Dashboard 应该包含：

- 上传按钮
- demo 列表
- 解析状态
- 地图名
- 上传时间
- 文件大小
- 回合数
- 失败任务重试入口
- 删除 demo

### Demo Detail 页面

```text
┌──────────────────────────────────────────────┐
│ Top bar: demo name, map, status, user menu    │
├─────────────────────────────┬────────────────┤
│ 2D Replay Viewer             │ Coaching Panel │
│ - minimap                    │ - 当前 tick建议 │
│ - player dots                │ - 事件列表      │
│ - utility trajectory         │ - 严重程度筛选  │
│ - bomb state                 │ - 点击跳转      │
├─────────────────────────────┴────────────────┤
│ Timeline: round selector, play, speed, slider │
└──────────────────────────────────────────────┘
```

### 前端核心模块

```text
components/replay/ReplayViewer.tsx
components/replay/Timeline.tsx
components/replay/RoundSelector.tsx
components/replay/PlayerMarker.tsx
components/coaching/CoachingPanel.tsx
components/coaching/CoachingEventCard.tsx
components/upload/DemoUploader.tsx
lib/api.ts
lib/auth.ts
lib/replay-store.ts
types/demo.ts
types/coaching.ts
```

## 4. 后端服务拆分

第一版建议使用 FastAPI 单体后端 + 独立 worker，保持部署简单，但代码边界按工业级服务拆分。

```text
backend/
  app/
    main.py
    api/
      auth.py
      demos.py
      uploads.py
      replay.py
      coaching.py
    core/
      config.py
      security.py
      rate_limit.py
      storage.py
      database.py
      redis.py
    models/
      user.py
      demo.py
      job.py
      replay.py
      coaching.py
    schemas/
      auth.py
      demo.py
      coaching.py
    services/
      upload_service.py
      demo_service.py
      replay_service.py
      coaching_service.py
      ai_service.py
    workers/
      celery_app.py
      parse_demo_task.py
      analyze_demo_task.py
      generate_ai_feedback_task.py
    parser/
      demo_parser.py
      normalizer.py
    analysis/
      rule_engine.py
      rules/
        positioning.py
        utility.py
        trading.py
        economy.py
        objective.py
```

### 服务边界

- API Service：认证、上传会话、状态查询、replay/coaching 数据读取。
- Worker Service：解析 demo、生成 replay、规则分析、LLM 改写。
- Parser Module：封装 `demoparser2` 或 `awpy`。
- Rule Engine：核心教练分析逻辑。
- AI Service：只负责把结构化事件转成自然语言。
- Storage Service：封装 S3/R2 读写。
- Queue Service：封装 Redis + Celery/RQ。

## 5. 数据库设计

PostgreSQL 只存元数据、索引、权限、任务状态。大 replay tick 数据不要直接塞进 PostgreSQL，应压缩后放对象存储。

### users

```text
users
- id
- email
- password_hash
- display_name
- created_at
- updated_at
- last_login_at
```

### demos

```text
demos
- id
- user_id
- original_filename
- storage_key
- file_size_bytes
- file_sha256
- map_name
- tick_rate
- duration_seconds
- round_count
- status
- error_message
- created_at
- updated_at
- completed_at
```

### demo_jobs

```text
demo_jobs
- id
- demo_id
- job_type              // parse / analyze / ai_feedback
- status
- attempts
- error_message
- started_at
- finished_at
- created_at
```

### matches

```text
matches
- id
- demo_id
- map_name
- tick_rate
- total_ticks
- replay_storage_key
- summary_storage_key
- created_at
```

### rounds

```text
rounds
- id
- demo_id
- round_number
- start_tick
- freeze_end_tick
- end_tick
- winner_side
- end_reason
- t_score
- ct_score
```

### players

```text
players
- id
- demo_id
- steam_id
- name
- team_name
- side_initial
```

### coaching_events

```text
coaching_events
- id
- demo_id
- round_id
- player_id nullable
- tick_start
- tick_end
- category
- severity
- title
- message
- structured_context_json
- ai_message
- confidence
- created_at
```

### upload_sessions

```text
upload_sessions
- id
- user_id
- demo_id
- storage_key
- status
- expires_at
- created_at
```

### 建议索引

```text
demos(user_id, created_at desc)
demos(status)
demo_jobs(status, created_at)
rounds(demo_id, round_number)
coaching_events(demo_id, tick_start)
coaching_events(demo_id, round_id)
coaching_events(demo_id, category, severity)
players(demo_id, steam_id)
```

## 6. Demo 上传、解析、排队、状态更新流程

上传必须走对象存储直传，不要让大文件经过 FastAPI 进程。

```text
1. Frontend -> Backend: POST /uploads/init

2. Backend:
   - 校验登录
   - 检查用户额度
   - 创建 demos 记录，status = uploaded_pending
   - 创建 upload_sessions
   - 返回 presigned URL

3. Frontend -> S3/R2:
   - PUT 文件
   - 支持大文件 multipart upload

4. Frontend -> Backend: POST /uploads/complete

5. Backend:
   - HEAD 对象确认存在
   - 校验大小、扩展名、content type
   - demos.status = queued
   - 创建 demo_jobs(parse)
   - enqueue parse task

6. Worker parse:
   - demos.status = parsing
   - 下载 demo 或 zip
   - 解压
   - 校验只允许 .dem
   - 解析 tick、round、players、events
   - 生成 replay blob
   - 写入 matches / rounds / players
   - demos.status = analyzing
   - enqueue analysis task

7. Worker analysis:
   - 执行规则引擎
   - 生成 coaching_events structured_context_json
   - demos.status = ai_generating
   - enqueue ai feedback task

8. Worker AI:
   - 批量读取 coaching context
   - 调用 OpenAI API
   - 写入 ai_message
   - demos.status = completed

9. Frontend:
   - MVP 可轮询 /demos/:id/status
   - 后续改 SSE 或 WebSocket
```

### 失败处理

- 每个 job 记录 attempts。
- 可重试错误最多 3 次。
- parser 不支持、文件损坏、超限直接 `failed`。
- `failed` 状态保留 error code，不向用户暴露内部 stack trace。

## 7. Coaching Events 数据结构

每个 coaching event 应该能被时间轴索引、能跳转、能筛选、能被 LLM 改写。

```json
{
  "id": "evt_123",
  "demo_id": "demo_abc",
  "round_number": 8,
  "tick_start": 54210,
  "tick_end": 54880,
  "time_seconds": 423.4,
  "player": {
    "steam_id": "7656119...",
    "name": "player1",
    "side": "T"
  },
  "category": "positioning",
  "severity": "high",
  "phase": "mid_round",
  "title": "Overexposed without trade support",
  "rule_id": "positioning.overpeek_no_trade_v1",
  "confidence": 0.82,
  "evidence": {
    "player_position": { "x": 1240, "y": -532 },
    "nearest_teammate_distance": 1800,
    "enemy_visible_count": 2,
    "time_to_death_ticks": 96,
    "weapon": "ak47"
  },
  "structured_context": {
    "situation": "Player peeked into two defenders while closest teammate was too far to trade.",
    "mistake": "Took isolated duel",
    "impact": "Death created 4v5 and lost map control",
    "recommendation": "Wait for teammate spacing or use flash before re-peeking"
  },
  "ai_message": "你这次中期单人前压太深，队友距离不够，死后没人能补枪。下次要么等队友贴近形成 trade，要么先用闪光再重新拿这个角度。"
}
```

### 分类建议

```text
positioning
crosshair
utility
trading
economy
objective
rotation
timing
post_plant
retake
entry
clutch
```

### 严重程度

```text
info
low
medium
high
critical
```

## 8. 如何支持 20 万玩家规模

关键原则：数据库存索引，对象存储存大数据，解析任务水平扩展。

### 容量假设

- 20 万注册用户不等于 20 万同时在线。
- MVP 可按 5k-20k DAU 设计。
- 解析任务是最大成本中心。
- 单个 demo 可能 100MB-500MB，zip 可能更大。

### 核心策略

- 上传直传 S3/R2，API 不承载大文件流量。
- 原始 demo、replay JSONL/Parquet、summary 全部放对象存储。
- PostgreSQL 只保存元数据与索引。
- Worker 横向扩展。
- Redis Queue 按任务类型拆队列：
  - `parse`
  - `analysis`
  - `ai`
  - `cleanup`
- 对 replay tick 数据降采样，例如 viewer 默认 8/16 tick 一帧。
- 按 round 切 replay blob，前端按需加载。
- coaching events 走 DB 查询，replay 大数据走 signed URL/CDN。
- 热门 demo summary 和 event list 用 Redis 缓存。
- 用户配额限制：
  - 免费用户每日上传数
  - 单文件大小限制
  - 总存储空间限制
  - 并发解析任务限制
- Parser worker 使用 CPU 优化实例，AI worker 独立限流。
- 大表按时间或 user_id 做分区预留。
- 对象存储设置 lifecycle policy，过期清理原始 demo。

## 9. 同步处理与异步处理

### 同步处理

- 注册/登录
- 创建上传会话
- 返回 presigned URL
- 查询 demo 列表
- 查询 demo 状态
- 查询 coaching events
- 获取 replay signed URL
- 基础权限校验
- 文件大小、扩展名、用户额度初步校验

### 异步处理

- zip 解压
- sha256/hash 计算
- 病毒/恶意文件扫描
- demo parser 解析
- replay 数据生成
- round/player/event 归一化
- 规则引擎分析
- OpenAI API 生成教练语言
- replay blob 压缩上传
- 清理临时文件
- 失败任务重试
- 存储生命周期清理

## 10. 安全、权限、限流、存储成本控制

### 安全与权限

- 所有 demo 归属于 `user_id`。
- API 查询必须校验 demo ownership。
- 对象存储 bucket 不公开。
- replay 和原始 demo 通过短期 signed URL 访问。
- 密码使用 Argon2 或 bcrypt。
- 使用 JWT access token + refresh token。
- Refresh token 存 DB，可撤销。
- 所有上传文件先进入 quarantine 前缀。
- zip 解压防 Zip Slip。
- 限制 zip 解压后文件数量和总大小。
- 禁止任意后缀文件进入 parser。
- 不把原始 demo 内容直接发给 OpenAI。
- OpenAI prompt 只包含结构化 coaching context。

### 限流

- IP 级登录限流。
- 用户级上传限流。
- 用户级解析并发限制。
- AI 调用速率限制。
- 单 demo coaching event 数量上限。
- presigned URL 短过期时间。

### 成本控制

- 原始 demo 可设置保留期，例如 7/30/90 天。
- replay 数据长期保存，原始 demo 可删除。
- replay 数据按 round 分片压缩。
- 低价值 tick 字段不保存。
- 免费用户限制总存储。
- AI 只处理 top N 高价值事件。
- 相似事件先聚合，再让 LLM 改写。
- 使用 Redis 缓存 demo summary，减少 DB 和对象存储读取。

## 11. 部署架构

推荐 Docker 化部署。

```text
Internet
  |
CDN / WAF
  |
Next.js Frontend
  |
FastAPI API Service
  |
PostgreSQL
Redis
Object Storage S3/R2
  |
Worker Pool
  - parser workers
  - analysis workers
  - ai workers
  - cleanup workers
```

### 初期部署

```text
docker-compose.dev.yml
- frontend
- api
- postgres
- redis
- worker
- minio optional
```

### 生产部署

- Frontend：Vercel / Cloudflare Pages / container
- API：ECS / Fly.io / Render / Kubernetes / Cloud Run
- Worker：独立 autoscaling service
- PostgreSQL：managed Postgres
- Redis：managed Redis
- Object Storage：S3 或 Cloudflare R2
- Observability：
  - structured logs
  - job metrics
  - queue depth
  - parser duration
  - failed job rate
  - AI token cost
  - upload volume
  - storage growth

## 12. 第一阶段应该先实现的文件和模块

第一阶段目标不是做完整 AI，而是把“上传、排队、解析状态、mock replay、mock coaching event、前端播放框架”跑通。

建议先建立这个结构：

```text
/
  docker-compose.yml
  .env.example
  README.md

  frontend/
    package.json
    next.config.ts
    tsconfig.json
    app/
      layout.tsx
      page.tsx
      login/page.tsx
      register/page.tsx
      dashboard/page.tsx
      demos/[demoId]/page.tsx
    components/
      upload/DemoUploader.tsx
      replay/ReplayViewer.tsx
      replay/Timeline.tsx
      replay/RoundSelector.tsx
      coaching/CoachingPanel.tsx
      coaching/CoachingEventCard.tsx
    lib/
      api.ts
      auth.ts
    types/
      demo.ts
      coaching.ts

  backend/
    pyproject.toml
    Dockerfile
    app/
      main.py
      core/
        config.py
        database.py
        redis.py
        security.py
        storage.py
      api/
        auth.py
        uploads.py
        demos.py
        replay.py
        coaching.py
      models/
        user.py
        demo.py
        job.py
        coaching.py
      schemas/
        auth.py
        demo.py
        coaching.py
      services/
        upload_service.py
        demo_service.py
        queue_service.py
      workers/
        celery_app.py
        parse_demo_task.py
      parser/
        demo_parser.py
        normalizer.py
      analysis/
        rule_engine.py
```

### 第一阶段实施顺序

1. 初始化 Next.js、FastAPI、PostgreSQL、Redis、Docker Compose。
2. 实现用户注册/登录和 JWT 权限。
3. 实现 demo 表、job 表、coaching_events 表迁移。
4. 实现上传会话 API，开发环境可先用本地/MinIO 存储。
5. 实现上传完成后入队。
6. 实现 worker 消费任务并更新状态。
7. 先用 mock parser 生成 replay frames 和 coaching events。
8. 实现 demo 状态轮询。
9. 实现 replay 页面布局、时间轴、回合选择、倍速播放。
10. 实现 coaching panel 根据 tick 联动。
11. 接入真实 parser，把 mock replay 替换成真实解析结果。
12. 接入第一批规则引擎。
13. 最后接入 OpenAI 文案改写。

## 建议执行策略

先实现第一阶段第 1-7 步，把端到端异步链路跑通：

```text
register/login
  -> create upload session
  -> complete upload
  -> enqueue job
  -> worker consumes job
  -> mock parser generates replay
  -> mock rule engine generates coaching events
  -> frontend displays replay and events
```

端到端链路稳定后，再接入真实 CS2 parser、规则引擎和 OpenAI 改写。这样可以避免一开始就被 `.dem` 解析细节、AI prompt、前端 replay 交互同时阻塞。
