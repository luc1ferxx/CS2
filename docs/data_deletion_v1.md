# 数据删除 V1：永久删除比赛和账户

用户可以在网站上永久删除一场比赛；用真实账户（production 的 Steam 登录）还可以删除整个账户和全部数据。公开的隐私说明页 `/privacy` 用同样的事实告诉用户：网站保存什么、保存多久、删除后还剩什么。本文档是实现和运维的依据。

改动数据的收集、保存、外发或删除方式时，要在同一次改动里同时更新本文档、`/privacy`（`frontend/app/privacy/page.tsx`）和 [README](../README.md) 的"安全与隐私边界"一节。

## 决策

1. **归档仍是日常动作，删除必须显式操作。** 比赛库的软归档不变：可以撤销，归档的比赛按 ID 仍能打开。永久删除是单独的操作，需要确认，无法撤销。
2. **硬删除，不留墓碑。** 行在一次短事务里删掉；存储在提交之后清理，清理失败由持久化的删除任务（outbox）重试。比赛没有"删除中"状态。如果用墓碑，解析和渲染的每一次写入都得改成条件写入，否则晚到的写入会把内容写回来。
3. **任何状态都能删**：排队、解析中、失败、渲染中都可以。每条进行中的路径都要容忍行突然消失（见[并发与加固](#并发与加固)）：worker 不崩溃，接口不返回 500，内容不会被写回来，最后一次清扫之后不留对象。
4. **删除不退还上传次数。** 每日上传数不再按 `demos` 行计算，改为按不含内容的上传账本 `upload_ledger` 计算（见[上传配额账本](#上传配额账本)）。
5. **只有真实账户能删除账户（`AUTH_MODE=production`）。** development/test 下 `DELETE /auth/account` 返回 `409 account_deletion_unavailable`。本地开发 owner 没有账户行，不能让一次点击清空本地比赛库。界面在开发模式下会说明这一点，比赛仍可以逐场删除。
6. **会话随账户一起失效**：删除账户会让所有设备上的会话失效，不只是当前浏览器。
7. **production 下，创建比赛的路径有围栏。** 上传和 Steam 导入的提交事务都会确认账户仍然存在。账户已经不在时，回滚事务、丢弃已准备好的文件：上传返回 `401 account_deleted`，Steam 导入干净地失败。
8. **Steam 导入的比赛只解除关联。** 删除这样的比赛时，对应的 `steam_matches` 行保留，只做三件事：`demo_id` 置空；状态回到"已发现、可以导入"；从比赛派生的字段（`players_json`、地图、比分、解析和派发字段）清空。删除账户时则显式删除 `steam_matches` 和 `steam_connections`。
9. **建议评价随比赛一起删除**，不保留匿名残留。
10. **存储清理同时用精确引用和前缀清扫，并且无条件删除。** 见[存储清理](#存储清理)。
11. **删除任务落库，持久化重试。** 见[删除任务（outbox）](#删除任务outbox)。
12. **固定锁顺序，遇到死锁时整个事务重试。**
13. **备份和日志里的副本不单独清除。** 它们按各自的保留期过期，隐私页如实说明（见[删除后仍然存在的数据](#删除后仍然存在的数据)）。

## API

| 路由 | 行为 |
| --- | --- |
| `DELETE /demos/{demo_id}` | 成功返回 `204`，没有 body。<br>按 owner 隔离：别人的或不存在的 id 返回 `404`，和读取时一样。<br>对用户来说是幂等的：第二次删除得到 `404`，前端当作成功处理。同一场比赛的两个并发删除，一个得到 `204`，另一个得到 `404`，不会出现 `500`。 |
| `DELETE /auth/account` | body 为 `{"confirm": "delete-my-account"}`，成功返回 `204`。响应让 `__Host-cs2_session` 过期，cookie 属性与退出登录时完全相同。<br>confirm 缺失或不对：`400 confirmation_required`。<br>没有会话：`401`。<br>development/test：`409 account_deletion_unavailable`。 |
| `GET /uploads/quota` | 返回形状不变；每日已用次数改为来自上传账本。 |

production 下两个 `DELETE` 都经过会话和 CSRF 中间件：必须有会话，`Origin` 必须是站点源。响应都带 `Cache-Control: private, no-store`。

Caddy 路由：
- `DELETE /demos/{id}` 已经由 `@api_demo_write` 转给 API。
- `/auth/account` 加进了 `@api` 匹配。Next.js 的"账户与数据"页在 `/account`，是另一个路径。

## 删除一场比赛

整个删除是一次短的数据库事务，事务里不做任何存储 I/O：

1. 按 id 顺序锁住这场比赛的 `demo_jobs`（`FOR UPDATE`），再锁住 demo 行（按 owner 限定）。找不到比赛就返回 `404`。
2. 在锁内读出所有存储引用：`demos` 上的源文件和回放引用，以及每个任务元数据里记录的源文件、输出视频和回放引用。必须先加锁再读：刚提交的解析或渲染可能写了新引用，加锁后才读得到。
3. 按以下顺序显式删除：
   1. 解除 `steam_matches` 关联；
   2. `coaching_feedback`；
   3. `coaching_events`；
   4. `demo_jobs`；
   5. `demos`。

   不依赖外键级联：`demo_jobs` 和 `coaching_events` 的外键没有 `ON DELETE`，SQLite 测试默认也不检查外键。
4. 在同一个事务里插入一条删除任务，然后提交。
5. Postgres 报死锁（SQLSTATE `40P01`）或序列化失败时，整个事务最多重试 3 次。解析和渲染路径里，有的先锁任务再锁比赛，有的顺序相反，所以任何一种删除顺序都可能和其中一条路径互相等待。

提交之后，请求立即做一次尽力而为的存储清理。这次清理失败不影响 `204`，剩下的由删除任务继续重试。

不在事务里做存储 I/O 有两个原因：
- 解析重试在等比赛行锁时，占着全局的额度锁。事务一长，所有上传和重试都会跟着卡住。
- 如果先删存储、后删行，比赛库在这段时间里会显示"回放缺失，可重试"的行。

## 删除账户

仅 production。

1. 锁住账户行（`FOR UPDATE`）。上传和 Steam 导入提交时会对账户行加 `FOR KEY SHARE`，和这把锁互斥。所以并发的提交只有两种结果：在删除之前落地，随后和其他数据一起被删；或者看到账户已经不在，按围栏失败。
2. 在数据库事务提交**之前**写入 Redis 的 owner 撤销标记（见[会话撤销](#会话撤销)），让其他设备上的会话立刻失效。
3. 删除数据：
   1. 这个 owner 的每一场比赛都走[删除一场比赛](#删除一场比赛)的同一套步骤；
   2. 删除 `coaching_feedback WHERE owner_id`、`steam_matches`、`steam_connections`、`external_identities` 和 `accounts`；
   3. 插入一条覆盖整个 owner 的删除任务（`demo_id` 为空，`cutoff_at` 取当前时间）。

   比赛多时分批处理，每个事务都保持短小。
4. 响应让会话 cookie 过期。前端随后清除本浏览器里"你在比赛里选的玩家"偏好，把登录状态切换为已退出，然后显示"账户已删除"。前端不会把 `401` 当作删除成功。

如果撤销标记已经写入、数据库事务却失败了，数据还在，所有会话已经失效；用户重新登录后可以再删一次。

删除之后，只要这个 Steam 账号还在邀请名单里，重新登录就会得到一个全新的空账户（新的随机 `owner_id`），上传次数也从零算起。这是有意的：若要按 Steam 身份跨账户计数，删除账户后就得继续保存一个由 SteamID64 推出的标识，与"删除账户会删掉全部数据"相冲突；邀请名单本身限定了谁能这样做，全站处理数上限照常生效。要阻止此人再登录，把他从 `STEAM_LOGIN_ALLOWLIST` 里去掉并重新部署。移出名单不会删除他的数据，而他也无法再登录自己删除，所以先按[代用户删除](#代用户删除)删掉他的账户（如果他希望），再移出名单。

邀请名单（受邀者的 SteamID64）保存在服务器的 `deploy/.env.production` 里，不属于账户数据，删除账户不会改动它。`/privacy` 和「账户与数据」页都如实说明了这一点。

登录与删除的竞争：删除账户先锁账户行、写撤销标记，再提交。一次恰好在这之间完成身份解析的 Steam/OIDC 登录，可能在标记之后才签发会话，标记拦不住它。所以登录回调在签发会话之后，再对账户行取一次创建者围栏（`FOR KEY SHARE`）：正在删除时它等删除提交，然后看到账户已不在，撤销刚签发的会话并返回 `401`；它先拿到锁时，之后的删除写入的标记晚于这个会话的 `issuedAt`，照样撤销。解析身份时如果更新到一行刚被删掉的身份（`StaleDataError`），就重新解析一次，结果是一个全新的空账户，和之后再登录一样。

## 会话撤销

- 新签发的会话记录多一个 `issuedAt`（毫秒）。
- 删除账户时写入 `auth:owner-revoked:{sha256(owner_id)}`，值为当前毫秒时间，TTL 为 86400 + 300 秒（production 会话最长 86400 秒，再加时钟余量）。
- 解析会话时，如果该 owner 有撤销标记，并且会话没有 `issuedAt` 或 `issuedAt` ≤ 标记，就删除这个会话并返回 `401`。
- 只用到 Redis 的 `get`、`setex` 和 `delete`，所以测试里现有的 FakeRedis 都能用。
- 标记存在期间，删除前签发的会话全部失效；标记过期时，这些会话本身也早已过期。
- 这不同于退出登录：退出只撤销当前这一个会话。

## 上传配额账本

- 表结构为 `upload_ledger (id, owner_id, created_at)`，不含任何比赛内容。
- 每次创建比赛（上传和 Steam 导入）都在同一个事务里写入一行。
- 每日上传数 = 这个 owner 最近 24 小时的账本行数。超过 24 小时的行会被清理。
- 迁移时，用最近 24 小时的 `demos.created_at` 回填账本，所以升级当天的计数不会清零。
- 同时处理数（`DEMO_ACTIVE_PARSE_LIMIT`）和全站处理数（`PARSE_QUEUE_GLOBAL_LIMIT`）仍然按 `demos` 行计算：删除一场正在处理的比赛会让出名额，这是预期行为。

## 存储清理

**键的结构**
- 对象键是 `{OBJECT_STORAGE_PREFIX}/v1/{state}/{kind}/{b64url(owner)}/{b64url(demo)}/{id}`。
- 本地存储是 `artifact-v1/{state}/{kind}/{owner}/{demo}/{id}.blob`，外加 `{id}.metadata.json`。
- state 和 kind 排在 owner 前面，所以没有统一的"某个 owner"或"某场比赛"的前缀。

**清理做什么**
- 清理新加在存储合同里，两种后端都实现：列出 5 种状态 × 4 种类型、共 20 个前缀下属于 `(owner, demo)` 或 `(owner, *)` 的全部对象，然后逐个删除。
- 每个 base64url 片段后面都带 `/`。这样另一个 owner 或比赛的编码即使与之前缀相同，也不会被匹配到。
- 删除按原始键进行，不带条件：
  - S3/R2 用 ListObjectsV2 列出，再用 DeleteObjects 分批删除，不带 `IfMatch` 或 `VersionId`。R2 是否支持条件删除还不确定，清理本身也不需要条件。
  - 本地删除 blob 和元数据文件，再删掉空目录。
- 精确引用和前缀清扫一起用。精确引用就是事务里读出的引用；前缀清扫还能找到以下几类对象：
  - 只记录在回放 JSON 里的手动视频；
  - 旧的回放版本（以前那次尽力删除失败时留下的）；
  - 隔离区里没有清掉的上传；
  - 删除之后才写完的回放或视频。
- 整个 owner 的清扫可以带创建时间截止点（`created_before`）：S3 用 LastModified，本地用 created-at 元数据或文件 mtime。只删除截止点之前创建的对象，所以 owner ID 可复现的登录方式（OIDC 兼容路径）在删除后重新注册时，新数据不会被旧任务误删。
- 截止点只在需要时用：过了 `final_sweep_after`、而且这个 `owner_id` 下已经没有账户行时，账户任务的清扫扩大到"当前时间之前"的全部对象。删除时正在上传、在提交时被围栏拦下的请求，会尽力丢弃自己已经写好的文件；这次丢弃失败（或进程在两步之间崩溃）时，留下的对象创建于截止点之后，不属于任何比赛，也没有自己的删除任务，只能靠这一步清掉。账户被同一 owner ID 重新创建时，截止点照旧保护新数据。
- 开启了版本控制的 S3 兼容桶（AWS S3、MinIO、B2 等）上，普通删除只加一个删除标记，数据仍作为旧版本留着。清理第一次运行时用 `GetBucketVersioning` 问一次：状态为 Enabled 或 Suspended 时，改用 ListObjectVersions 列出全部版本和删除标记，并按 `VersionId` 逐个删除，统计也按版本计，所以任务要等旧版本也删光才会结束。R2 没有桶版本控制，这个请求返回 NotImplemented，按不带版本的普通删除处理，与上面的无条件删除一致。
- 开发环境遗留的 `local://…` 键：通过旧的本地存储解析，文件存在就删掉，任何情况都不报错；production 完全跳过这类键。

## 删除任务（outbox）

表结构为 `deletion_tasks (id, owner_id, demo_id, artifact_refs, cutoff_at, final_sweep_after, next_attempt_at, attempts, last_error, created_at, updated_at)`：

| 字段 | 含义 |
| --- | --- |
| `demo_id` | 要删除的比赛；为空表示整个账户 |
| `artifact_refs` | 事务里读出的精确存储引用（JSON） |
| `cutoff_at` | 账户任务的截止时间：只处理这之前创建的比赛和对象 |
| `final_sweep_after` | 创建时间 + 35 分钟 |
| `next_attempt_at` | 下一次处理的时间，同时充当租约 |
| `attempts` / `last_error` | 失败次数和一行错误说明（不含密钥和路径） |

**写入与处理**
- 删除任务和删除行在同一个事务里写入，所以只要行删掉了，就一定有任务负责清理存储。
- 请求提交后立即尽力清理一次。
- 之后由这两处继续处理到期的任务：
  - 解析 worker 的空闲周期，最多每 60 秒一次。它和 worker 的其他后台检查一样相互隔离，出错不会让 worker 退出。Postgres 上用 `FOR UPDATE SKIP LOCKED`，多个进程不会同时处理同一条任务。
  - API 启动时也处理一遍。
- worker 正在解析时（最长 `PARSE_TIMEOUT_SECONDS`，默认 20 分钟），或者队列一直有任务时，空闲周期不运行，清理会相应推迟。

**什么时候算完成**
- 35 分钟覆盖 `PARSE_RECLAIM_AFTER_SECONDS` 和 `RENDER_CLIP_STALE_AFTER_SECONDS`（各 1800 秒）。删除时正在进行的解析或渲染，到这个时间之前一定已经写完或被放弃；它们晚到的文件由最后一次清扫删掉。
- 任务同时满足以下条件才会删除：
  - 当前时间 ≥ `final_sweep_after`；
  - 最近一次清扫没有找到剩余对象；
  - 如果是账户任务：这个 owner 已经没有 `created_at ≤ cutoff_at` 的 `demos` 行（账户已不存在时，截止点按上面的规则扩大到当前时间）。
- 账户任务如果还发现这样的比赛（例如删除时刚好提交的一次上传），会走同样的单场删除流程把它删掉。
- 出错时 `attempts` 加一，`last_error` 记一行说明，异常不会抛出到周期之外。

**顺带的隔离区清理**：同一个空闲周期每小时最多一次，清理隔离区里超过 1 小时的对象。中断的上传不用再等 API 重启才被清理。

**运维查看卡住的任务**（不显示 owner）：

```bash
dc exec -T postgres psql -U cs2coach -d cs2coach -c \
  "select id, demo_id is null as whole_account, attempts, last_error, final_sweep_after, next_attempt_at from deletion_tasks order by created_at"
```

`dc` 的定义见 [VPS 部署](vps_deploy_v1.md)。任务通常在删除后 35 分钟左右消失。`attempts` 一直在涨的，看 `last_error` 和 `dc logs worker`。

## 并发与加固

| 进行中的操作 | 比赛或账户被删除后的行为 |
| --- | --- |
| 上传进行中 | 客户端还没有比赛 id，无从删除。账户被删除时，上传在提交时被围栏拦下：返回 `401 account_deleted`，不留行，也不留对象。 |
| Steam 导入 | 账户被删除时，导入在提交时干净地失败。删除一场导入的比赛时，只解除关联，这场比赛可以重新导入。 |
| 排队中的解析 | worker 发现行已经不在，直接跳过。 |
| 解析中 | 解析子进程运行期间，worker 每 15 秒（`PARSE_DELETION_CHECK_SECONDS`）用一个单独的短会话检查任务行是否还在；不在了就停掉子进程、删掉临时复制的 `.dem`，记一行 `demo deleted during parse` 后处理下一个任务，不再占着唯一的 worker 等到解析超时。子进程刚好结束时，后续的写入（进入分析、完成、失败）捕获"行已消失"的错误，然后依次：回滚、删除已经写好的回放、记一行 `demo deleted during parse`、返回。worker 不会退出。 |
| render worker API | manifest、源文件、媒体上传和结果回调遇到已消失的任务或比赛，统一返回 `404 {"detail": "Render clip job not found"}`。如果媒体已经上传而任务不在了，刚存下的视频会被删除。渲染的定时清扫也容忍行消失。 |
| `render-worker/runner.py` | manifest、源文件、媒体或结果返回 `410`，或返回 API 自己的 `404 {"detail": "Render clip job not found"}` 时视为终态：不再发送失败回调，不退避重试，删除这个任务的工作目录（`WORK_DIR/jobs/{job}` 和它的 manifest）。其他 `404`（HTML 页面、别的内容，例如 `API_BASE_URL` 填成了前端地址）只是普通的请求失败，不删除任何东西，也不删操作员手动录制的视频。 |
| 其他用户路由 | 以下路由在行消失时返回 `404`，不再是 `500`：`PATCH /demos/{id}`、`/archive`、解析重试（在额度锁内锁住比赛）、建议评价保存、创建 `render_clip`（原来是 `400`）、开发用的视频路由。 |
| 比赛库页面 | 删除前后都作废进行中的列表请求，轮询不会把删掉的行加回来。指向这场比赛的重命名状态和提示也一并清掉。 |
| 复盘页 | 删除前停止状态、渲染和片段轮询，并忽略随后的 `404` 提示。 |

## 界面

- **比赛库**
  - 入口：行菜单最后一项「删除比赛…」。
  - 确认框标题「永久删除这场比赛？」，列出会删除的内容：比赛文件 .dem、回放数据、复盘建议和你的评价。
  - 确认框写明「此操作无法撤销。」；有每日上限时，再加一句「删除不会恢复今天的上传次数。」
  - 成功后显示「已永久删除「{名称}」」，焦点移到相邻的行。
- **复盘页**
  - 入口：「高级工具」最下方的「删除这场比赛」；处理中、失败、不可用的状态卡片上也有删除入口。
  - 删除后回到比赛库，显示同样的提示。比赛名和 id 不会出现在网址里。
- **`/account`「账户与数据」**
  - 内容：账户信息（昵称、登录方式、SteamID64）、保存了哪些数据，以及删除账户。
  - 删除账户前要输入「删除账户」确认。
  - 开发模式下不显示删除按钮，只说明账户删除要用 Steam 登录，并引导去比赛库逐场删除。
  - 顶栏的账户名链接到这里。
- **`/privacy`「隐私说明」**
  - 公开页面，不需要登录。
  - 服务器地区和联系方式来自构建时变量：
    - `NEXT_PUBLIC_DATA_REGION`：未设置时显示「海外 VPS，具体地区由站长部署时选定」。
    - `NEXT_PUBLIC_PRIVACY_CONTACT`：邮箱显示为 mailto 链接，http(s) 网址显示为链接，其他内容显示为纯文本。同场的其他玩家从没被邀请过，这是他们提出移除的唯一途径，所以 production 必填：`deploy.sh` 在它为空时拒绝部署。未设置时（本地和预览构建）页面如实写明「本站没有公开联系方式」，受邀用户找邀请人，其他玩家请上传者删除。
- **页脚**
  - 出现在比赛库、复盘页、账户页、隐私页，以及登录墙、回调、未受邀、错误和 404 页面。
  - 内容：「隐私说明」链接，和「本站与 Valve Corporation 无关联。Counter-Strike、CS2 和 Steam 是 Valve 的商标。」
- 确认框统一使用 `frontend/components/feedback/ConfirmDialog.tsx`：
  - 打开后焦点移进对话框，Tab 键限制在对话框内；Esc 或「取消」关闭，并把焦点还给打开它的按钮；
  - 请求进行中两个按钮都禁用，焦点暂放在对话框面板上；
  - 出错时在对话框里显示，焦点回到第一个可用的控件（输入框或「取消」）。面板本身不算在 Tab 循环里；焦点万一跑到对话框外，下一次按 Tab 会把它带回来，Esc 仍然关闭对话框。

## 删除后仍然存在的数据

删除立即作用于网站的数据库和存储。下面这些地方的副本不单独清除，按各自的规则过期：

| 位置 | 内容 | 保留多久 |
| --- | --- | --- |
| 远端数据库备份 `<备份桶>/postgres/` | 整库 dump | 30 天（`BACKUP_REMOTE_KEEP_DAYS`） |
| 本地数据库备份 `/var/backups/cs2coach/pg-*.dump` | 整库 dump | 最新 14 份（`BACKUP_LOCAL_KEEP`），同时不超过 30 天：连续几晚备份失败时，最新 14 份也可能超过 30 天，按时间的规则会删掉它们 |
| 文件备份 `<备份桶>/artifacts/` → `artifacts-replaced/<UTC>/` | 被删除的对象 | 下一次每日备份（最多一天后）把它从镜像挪到 `artifacts-replaced/`，再保留 29 天，合计不超过 30 天 |
| 容器日志 | 访问日志：IP、浏览器标识、网址（含比赛 id 和搜索词） | 按大小轮转，每个服务 10 MB × 5 份，不按时间删除 |
| Redis AOF | 会话记录 | 到下一次 AOF 重写为止；这些会话已经被撤销 |
| 上传账本 | 不透明的账户 ID 和上传时间 | 最多 24 小时 |
| 恢复遗留：`pre-restore-*.dump`、`cs2coach_pre_restore_*` 库、`cs2coach_restore_check` | 整库 | 直到站长手动删除；运维手册要求 30 天内删除 |
| 渲染机工作目录 | 源 `.dem` 和片段 | production 模板不部署渲染机。如果启用了渲染机，删除时已经完成的任务，其工作目录不会被清理 |
| 渲染机上 CS Demo Manager 的数据库 | CSDM 适配器每渲染一场都会 `csdm analyze`，把整场比赛（所有玩家的 SteamID64、昵称、击杀和位置）导入 CSDM 自己的 PostgreSQL | 直到操作员在 CSDM 里手动删除；runner 的清理只管 `WORK_DIR`。production 不部署渲染机 |
| 邀请名单 `STEAM_LOGIN_ALLOWLIST` | 受邀者的 SteamID64（在 `deploy/.env.production` 和站长的密码管理器里） | 直到站长把人移出名单；删除账户不会改动它 |

这一版不清理备份里单个用户的数据：那需要重写整份 dump，而备份本身会在 30 天后过期。

`backup.sh` 在失败的晚上也会执行上面的保留规则（dump、上传或镜像任一步失败时，退出前照样清理过期的 dump 和 `artifacts-replaced/`），所以某一晚备份失败不会让旧副本超过 30 天。前提是每日备份的定时器还在运行。

## 代用户删除

用户自己无法删除时，由站长用运维命令代为删除，例如：
- 同场的其他玩家（从没被邀请）请求移除一场比赛；
- 已被移出邀请名单、无法再登录的用户要求删除数据；
- 从备份恢复后，要重做备份之后发生的删除（见 [VPS 部署](vps_deploy_v1.md) 第 7 步）。

命令在 api 容器里运行，走的是和网站完全相同的删除流程：行在一次事务里删掉，存储由删除任务清理并自动重试，账户的所有会话一起失效。不要改为在 psql 里直接删行，那样会跳过存储清理。

```bash
dc exec api python -m app.cli.delete_data demo <demo_id>...                 # 只列出会删除什么
dc exec api python -m app.cli.delete_data demo <demo_id>... --yes           # 删除这些比赛（不论属于谁）
dc exec api python -m app.cli.delete_data account --steam-id <SteamID64>... --yes
dc exec api python -m app.cli.delete_data account <owner_id>... --yes
```

- 不带 `--yes` 时只显示会删除什么（比赛的 owner、状态和创建时间；账户的比赛数量），什么都不删。
- 账户删除和网站上一样只在 `AUTH_MODE=production` 下可用；比赛删除在任何模式下都可用。
- 退出码：`0` 表示全部删除，`1` 表示有 id 没找到或没有加 `--yes`，`2` 表示用法错误。
- 找第三方要求移除的那场比赛，需要对方提供足够的信息（例如比赛时间、地图、上传者）；按 id 删除前先不带 `--yes` 看一眼。

## 从备份恢复

用旧备份恢复数据库，会把备份之后删除的比赛和账户带回来；备份之后写入的删除任务也会一起丢失。恢复生产库之后必须重做这些删除，并在 30 天内删掉恢复遗留。步骤见 [VPS 部署](vps_deploy_v1.md) 第 7 步。

## 测试矩阵

后端删除测试通过 engine connect 事件打开 SQLite 外键检查（`PRAGMA foreign_keys=ON`），行为才和 Postgres 一致。存储用临时目录里的本地后端。

| 场景 | 断言 |
| --- | --- |
| 删除一场比赛 | 比赛、任务、建议、评价的行都不在；这场比赛前缀下没有对象；删除任务处理完后被删除 |
| 配额 | 删除后 `GET /uploads/quota` 的每日已用次数不变，上限照样生效 |
| 另一个 owner | 同时存在的另一个 owner 的行和对象都不受影响；用别人的 id 删除得到 `404`，且没有任何改动 |
| 解析中删除 | worker 不退出，随后继续处理下一个任务；已写好的回放被删除 |
| 渲染媒体上传或回调时删除 | 返回 `404`，刚上传的视频被删除 |
| 重复删除和并发删除 | 一个 `204`，一个 `404`，没有 `500` |
| 删除账户 | 为同一 owner 预先写入的第二个会话随后得到 `401`；账户、身份、Steam 连接和比赛、所有比赛和对象都不在 |
| 账户删除后的上传 | 返回 `401 account_deleted`，不留行，也不留对象 |
| 截止点之后留下的孤儿对象 | 账户已不在时，最后一次清扫删掉截止点之后才写入的对象，任务随后结束；同一 owner ID 重新建号时，新数据保留 |
| 登录与删除账户竞争 | 删除在身份解析和签发会话之间提交时，登录返回 `401`，刚签发的会话已撤销；随后再登录得到新的空账户 |
| 解析中删除（长解析） | 下一次检查就停掉解析，不写回放，不标记失败；检查失败不算删除 |
| 本地存储并发 | 删除一场比赛的清理不会删掉同一 owner 的目录，同一 owner 另一场比赛的并发写入不受影响 |
| 版本控制的 S3 桶 | 清理删除全部版本和删除标记，第二遍什么也找不到；R2（NotImplemented）仍用不带版本的删除 |
| render-worker 的 `404` | 只有 `410` 或 API 自己的 `404` 算作任务已删除；HTML 或其他 `404` 不删工作目录和手动录制的视频 |
| 运维命令 `app.cli.delete_data` | 不带 `--yes` 什么都不删；按比赛 id 或 SteamID64 删除，会话随账户失效；非 production 拒绝删除账户 |
| Steam 导入的比赛 | `steam_matches` 行保留，`demo_id` 为空，状态回到可导入，派生字段清空 |
| 开发模式 | `DELETE /auth/account` 返回 `409 account_deletion_unavailable`，什么都不删 |
| 前端 | 确认框（焦点、Esc、输入确认、忙碌、错误）；比赛库和复盘页的删除流程（成功、`404` 当作成功、失败提示）；`/account` 在开发和生产模式下的显示；`/privacy` 未登录可渲染，包含免责声明，联系方式有回退文字和已配置两种情况 |
| 生产冒烟 `prod_smoke.sh` | `GET /privacy` 返回 200 HTML；匿名 `DELETE /auth/account` 和 `DELETE /demos/{id}` 由 FastAPI 返回 401 JSON |

## 本地手动检查

本地 Docker 环境（http://localhost:3000、:8000）里，默认开发 owner 名下是真实的比赛。手动检查时：

- 不要对默认开发 owner、也不要对任何已有的比赛 id 调用删除接口。
- API 检查只用一次性的 `X-Dev-User-Id`，删除只针对为它新建的比赛。
- 在界面上检查时，先新建一场示例比赛，只删除这一场。

```bash
OWNER=del-test-1
curl -X POST http://localhost:8000/uploads/mock -H "X-Dev-User-Id: $OWNER"         # 记下返回的比赛 id
curl -i -X DELETE http://localhost:8000/demos/<id> -H "X-Dev-User-Id: $OWNER"      # 204
curl -i -X DELETE http://localhost:8000/demos/<id> -H "X-Dev-User-Id: $OWNER"      # 404
curl -i -X DELETE http://localhost:8000/auth/account -H "X-Dev-User-Id: $OWNER" \
  -H "Content-Type: application/json" -d '{"confirm":"delete-my-account"}'          # 409
```

相关文档：[建议评价](coaching_feedback_v1.md)、[Steam 登录与账号](steam_auth_accounts_v1.md)、[安全上传与对象存储](object_storage_safe_artifact_intake_v1.md)、[VPS 部署](vps_deploy_v1.md)。
