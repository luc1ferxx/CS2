# VPS 部署 V1：一台海外 VPS 上线邀请制内测

整个网站跑在一台 VPS 上：Caddy 负责 HTTPS（Let's Encrypt 自动签发）和路由，后面是 Next.js、FastAPI、解析 worker、PostgreSQL、Redis；`.dem`、回放等文件放在私有的 S3 兼容桶（推荐 Cloudflare R2），数据库和文件每天备份到另一个桶。

| 文件 | 作用 |
| --- | --- |
| `deploy/Caddyfile` | 单一 HTTPS 源站的路由、上传大小、超时、安全头、JSON 访问日志 |
| `docker-compose.prod.yml` | 叠加在 base + preview 之上：加 `caddy`，去掉 api/frontend 的公开端口，数据库密码必填，Redis 持久化，自动重启，日志轮转 |
| `deploy/env.production.example` | 生产环境变量模板（只有占位符）；真实文件是 git 忽略的 `deploy/.env.production` |
| `scripts/deploy/bootstrap.sh` | 新 VPS 初始化（root 运行一次，可重复运行） |
| `scripts/deploy/deploy.sh` | 更新、构建、启动、等健康检查、冒烟；`--rollback <sha>` 回滚 |
| `scripts/deploy/prod_smoke.sh` | 匿名生产冒烟（只用 curl），并打印手动 Steam 登录清单 |
| `scripts/deploy/backup.sh` + `cs2coach-backup.{service,timer}` | 每天 03:30 UTC 备份数据库并镜像文件桶 |
| `scripts/deploy/restore.sh` | 恢复演练（默认恢复到临时库），或显式确认后恢复生产库 |

以下命令里的 `dc` 指：

```bash
alias dc='docker compose --env-file deploy/.env.production -f docker-compose.yml -f docker-compose.preview.yml -f docker-compose.prod.yml'
```

## 0. 只给朋友用的最简配置

第一次上线只给自己的朋友用：按下面准备资源（代替第 1 步，只引用其中的具体做法），然后照第 2–10 步操作。

1. **VPS**：Ubuntu 24.04，2–4 vCPU / 4 GB 内存就够，靠 `bootstrap.sh` 建的 4 GB swapfile 兜底（大 demo 解析时可能用到 swap，会慢一些）。机房选东京、新加坡或香港，约 US$10–20/月。
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

只给朋友用时**刻意跳过**，扩大内测前必须补上：

- 指标和告警（现在只有容器日志和 `/health`）；
- 数据库迁移演练；
- 真实 demo 语料门禁（corpus gate，见 [2D 内测上线计划](rules_2d_beta_launch_v1.md)）；
- 确认雷达图可以公开分发：第三方（MIT/GPL）雷达图已经换成本项目从 CS2 导航网格渲染的图（见 `frontend/public/maps/ATTRIBUTION.md`），但这些图由 Valve 的游戏数据派生，分发条款还没有单独确认。

隐私说明页和删除比赛、删除账户已经有了，见 [数据删除](data_deletion_v1.md)。

## 1. 准备资源（完整配置）

1. **VPS**：Ubuntu 24.04，推荐 4 vCPU / 8 GB 内存 / 80 GB 磁盘（单个解析子进程内存上限 4 GB）。面向国内玩家选东京、新加坡或香港，优先到国内线路好的机房。海外机房不需要 ICP 备案。
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

注意：Docker 发布的端口会绕过 ufw，所以只有 Caddy 发布端口。api 用 `--forwarded-allow-ips=*` 信任转发的客户端 IP（登录限流按 IP 计），这只在 api 端口不公开时才安全——**不要给 api 加 `ports:`**。

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
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`，生成一次后不要随意更换 |
| `RENDER_WORKER_TOKEN` | `openssl rand -hex 32` |
| `STEAM_WEB_API_KEY` | 第 1 步申请的 key |
| `STEAM_LOGIN_ALLOWLIST` | 受邀 SteamID64，逗号分隔；`*` 表示任何人都能登录，内测不要用 |
| `OBJECT_STORAGE_*` | artifacts 桶和应用 token |
| `BACKUP_*` | backups 桶和备份 token |
| `NEXT_PUBLIC_BETA_CONTACT_URL` | 可选；未受邀页面上"申请内测资格"的链接 |
| `NEXT_PUBLIC_PRIVACY_CONTACT` | 必填（`deploy.sh` 检查）；显示在 `/privacy` 的隐私问题和删除请求联系方式。邮箱会变成 mailto 链接，`http(s)://` 网址会变成链接，其他内容原样显示为文字 |
| `NEXT_PUBLIC_DATA_REGION` | 可选；显示在 `/privacy` 的服务器所在地区，如 `日本东京`。不填时显示"海外 VPS，具体地区由站长部署时选定" |

整份文件存一份到密码管理器。丢了 `STEAM_CREDENTIAL_ENCRYPTION_KEY`，已保存的 Steam 比赛授权就无法解密；备份桶里不包含这份文件。

## 4. 首次部署

确认 DNS 已经解析到 VPS，然后以 `cs2coach` 用户：

```bash
cd /opt/cs2coach
bash scripts/deploy/deploy.sh
```

脚本会：检查 env 文件存在且没有占位符 → `git pull --ff-only` → 用 Caddy 镜像校验 `deploy/Caddyfile` → `dc build --pull` → `dc up -d`（api 启动失败时打印日志并停下）→ Caddyfile 有变化时重启 caddy → 最多等 300 秒（`DEPLOY_HEALTH_TIMEOUT_SECONDS`）直到 `https://<域名>/health` 返回 200 → 运行 `prod_smoke.sh` → 打印当前提交并追加到 `deploy/.deploy-history`。首次构建前端约需 5–10 分钟；证书签发要求 80 和 443 端口能从公网访问。

路由（`deploy/Caddyfile`）：`/auth/steam/login`、`/auth/steam/callback`、`/auth/me`、`/auth/logout`、`/auth/account` 等后端认证路由、`/steam/*`、`/demos`、`/demos/<id>/…`、`/uploads/*`、`/coaching/*`、`/health`、`/render-worker/*`、`/render/*` 进 FastAPI；`PATCH` 和 `DELETE /demos/<id>` 进 FastAPI，`GET /demos/<id>` 是 Next.js 复盘页；其余（`/`、`/dashboard`、`/account`、`/privacy`、`/auth/callback`、`/_next/*`、`/maps/*`）进 Next.js。只有 `/uploads/*` 放宽到 1100 MB 请求体，读写超时 2 小时。

## 5. 冒烟与手动 Steam 登录检查

```bash
bash scripts/deploy/prod_smoke.sh https://<域名>
```

它检查：证书有效、HTTP 跳转 HTTPS、`/health` 返回 `{"status":"ok"}`、`/dashboard` 是 HTML 且带 HSTS 等安全头、`GET /demos/<id>` 由 Next.js 返回、`GET /privacy` 是 200 的 HTML、匿名访问 API（`/demos`、`/demos/<id>/status`、`PATCH /demos/<id>`、`/uploads/demo`、`/auth/me`）都是 401、匿名 `DELETE /auth/account` 和 `DELETE /demos/<id>` 都由 FastAPI 返回 401 JSON（证明它们没有落到 Next.js）、`/docs` 和 `/openapi.json` 是 404、`/auth/steam/login` 跳转到 `https://steamcommunity.com/openid/login` 并下发带 `Secure; HttpOnly` 的 `__Host-` 状态 cookie。

然后在浏览器里手动走一遍（脚本最后也会打印一份手动清单）：

1. 受邀账号点"通过 Steam 登录"，回到 `/dashboard`。
2. 不在名单里的账号登录后停在 `/auth/callback?error=not_invited`。
3. 上传一份真实 `.dem`，状态走到完成。这一步同时验证 R2 写入（应用用条件写入 `If-None-Match`）。
4. 打开复盘页：回放播放、切换回合、战术地图和建议卡片正常。
5. 退出登录后，`/dashboard` 要求重新登录。
6. 退出登录的状态下打开 `/privacy`：不需要登录，地区和联系方式是你配置的值，页脚有"与 Valve 无关联"声明。
7. 在比赛库里用行菜单「删除比赛…」删掉第 3 步上传的那场，它从列表消失，今天的上传次数不变。首次部署时最好再用一个专门的受邀测试账号，在 `/account` 走一遍删除账户：该账号在另一个浏览器里的登录也要同时失效。

需要带会话的完整脚本冒烟时，用 `scripts/cloud_preview_smoke.py`（要设 `AUTH_SESSION_COOKIE` 和 `SAMPLE_DEMO_PATH`，每次会占用该账号一次上传配额）。

## 6. 启用备份

以 root：

```bash
cp /opt/cs2coach/scripts/deploy/cs2coach-backup.service /opt/cs2coach/scripts/deploy/cs2coach-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cs2coach-backup.timer
systemctl start cs2coach-backup.service     # 先手动跑一次
journalctl -u cs2coach-backup.service -n 50
systemctl list-timers cs2coach-backup.timer
```

每次备份：`pg_dump -Fc` 到 `/var/backups/cs2coach/pg-<UTC>.dump`，确认非空且 `pg_restore --list` 能解析 → 上传到 `<备份桶>/postgres/` → 本地保留最新 14 份、桶里删除 30 天前的 → 把 artifacts 桶 `rclone sync` 到 `<备份桶>/artifacts/`，被删除或覆盖的对象挪到 `artifacts-replaced/<UTC>/`，30 天后清掉。任何一步失败都会打印 `BACKUP FAILED` 并以非零退出（`systemctl status` 会显示 failed）。

不在备份里的：Redis（会话和队列，丢了只需重新登录）、Caddy 证书（会自动重签）、`deploy/.env.production`（自己存密码管理器）。备份桶如果支持版本控制、对象锁或生命周期规则，在存储商那边打开是更强的保护。

**已删除数据在备份里的保留期**（`/privacy` 对用户这样承诺，改保留参数时要一起改）：
- 用户删除比赛或账户后，数据库 dump 里的副本在桶里最多保留 30 天（`BACKUP_REMOTE_KEEP_DAYS`），本地最多保留最新 14 份（`BACKUP_LOCAL_KEEP`）。
- 被删除的文件在下一次备份时从镜像挪到 `artifacts-replaced/<UTC>/`，30 天后清掉。
- 打开版本控制或对象锁会让被删除的数据保留得更久，要同步更新隐私页的说法。

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
bash scripts/deploy/backup.sh               # 可选：涉及数据库结构的版本先备份
bash scripts/deploy/deploy.sh
```

`NEXT_PUBLIC_*` 在构建时写进前端，改了之后也要重新跑 `deploy.sh`。

## 9. 回滚

```bash
cat deploy/.deploy-history                 # 找上一个正常的提交
bash scripts/deploy/deploy.sh --rollback <sha>
```

回滚只换代码。数据库结构在 API 启动时只会向前补表和字段，旧代码一般能忽略多出来的表和列；但如果新版本改了字段含义或约束，回滚可能失败，这时要用部署前的备份恢复（第 7 步）。回滚后仓库处于 detached HEAD，下一次不带参数的 `deploy.sh` 会切回 `main` 再更新。

## 10. 日志与排查

```bash
dc ps
dc logs -f --tail 100 api worker           # 应用日志
dc logs -f caddy                           # JSON 访问日志（已去掉 Cookie/Authorization、登录回调的查询参数、渲染 worker token 和跳转地址的查询参数）
curl -i https://<域名>/health              # 依赖异常时返回 503 {"status":"degraded"}
```

每个容器的日志按 10 MB × 5 份轮转。Caddy 只在启动时读配置，而且 git 更新 `deploy/Caddyfile` 时换的是新文件，运行中容器的单文件挂载仍指向旧文件；所以 `deploy.sh`（包括 `--rollback`）发现 caddy 挂载的内容与仓库不一致时会自动重启 caddy。在 VPS 上手动改过 Caddyfile 时执行 `dc restart caddy`（不用 `caddy reload`：编辑器保存时换了新文件的话，reload 读到的仍是旧内容）。

## 11. 已知限制

- **单台 VPS**：没有高可用，机器或机房故障期间整站不可用；数据库、Redis 和应用在同一台机器上。
- **没有自动部署**：更新靠 ssh 上去手动跑 `deploy.sh`，CI 不会触发部署。
- **还没有指标和告警**：只有容器日志和 `/health`。建议先用外部拨测（如 UptimeRobot）每分钟探测 `/health`。
- **每天备份一次**：最坏丢失 24 小时的数据；Redis 不备份。
- **没有 GPU 渲染机**：`RENDER_CLIPS_ENABLED=0`，第一人称片段入口隐藏。
- **回滚不回退数据库结构**，见第 9 步。
- **R2 兼容性要在真实部署上确认**：应用写对象时用条件写入，首次部署务必完成第 5 步的真实上传。删除时的清理不带条件（列出后批量删除），第 5 步第 7 条的删除也顺带验证了这一点。
- **删除追不回备份**：已删除的数据在备份里最多再保留 30 天；从备份恢复后要按第 7 步用 `python -m app.cli.delete_data` 重做删除。
- **代用户删除**：第三方的移除请求、被移出邀请名单的用户的删除请求，都用同一个运维命令处理，见 [数据删除](data_deletion_v1.md#代用户删除)。移出邀请名单本身不删除数据。

相关文档：[云端预览部署](cloud_preview_deploy_v1.md)、[部署准备](deployment_readiness_v1.md)、[2D 内测上线计划](rules_2d_beta_launch_v1.md)、[数据删除](data_deletion_v1.md)、[Configuration Reference](configuration_reference_v1.md)。
