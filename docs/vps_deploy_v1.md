# VPS 部署 V1：一台海外 VPS 上线邀请制内测

整个网站跑在一台 VPS 上：Caddy 负责 HTTPS（Let's Encrypt 自动签发）和路由，后面是 Next.js、FastAPI、解析 worker、PostgreSQL、Redis；`.dem`、回放等文件放在私有的 S3 兼容桶（推荐 Cloudflare R2），数据库和文件每天备份到另一个桶。

| 文件 | 作用 |
| --- | --- |
| `deploy/Caddyfile` | 单一 HTTPS 源站的路由、上传大小、超时、安全头、JSON 访问日志 |
| `docker-compose.prod.yml` | 叠加在 base + preview 之上：加 `caddy`，去掉 api/frontend 的公开端口，数据库和 Redis 密码必填，postgres/redis 只在内部网络上，Redis 持久化，worker 内存上限，自动重启，日志轮转 |
| `deploy/env.production.example` | 生产环境变量模板（只有占位符）；真实文件是 git 忽略的 `deploy/.env.production` |
| `scripts/deploy/bootstrap.sh` | 新 VPS 初始化（root 运行一次，可重复运行） |
| `scripts/deploy/deploy.sh` | 预检（迁移兼容、新迁移前自动备份）、更新、构建或复用按提交打标签的镜像、启动、等 `/health` 和 `/health/worker`、冒烟、清理旧镜像；`--rollback <sha>` 回滚 |
| `scripts/deploy/prod_smoke.sh` | 匿名生产冒烟（只用 curl，含 `/health/worker`），并打印手动 Steam 登录清单 |
| `scripts/deploy/backup.sh` + `cs2coach-backup.{service,timer}` | 每天 03:30 UTC 备份数据库并镜像文件桶；成功后 ping `BACKUP_PING_URL`，失败时发告警 |
| `scripts/deploy/watch.sh` + `cs2coach-watch.{service,timer}` | 每 5 分钟健康巡检，状态变化时向 `ALERT_WEBHOOK_URL` 发一行告警，全部正常时 ping `DEADMAN_PING_URL` |
| `scripts/deploy/restore.sh` | 恢复演练（默认恢复到临时库），或显式确认后恢复生产库；`--no-start` 恢复后不启动 api/worker（跨迁移回滚用） |

以下命令里的 `dc` 指：

```bash
alias dc='docker compose --env-file deploy/.env.production -f docker-compose.yml -f docker-compose.preview.yml -f docker-compose.prod.yml'
```

## 0. 只给朋友用的最简配置

第一次上线只给自己的朋友用：按下面准备资源（代替第 1 步，只引用其中的具体做法），然后照第 2–10 步操作。

1. **VPS**：Ubuntu 24.04，2–4 vCPU / 4 GB 内存就够。worker 容器（含解析子进程）默认最多用 3 GB（`WORKER_MEM_LIMIT=3g`，不用 swap）：超出的大 demo 解析会失败并报内存不足，而不是把整机拖进 swap；`bootstrap.sh` 建的 4 GB swapfile 给其他服务兜底。磁盘至少 40 GB，分片上传的暂存要留出约 11 GiB（见第 1 步第 1 条）。机房选东京、新加坡或香港，约 US$10–20/月。
2. **域名（免费 DuckDNS）**：
   - 在 https://www.duckdns.org 登录，建一个子域名 `<name>.duckdns.org`，`current ip` 填 VPS 的公网 IPv4，保存后就是一条指向 VPS 的 A 记录。
   - `duckdns.org` 在 Public Suffix List 上（2026-09-25 核实），每个子域名单独计算 Let's Encrypt 的签发限额；Caddy 默认的 HTTP-01 验证直接可用，不需要插件，也不需要把 DuckDNS token 放到 VPS 上。
   - **不要用 `sslip.io` 或 `nip.io`**：它们不在该列表上，全体用户共享每周 50 张证书的限额，证书可能签不下来，首次部署会卡在等待 `/health` 直到超时。
   - 最稳的替代是买一个域名（约 US$10/年），按第 1 步第 2 条加 A 记录。
3. **存储**：Cloudflare R2 免费额度（10 GB）。建两个私有桶（artifacts、backups），各配一个只限定该桶的 token，做法同第 1 步第 3 条。
4. **Steam Web API key**：同第 1 步第 4 条，域名填 `<name>.duckdns.org`。
5. **邀请名单**：`STEAM_LOGIN_ALLOWLIST` 填朋友们的 SteamID64，逗号分隔。让朋友自己查：打开自己的 Steam 个人资料页，网址是 `https://steamcommunity.com/profiles/7656119…` 时，后面那串数字就是；设置过自定义网址（`/id/<名字>`）的，把个人资料链接粘到 steamid.io 这类查询站，取 steamID64 那一项。它是 17 位数字，以 `7656119` 开头。以后加人：改这一项后重跑 `deploy.sh`。
6. **配额**：保持默认（每人每天 10 份、同时 2 份在解析），`RENDER_CLIPS_ENABLED=0`。
7. **备份照开**（第 6 步），放在 R2 上成本很低。
8. **env 文件**：模板只需替换占位符，第 3 步的 `sed` 里域名写 `<name>.duckdns.org`。
9. **隐私页**：`NEXT_PUBLIC_PRIVACY_CONTACT`（你的联系方式）必填，`deploy.sh` 在它为空时拒绝部署：同场的其他玩家从没被邀请过，只能通过它提出移除。`NEXT_PUBLIC_DATA_REGION`（VPS 所在地区，如"日本东京"）建议填上，不填时显示通用说明。两者都显示在公开的 `/privacy` 页面上。
10. **告警照开**（第 6 步）：至少填 `ALERT_WEBHOOK_URL`（如一个 ntfy 主题）并启用 `cs2coach-watch.timer`；再用 healthchecks.io 免费版配 `DEADMAN_PING_URL` 和 `BACKUP_PING_URL`。

只给朋友用时**刻意跳过**，扩大内测前必须补上：

- 指标（现在只有 `watch.sh` 的状态告警、容器日志、`/health` 和 `/health/worker`）；
- 数据库迁移演练；
- 真实 demo 语料门禁（corpus gate，见 [2D 内测上线计划](rules_2d_beta_launch_v1.md)）；
- 确认雷达图可以公开分发：第三方（MIT/GPL）雷达图已经换成本项目从 CS2 导航网格渲染的图（见 `frontend/public/maps/ATTRIBUTION.md`），但这些图由 Valve 的游戏数据派生，分发条款还没有单独确认。

隐私说明页和删除比赛、删除账户已经有了，见 [数据删除](data_deletion_v1.md)。

## 1. 准备资源（完整配置）

1. **VPS**：Ubuntu 24.04，推荐 4 vCPU / 8 GB 内存 / 80 GB 磁盘（单个解析子进程内存上限 4 GB；8 GB 机器把 `WORKER_MEM_LIMIT` 设为 `6g`）。面向国内玩家选东京、新加坡或香港，优先到国内线路好的机房。海外机房不需要 ICP 备案。
   - **磁盘规划**：浏览器分片上传时，未完成上传的分片暂存在这台 VPS 上（Docker 命名卷 `cs2coach_upload-staging`，挂进 api 和 worker 的 `/data/upload-staging`），完成后才转存到 R2。全站同时最多 `UPLOAD_SESSION_GLOBAL_LIMIT`（默认 6）个未完成的上传，每个最大 1 GiB，所以暂存最多约 6 GiB；建新上传时还要求盘上剩余至少 `UPLOAD_STAGING_MIN_FREE_BYTES`（默认 5 GiB），这部分留给同一块盘上的 Postgres、Redis、Docker 镜像和日志。80 GB 的盘按"系统和镜像约 15 GB + 数据库和本地备份 + 暂存 6 GiB + 余量 5 GiB"规划绰绰有余（`deploy.sh` 只保留最近 3 次部署的镜像，构建缓存压到 5 GB 以内；`watch.sh` 在使用率到 85% 时告警）；盘小时调小 `UPLOAD_SESSION_GLOBAL_LIMIT`，不要把余量调到 0。余量不足时新上传返回 `503 upload_storage_full`，已有的上传不受影响。
   - 暂存在命名卷上，部署和重启 api 容器后上传仍能续传；放弃、24 小时过期、删除账户的上传由 API 和 worker 的清扫删除。查看占用：`docker system df -v | grep upload-staging`。
2. **域名与 DNS**：给一个子域名（如 `coach.example.com`）加 A 记录（有 IPv6 再加 AAAA）指向 VPS。用 Cloudflare DNS 时必须是**仅 DNS（灰色云朵）**：Cloudflare 代理在免费版把请求体限制在 100 MB，而 `.dem` 最大 1 GiB；证书也由 VPS 上的 Caddy 直接签发。
3. **对象存储（Cloudflare R2）**：
   - 建两个私有桶：`cs2coach-artifacts`（应用文件）和 `cs2coach-backups`（备份）。不要开启公开访问或 r2.dev 域名。
   - 在 R2 → Manage API tokens 建两个 token，权限都选 Object Read & Write，并且各自只限定一个桶：应用 token 只能访问 artifacts 桶，备份 token 只能访问 backups 桶。
   - 记下 Access Key ID、Secret Access Key 和 endpoint `https://<account-id>.r2.cloudflarestorage.com`；region 填 `auto`。
4. **Steam Web API key**：在 https://steamcommunity.com/dev/apikey 用运营账号申请，域名填上面的子域名。得到 32 位十六进制 key。
5. **内测名单**：收集受邀玩家的 SteamID64（17 位数字）。

## 2. 初始化 VPS

`bootstrap.sh` 克隆的是 GitHub 上的 `main`，所以部署套件要先合并并推送到 `main`。

```bash
# 在本机：把脚本传上去（私有仓库无法用 raw URL 下载）
scp scripts/deploy/bootstrap.sh root@<vps-ip>:/root/
# 在 VPS 上，以 root：
bash /root/bootstrap.sh                    # 默认仓库 https://github.com/luc1ferxx/CS2.git
# 私有仓库：先给 cs2coach 用户配只读 deploy key，再传 SSH 地址
# bash /root/bootstrap.sh git@github.com:luc1ferxx/CS2.git
```

脚本会：升级系统并开启 unattended-upgrades；从 Docker 官方 apt 源装 Docker Engine 和 compose 插件（需要 Compose ≥ 2.24，`!reset` 语法依赖它）；没有 swap 时建 4 GB swapfile；ufw 只放行 OpenSSH、80/tcp、443/tcp、443/udp（SSH 改过端口时先设 `SSH_PORT=<端口>`）；建 `cs2coach` 用户并加入 docker 组（等同 root 权限，只给运维用）；克隆或快进仓库到 `/opt/cs2coach`；装 rclone；建 `/var/backups/cs2coach`。

注意：Docker 发布的端口会绕过 ufw，所以只有 Caddy 发布端口。api 用 `--forwarded-allow-ips=*` 信任转发的客户端 IP（登录限流按 IP 计），这只在 api 端口不公开时才安全——**不要给 api 加 `ports:`**。postgres 和 redis 只在 `internal` 的 `backend` 网络上，只有 api 和 worker 连得到（caddy、frontend 连不到）；Redis 还要密码（`REDIS_PASSWORD`），因为会话以未签名的形式存在 Redis 里，能写 Redis 就能伪造任何人的登录。

## 3. 填写 `deploy/.env.production`

```bash
su - cs2coach
cd /opt/cs2coach
cp deploy/env.production.example deploy/.env.production
chmod 600 deploy/.env.production
sed -i 's/coach\.example\.com/<你的域名>/g' deploy/.env.production
nano deploy/.env.production                # 替换所有 CHANGE_ME
```

| 变量 | 取值 |
| --- | --- |
| `SITE_DOMAIN`、`*_PUBLIC_URL`、`CORS_ORIGINS`、`NEXT_PUBLIC_API_BASE_URL` | 同一个域名；URL 都是 `https://<域名>`，不带路径（生产环境拒绝前后端分域） |
| `ACME_EMAIL` | 你的邮箱，Let's Encrypt 证书到期提醒用 |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24`（只在数据库卷第一次创建时生效） |
| `REDIS_PASSWORD` | `openssl rand -hex 24`；api 和 worker 的 `REDIS_URL` 里带它，解析子进程拿不到。改了之后重跑 `deploy.sh`（会话和队列在 Redis 的 AOF 里，不会丢） |
| `WORKER_MEM_LIMIT` | worker 容器（含解析子进程）的内存上限：4 GB VPS 用默认 `3g`，8 GB VPS 用 `6g`。上线后按 worker 日志 `job_done` 行的 `peakRssMiB` 调整 |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`，生成一次后不要随意更换 |
| `RENDER_WORKER_TOKEN` | `openssl rand -hex 32` |
| `STEAM_WEB_API_KEY` | 第 1 步申请的 key |
| `STEAM_LOGIN_ALLOWLIST` | 受邀 SteamID64，逗号分隔；`*` 表示任何人都能登录，内测不要用 |
| `OBJECT_STORAGE_*` | artifacts 桶和应用 token |
| `BACKUP_*` | backups 桶和备份 token |
| `NEXT_PUBLIC_BETA_CONTACT_URL` | 可选；未受邀页面上"申请内测资格"的链接 |
| `NEXT_PUBLIC_PRIVACY_CONTACT` | 必填（`deploy.sh` 检查）；显示在 `/privacy` 的隐私问题和删除请求联系方式。邮箱会变成 mailto 链接，`http(s)://` 网址会变成链接，其他内容原样显示为文字 |
| `NEXT_PUBLIC_DATA_REGION` | 可选；显示在 `/privacy` 的服务器所在地区，如 `日本东京`。不填时显示"海外 VPS，具体地区由站长部署时选定" |
| `ALERT_WEBHOOK_URL`、`DEADMAN_PING_URL`、`BACKUP_PING_URL`、`WATCH_*` | 可选，留空即关闭；见第 6 步 |

整份文件存一份到密码管理器。丢了 `STEAM_CREDENTIAL_ENCRYPTION_KEY`，已保存的 Steam 比赛授权就无法解密；备份桶里不包含这份文件。

## 4. 首次部署

确认 DNS 已经解析到 VPS，然后以 `cs2coach` 用户：

```bash
cd /opt/cs2coach
bash scripts/deploy/deploy.sh
```

脚本会：

1. 检查 env 文件存在且没有占位符，`git fetch` 目标提交。
2. **预检，不动任何运行中的服务**：读数据库的 `app_schema_migrations`，和目标提交 `backend/app/migrations/runner.py` 里的迁移版本比对。数据库里有目标不认识的版本（比如回滚到加迁移之前的提交），就直接退出并打印"先恢复再回滚"的命令（第 9 步）；目标带来新迁移的，记下待会要先备份。首次部署还没有数据库，跳过这一步。
3. 快进到目标提交，用 Caddy 镜像校验 `deploy/Caddyfile`，`dc build --pull`（回滚且该提交的镜像还在时跳过构建，见第 9 步）。
4. 有新迁移时先跑 `backup.sh --db-only`（只 dump 数据库并上传，不镜像文件桶），dump 名和迁移版本写进 `deploy/.deploy-history`。备份失败就在动服务前停下。
5. `dc up -d`（api 启动失败时打印日志并停下），Caddyfile 有变化时重启 caddy。
6. 最多等 300 秒（`DEPLOY_HEALTH_TIMEOUT_SECONDS`）直到 `https://<域名>/health` 返回 200；再等 `/health/worker` 返回 200，并且 worker 容器已经连续运行 95 秒以上：旧 worker 的心跳在它停止后 90 秒内仍算存活，刚重建的 worker 要跑过这个窗口，200 才证明是它自己的心跳。worker 在等待期间重启 2 次或更多，或超时，打印 worker 日志并停下。
7. 运行 `prod_smoke.sh`，把这次部署追加到 `deploy/.deploy-history`。
8. 给正在运行的镜像打上 `cs2coach-backend:<sha>`（api 和 worker 共用）和 `cs2coach-frontend:<sha>` 标签，同时标成 `latest`（不带 `DEPLOY_SHA` 的手动 `dc up` 用它）；只保留最近 3 次部署的 `<sha>` 标签，`docker image prune -f` 清掉悬空镜像，构建缓存压到 5 GB 以内。最后打印当前提交。

首次构建前端约需 5–10 分钟；证书签发要求 80 和 443 端口能从公网访问。部署期间 `watch.sh` 自动跳过巡检，不会误报。

部署前备份失败时（比如备份桶出故障），确实要先上线紧急修复，可以加 `--skip-backup`：历史里记为 `backup=skipped`，这次就没有回退用的 dump 了。

路由（`deploy/Caddyfile`）：`/auth/steam/login`、`/auth/steam/callback`、`/auth/me`、`/auth/logout`、`/auth/account` 等后端认证路由、`/steam/*`、`/demos`、`/demos/<id>/…`、`/uploads/*`、`/coaching/*`、`/health`、`/health/worker`、`/render-worker/*`、`/render/*` 进 FastAPI；`PATCH` 和 `DELETE /demos/<id>` 进 FastAPI，`GET /demos/<id>` 是 Next.js 复盘页；其余（`/`、`/dashboard`、`/account`、`/privacy`、`/auth/callback`、`/_next/*`、`/maps/*`）进 Next.js。`/uploads/*` 放宽到 1100 MB 请求体（旧的单请求 `POST /uploads/demo`），读写超时 2 小时；浏览器的分片上传 `PUT /uploads/sessions/<id>/parts/<i>` 另限 34 MB 一片。分片请求只认请求头 `X-Upload-Token` 里的上传令牌，Caddy 的访问日志把这个头删掉。

站点走 HTTP/2 和 HTTP/3（`docker-compose.prod.yml` 发布 443/tcp 和 443/udp），浏览器会把 4 个并行分片复用在同一条连接上，提速可能不如预期。这一版不改协议；部署后用 API 日志里每个完成的上传一行的 `Upload session completed: bytes=… parts=… part_puts=… seconds=…` 测速（不含文件名和账户），再决定是否调整分片大小（`UPLOAD_PART_BYTES`）和并行数（`UPLOAD_MAX_PARALLEL_PARTS`）。

## 5. 冒烟与手动 Steam 登录检查

```bash
bash scripts/deploy/prod_smoke.sh https://<域名>
```

它检查：证书有效、HTTP 跳转 HTTPS、`/health` 返回 `{"status":"ok"}`、`/health/worker` 返回 200（解析 worker 的心跳正常；404 说明 Caddy 没把它路由到 FastAPI）、`/dashboard` 是 HTML 且带 HSTS 等安全头、`GET /demos/<id>` 由 Next.js 返回、`GET /privacy` 是 200 的 HTML、匿名访问 API（`/demos`、`/demos/<id>/status`、`PATCH /demos/<id>`、`/uploads/demo`、建上传会话 `POST /uploads/sessions`、`GET /uploads/sessions/current`、`/auth/me`）都是 401、不带令牌的分片 `PUT` 由 FastAPI 返回 404 JSON（证明这一条免于 cookie 检查，但没有令牌照样被拒）、匿名 `DELETE /auth/account` 和 `DELETE /demos/<id>` 都由 FastAPI 返回 401 JSON（证明它们没有落到 Next.js）、`/docs` 和 `/openapi.json` 是 404、`/auth/steam/login` 跳转到 `https://steamcommunity.com/openid/login` 并下发带 `Secure; HttpOnly` 的 `__Host-` 状态 cookie。

然后在浏览器里手动走一遍（脚本最后也会打印一份手动清单）：

1. 受邀账号点"通过 Steam 登录"，回到 `/dashboard`。
2. 不在名单里的账号登录后停在 `/auth/callback?error=not_invited`。
3. 上传一份真实 `.dem`，状态走到完成。这一步同时验证 R2 写入（应用用条件写入 `If-None-Match`）。再上传一份，传到一半时断网几秒：上传暂停后自动继续；传到一半时刷新页面：比赛库提示「有一个未完成的上传」，重新选择同一个文件后接着传。完成后 `dc logs api | grep 'Upload session completed'` 能看到一行测速记录。
4. 打开复盘页：回放播放、切换回合、战术地图和建议卡片正常。
5. 退出登录后，`/dashboard` 要求重新登录。
6. 退出登录的状态下打开 `/privacy`：不需要登录，地区和联系方式是你配置的值，页脚有"与 Valve 无关联"声明。
7. 在比赛库里用行菜单「删除比赛…」删掉第 3 步上传的那场，它从列表消失，今天的上传次数不变。首次部署时最好再用一个专门的受邀测试账号，在 `/account` 走一遍删除账户：该账号在另一个浏览器里的登录也要同时失效。

需要带会话的完整脚本冒烟时，用 `scripts/cloud_preview_smoke.py`（要设 `AUTH_SESSION_COOKIE` 和 `SAMPLE_DEMO_PATH`，每次会占用该账号一次上传配额）。

## 6. 启用备份与告警

先在 `deploy/.env.production` 填好告警变量（见下面"健康告警"），然后以 root：

```bash
cp /opt/cs2coach/scripts/deploy/cs2coach-backup.service /opt/cs2coach/scripts/deploy/cs2coach-backup.timer /etc/systemd/system/
cp /opt/cs2coach/scripts/deploy/cs2coach-watch.service /opt/cs2coach/scripts/deploy/cs2coach-watch.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cs2coach-backup.timer cs2coach-watch.timer
systemctl start cs2coach-backup.service     # 先手动跑一次
journalctl -u cs2coach-backup.service -n 50
systemctl list-timers 'cs2coach-*'
```

每次备份：`pg_dump -Fc` 到 `/var/backups/cs2coach/pg-<UTC>.dump`，确认非空且 `pg_restore --list` 能解析 → 上传到 `<备份桶>/postgres/` → 本地保留最新 14 份、桶里删除 30 天前的 → 把 artifacts 桶 `rclone sync` 到 `<备份桶>/artifacts/`，被删除或覆盖的对象挪到 `artifacts-replaced/<UTC>/`，30 天后清掉。任何一步失败都会打印 `BACKUP FAILED` 并以非零退出（`systemctl status` 会显示 failed），同时向 `ALERT_WEBHOOK_URL` 发一行 `FAIL backup: during <步骤> (exit <码>)`；完整备份成功后 GET `BACKUP_PING_URL`。`deploy.sh` 在新迁移前调用的 `backup.sh --db-only` 只做 dump 和上传，不镜像文件桶，也不 ping。

不在备份里的：Redis（会话和队列，丢了只需重新登录）、Caddy 证书（会自动重签）、`deploy/.env.production`（自己存密码管理器）。备份桶如果支持版本控制、对象锁或生命周期规则，在存储商那边打开是更强的保护。

**已删除数据在备份里的保留期**（`/privacy` 对用户这样承诺，改保留参数时要一起改）：
- 用户删除比赛或账户后，数据库 dump 里的副本在桶里最多保留 30 天（`BACKUP_REMOTE_KEEP_DAYS`），本地最多保留最新 14 份（`BACKUP_LOCAL_KEEP`）。部署前 dump 也是 `pg-*.dump`，按同样的规则清理。
- 被删除的文件在下一次备份时从镜像挪到 `artifacts-replaced/<UTC>/`，30 天后清掉。
- 打开版本控制或对象锁会让被删除的数据保留得更久，要同步更新隐私页的说法。

### 健康告警（watch.sh）

不上 Prometheus，只做最小可用：`cs2coach-watch.timer` 每 5 分钟以 `cs2coach` 用户跑一次 `scripts/deploy/watch.sh`。

| 检查 | 失败条件 |
| --- | --- |
| `health`、`health/worker` | 经 Caddy 访问 `https://<域名>/health`、`/health/worker` 不是 200 |
| `docker`、`compose` | Docker 不响应；compose 文件或 env 文件解析失败 |
| `svc:<服务>` | 某个服务没有容器、容器不在运行，或重启次数比上一轮多（`restart: unless-stopped` 下的重启循环） |
| `disk:<挂载点>` | `/`、Docker 数据目录、`BACKUP_LOCAL_DIR` 所在盘的使用率达到 `WATCH_DISK_PERCENT`（默认 85，0 关闭） |
| `backup` | 最新的本地 `pg-*.dump` 超过 `WATCH_BACKUP_MAX_AGE_HOURS` 小时（默认 26，0 关闭），或一份都没有 |

- 只在某项由正常变失败、或由失败恢复时告警，同一轮的变化合成一行，例如 `[cs2coach] FAIL health/worker: GET /health/worker -> HTTP 503`。内容只有检查名、状态和简短原因，不含账户、SteamID、文件名或 IP。
- 发送失败时不保存新状态，下一轮重发（脚本以非零退出，`journalctl` 里能看到）。全部正常时 GET `DEADMAN_PING_URL`。
- `deploy.sh` 和 `restore.sh --into-production` 运行期间巡检自动跳过（它们在 `~cs2coach/.local/state/cs2coach-watch/maintenance` 放标记，超过 1 小时的标记视为残留，不再跳过）。巡检状态也在这个目录。

告警变量（`deploy/.env.production`，都可选，留空即关闭）：

- `ALERT_WEBHOOK_URL`：普通 URL 用 POST 把告警作为纯文本正文发出，适合 ntfy（`https://ntfy.sh/<足够长的随机主题>`，手机装 ntfy 订阅这个主题）。URL 里含 `{message}` 时，把 URL 编码后的告警填进去用 GET 发，适合 Telegram 机器人（`https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>&text={message}`）和 Server酱（`https://sctapi.ftqq.com/<SendKey>.send?title={message}`）。URL 通过 curl 的标准输入传入，不会出现在 `ps` 里。
- `DEADMAN_PING_URL`：healthchecks.io 之类的"死人开关"。建一个周期 5 分钟、宽限 15 分钟以上（容得下一次部署）的检查，把 ping 地址填这里。VPS 整机宕机、timer 停了、Docker 和网络一起挂了，watch.sh 自己发不出告警，这时由 ping 中断触发外部告警。
- `BACKUP_PING_URL`：在 healthchecks.io 再建一个周期 1 天、宽限几个小时的检查，每次完整备份成功后 ping。

装好后以 `cs2coach` 测试：

```bash
bash scripts/deploy/watch.sh --test-alert   # 发一条测试告警
bash scripts/deploy/watch.sh                # 手动巡检一轮，打印每项 ok/FAIL
journalctl -u cs2coach-watch.service -n 50
```

第一次巡检时还没有备份的话，`backup` 会先告警一次，第一次备份完成后恢复。外部拨测（如 UptimeRobot 免费版）每 5 分钟探 `https://<域名>/health` 和 `https://<域名>/health/worker`，覆盖"VPS 到公网"这一段，是 watch.sh 的补充。

## 7. 恢复演练

建议每月一次，也在第一次备份后马上做一次：

```bash
bash scripts/deploy/restore.sh pg-20260925T033000Z.dump      # 从备份桶取；也可以传本地文件路径
```

默认恢复到临时库 `cs2coach_restore_check`，打印 `accounts`、`demos`、`demo_jobs`、`coaching_events` 的行数，生产库不受影响。和生产库对比：

```bash
dc exec -T postgres psql -U cs2coach -d cs2coach -c "select count(*) from demos"
```

真正恢复生产库必须同时带两个参数：

```bash
bash scripts/deploy/restore.sh <dump> --into-production --confirm-production-restore
```

它会停掉 api 和 worker，先把当前库另存为 `/var/backups/cs2coach/pre-restore-<UTC>.dump`，把当前库**改名**为 `cs2coach_pre_restore_<UTC>`（不删除），再恢复到新建的 `cs2coach`，最后重新启动 api 和 worker。确认网站正常、并做完下面的"重做删除"后，再手动删除旧库。

文件恢复需要手动用 rclone 把 `<备份桶>/artifacts/` 拷回 artifacts 桶。

### 恢复生产库之后：重做删除，清理遗留

从备份恢复，会把备份之后用户删除的比赛和账户带回来；数据库里的删除任务也回到了备份时的状态。`/privacy` 向用户承诺，恢复后会重新执行那之后的删除。所以恢复生产库之后必须做下面三步。

1. **找出备份之后被删除的数据。** 改名保留的旧库 `cs2coach_pre_restore_<UTC>` 是恢复前一刻的状态。恢复后的库里有、旧库里没有的比赛和账户，就是备份之后被删除的：

   ```bash
   OLD=cs2coach_pre_restore_<UTC>
   for q in "select id from demos" "select owner_id from accounts"; do
     dc exec -T postgres psql -U cs2coach -d cs2coach -Atc "$q" | sort >/tmp/restored.txt
     dc exec -T postgres psql -U cs2coach -d "$OLD" -Atc "$q" | sort >/tmp/before.txt
     echo "== $q -- deleted after the backup:"; comm -23 /tmp/restored.txt /tmp/before.txt
   done
   rm -f /tmp/restored.txt /tmp/before.txt
   ```

   旧库损坏、无法查询时，只能从访问日志里找：`dc logs caddy | grep '"method":"DELETE"'` 里的 `DELETE /demos/<id>`。日志按大小轮转，可能不全；账户删除请求在日志里看不出是哪个账户。
2. **重做这些删除。** 用运维命令代为删除，它走网站自己的删除流程，会同时清理存储并自动重试、让账户的会话失效；不要直接在 psql 里删行。把上一步列出的 id 传进去，先不带 `--yes` 看一眼，再加上 `--yes`：

   ```bash
   dc exec api python -m app.cli.delete_data demo <demo_id>... --yes
   dc exec api python -m app.cli.delete_data account <owner_id>... --yes
   ```

   - 先删账户，再删剩下的比赛：账户删除会带走它名下的比赛，之后再删这些比赛只会报 `already gone` 或 `not found`。
   - 删除时文件已经清掉了，所以这些比赛在删除前会显示为回放缺失，这不影响删除。
   - 如果还用 rclone 拷回了文件，备份之后删除的文件也会一起回来，重做删除时会一并清掉。
   - 命令的细节见 [数据删除](data_deletion_v1.md#代用户删除)。
3. **30 天内删掉恢复遗留。** 它们是完整的旧库副本，不在自动保留规则里（本地保留规则只管 `pg-*.dump`）：

   ```bash
   rm /var/backups/cs2coach/pre-restore-<UTC>.dump
   dc exec -T postgres psql -U cs2coach -d postgres -c 'DROP DATABASE "cs2coach_pre_restore_<UTC>"'
   ```

   演练用的临时库 `cs2coach_restore_check` 也要在对比完后删掉，`restore.sh` 会打印删除命令。

## 8. 更新

```bash
cd /opt/cs2coach
bash scripts/deploy/deploy.sh
```

目标提交带新的数据库迁移时，`deploy.sh` 在启动新版本前自动跑 `backup.sh --db-only`，dump 名记进 `deploy/.deploy-history`（`backup=pg-<UTC>.dump migrations=<版本>`），不需要再手动先备份。

`NEXT_PUBLIC_*` 在构建时写进前端，改了之后也要重新跑 `deploy.sh`。

## 9. 回滚

```bash
cat deploy/.deploy-history                 # deploy/rollback 行是成功的部署；backup 行是部署前 dump
bash scripts/deploy/deploy.sh --rollback <sha>
```

**跨迁移回滚要先恢复数据库。** API 启动时只会向前补表和字段，而且数据库里有它不认识的迁移版本就拒绝启动（api 和 worker 进入重启循环），所以"旧代码忽略多出来的表"并不成立。`--rollback` 在动服务之前先比对：数据库里已应用的迁移目标提交都认识，才继续；否则直接退出，服务保持原样，并打印要执行的两条命令：

```bash
bash scripts/deploy/restore.sh <部署前 dump> --into-production --confirm-production-restore --no-start
bash scripts/deploy/deploy.sh --rollback <sha>
```

- `<部署前 dump>` 是 `deploy/.deploy-history` 里带来这个迁移的那次部署的 `backup=`，脚本会直接打印出来；没有记录时（例如用了 `--skip-backup`），用那次部署之前最新的夜间 `pg-*.dump`。
- 一定要带 `--no-start`：不带的话，`restore.sh` 用当前（新）代码重启 api 和 worker，它们一启动就把迁移又补回去，回滚预检会再次拒绝。
- **恢复会丢掉那份 dump 之后的所有改动**（新上传、评价、删除），恢复后要按第 7 步"重做删除"。不想丢数据就向前修复。
- 部署失败时 `deploy.sh` 给出的下一步也遵循这一点：这次带了迁移，就提示"先恢复再回滚"，而不是直接 `--rollback`。

**镜像复用**：每次部署成功后，正在运行的镜像打上 `cs2coach-backend:<sha>`（api 和 worker 共用）和 `cs2coach-frontend:<sha>` 标签，保留最近 3 次部署的。回滚到其中之一时两个标签都在，就直接用它们启动，不重新构建（快，而且依赖和当时完全一致）；否则照常构建（后端依赖按 `backend/requirements.lock` 的哈希安装）。前端镜像里固化的是当时的 `NEXT_PUBLIC_*`：回滚后要用新值，先 `docker rmi cs2coach-frontend:<sha>` 再回滚，强制重建。查看保留了哪些：`docker images cs2coach-backend`。

回滚后仓库处于 detached HEAD，下一次不带参数的 `deploy.sh` 会切回 `main` 再更新。

## 10. 日志与排查

```bash
dc ps
dc logs -f --tail 100 api worker           # 应用日志
dc logs -f caddy                           # JSON 访问日志（已去掉 Cookie/Authorization、登录回调的查询参数、渲染 worker token、上传令牌和跳转地址的查询参数）
curl -i https://<域名>/health              # 依赖异常时返回 503 {"status":"degraded"}
curl -i https://<域名>/health/worker       # 解析 worker 心跳 90 秒内为 200，否则 503 {"status":"degraded"}
dc logs worker | grep '"job_done"'         # 每个任务结束一行 JSON
bash scripts/deploy/watch.sh               # 手动巡检一轮
```

worker 每个任务结束输出一行 JSON：`event`（`job_done`）、`jobId`（不透明 id）、`type`、`outcome`、`errorCode`、`attempt`、`sourceBytes`、`downloadS`、`parseS`、`normalizeS`、`analyzeS`、`coachingEvents`、`peakRssMiB`，不含文件名、账户或路径。`errorCode` 能把内存超限（`PARSE_OUT_OF_MEMORY`）、超时（`PARSE_TIMED_OUT`）和存储读取失败（`STORAGE_READ_FAILED`）分开；`peakRssMiB` 是解析子进程的峰值内存，用来定 `WORKER_MEM_LIMIT` 和 `PARSE_MEMORY_LIMIT_BYTES`。`/health/worker` 故意不并进 `/health`：caddy 和 frontend 依赖 api 的健康检查启动，worker 挂掉不能把整站拖下。

每个容器的日志按 10 MB × 5 份轮转。Caddy 只在启动时读配置，而且 git 更新 `deploy/Caddyfile` 时换的是新文件，运行中容器的单文件挂载仍指向旧文件；所以 `deploy.sh`（包括 `--rollback`）发现 caddy 挂载的内容与仓库不一致时会自动重启 caddy。在 VPS 上手动改过 Caddyfile 时执行 `dc restart caddy`（不用 `caddy reload`：编辑器保存时换了新文件的话，reload 读到的仍是旧内容）。

## 11. 已知限制

- **单台 VPS**：没有高可用，机器或机房故障期间整站不可用；数据库、Redis 和应用在同一台机器上。
- **没有自动部署**：更新靠 ssh 上去手动跑 `deploy.sh`，CI 不会触发部署。
- **告警是最小可用版**：`watch.sh` 每 5 分钟巡检、按状态变化告警，加上 dead-man ping；没有指标、图表和日志检索。外部拨测建议同时探 `/health` 和 `/health/worker`。
- **api 和 worker 容器仍以 root 运行**：解析不可信 `.dem` 的子进程已经以独立的低权限 `parser` 用户运行，只拿到白名单环境变量（不含数据库、Redis、存储、渲染、Steam 的密钥）；把两个容器本身改成非 root 需要先迁移命名卷的属主，留作后续。
- **每天备份一次**：最坏丢失 24 小时的数据；Redis 不备份。
- **没有 GPU 渲染机**：`RENDER_CLIPS_ENABLED=0`，第一人称片段入口隐藏。
- **分片上传依赖单个 API 进程**：分片池、每个上传的进行中计数和整文件 intake 槽都在 API 进程内存里，`docker-compose.prod.yml` 只跑一个 uvicorn 进程。暂存在 VPS 本地磁盘上，不在 R2，也不进备份；从数据库备份恢复后，没有暂存目录的会话 `complete` 时返回缺片，到期后被清扫。
- **回滚不回退数据库结构**：跨迁移回滚要先用部署前 dump 恢复（会丢掉之后的改动），见第 9 步。
- **R2 兼容性要在真实部署上确认**：应用写对象时用条件写入，首次部署务必完成第 5 步的真实上传。删除时的清理不带条件（列出后批量删除），第 5 步第 7 条的删除也顺带验证了这一点。
- **删除追不回备份**：已删除的数据在备份里最多再保留 30 天；从备份恢复后要按第 7 步用 `python -m app.cli.delete_data` 重做删除。
- **代用户删除**：第三方的移除请求、被移出邀请名单的用户的删除请求，都用同一个运维命令处理，见 [数据删除](data_deletion_v1.md#代用户删除)。移出邀请名单本身不删除数据。

相关文档：[云端预览部署](cloud_preview_deploy_v1.md)、[部署准备](deployment_readiness_v1.md)、[2D 内测上线计划](rules_2d_beta_launch_v1.md)、[数据删除](data_deletion_v1.md)、[Configuration Reference](configuration_reference_v1.md)。
