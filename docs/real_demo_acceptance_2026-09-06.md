# 四份真实 Demo 的本地运行验收

后续更新：个人复盘、0.25 秒采样、炸弹状态及 Nuke 楼层已完成；当前结果见 [个人复盘验收](personal_review_acceptance_2026-09-07.md)。下文保留首轮验收历史，其中 24 条建议上限与缺少 Z 等限制已由后续版本更新。

日期：2026-09-06（America/Los_Angeles；部分日志文件使用 UTC 日期 2026-09-07）。最初解析验收基于 `main` / `3016207145c37f0a8fea0f00b2da80c1165f5b73`，本次运行包含未提交的解析修复与前端视频占位状态修复。

## 当前结果

Docker 已恢复，四份真实 `.dem` 均通过 HTTP 上传、数据库/Redis、worker 和 artifact storage 流程，状态为 `completed`，可在本地 Demo Library 打开。Dust2 通过严格真实样本 smoke；Mirage、Ancient 通过辅助脚本执行真实上传；Nuke 通过 Playwright 操作网页上传控件，完成解析后在网页重命名。

| 比赛名称 | 回合 | Demo ID / 本地详情 |
| --- | ---: | --- |
| Spirit vs MOUZ - Dust2 | 21 | [218476df-2c11-4cd2-8c1f-ac8041a99f50](http://localhost:3000/demos/218476df-2c11-4cd2-8c1f-ac8041a99f50) |
| Spirit vs MOUZ - Mirage | 24 | [31f2d859-ca55-495d-95ce-153cf01cedd0](http://localhost:3000/demos/31f2d859-ca55-495d-95ce-153cf01cedd0) |
| Spirit vs MOUZ - Ancient | 16 | [9164fc0a-caf7-4ff0-a4c8-3172cf8acd4b](http://localhost:3000/demos/9164fc0a-caf7-4ff0-a4c8-3172cf8acd4b) |
| Spirit vs MOUZ - Nuke | 19 | [3b728e96-3d5b-4f15-9039-b4d01a561a51](http://localhost:3000/demos/3b728e96-3d5b-4f15-9039-b4d01a561a51) |

本次验收确认真实比赛上传和 2D 复盘可以在本机运行，不是生产上线、生产 Steam 登录或教练内容准确性的验收。

## 首轮直接解析结果

在项目独立 Python 3.12 虚拟环境中，直接运行真实 `demoparser2 → parse_demo_file → normalize_parser_output → analyze_replay`。没有替换解析器、生成 mock 比赛或修改原始 `.dem`。

| 本地文件 | 地图 | 回合 | 玩家 | 原始击杀记录 | 全链路秒数 |
| --- | --- | ---: | ---: | ---: | ---: |
| spirit-vs-mouz-m1-dust2.dem | Dust2 | 21 | 10 | 133 | 3.012 |
| spirit-vs-mouz-m2-mirage.dem | Mirage | 24 | 10 | 158 | 3.125 |
| spirit-vs-mouz-m3-ancient.dem | Ancient | 16 | 10 | 154 | 2.617 |
| spirit-vs-mouz-m4-nuke.dem | Nuke | 19 | 10 | 129 | 2.740 |

这些耗时是本机单次解析、标准化、规则分析和摘要计算时间，不包含上传、排队、存储或网页加载，也不是生产性能承诺。

四份结果均为 64 tick/s；有效回合区间内的击杀、事件、采样帧编号错位数均为 0，回合重叠数为 0。每份产生 24 条规则候选，达到当前分析器上限；数量不代表建议准确性已通过人工验收。

原始击杀记录包含回合间事件，不能直接当作比赛计分板的击杀总数。四份分别保留 3、5、29、1 条回合外记录。Ancient 的其中 28 条集中于第 12、13 回合之间的中场阶段，没有强行归属到正在进行的回合。

## 解析修复历史

- 原锁定版本 `demoparser2==0.41.0` 对四份文件均失败；直接调用原生事件解析得到 `EntityNotFound`，应用层最终报告 `MISSING_MATCH_METADATA`。
- 验证官方发布的 `0.42.0` 可处理四份文件后，更新 `backend/requirements.txt` 和 README 的版本说明。发布来源：[PyPI 0.42.0](https://pypi.org/project/demoparser2/0.42.0/)。
- 修复开头的重复 `round_start` / tick 0 伪结束导致的回合编号偏移。使用显式回合编号，并区分结束事件中已经完成本轮的计数。
- 结束事件匹配限制在下次开局之前，避免缺失结束事件时借用后一回合的胜方和时间；冻结结束、击杀、事件和回合标记按有效区间对齐。
- 新增 5 个紧凑回归测试，覆盖重复开局、结束计数、缺失边界和回合间事件保留。

## 运行与浏览器验证

- `python -m compileall -q backend/app`：通过。
- parser、normalizer、parser quality fixtures、rules analyzer：34 项测试通过。
- Linux 容器完整后端测试：424 项全部通过，日志 `.local/qa/backend-tests-linux.log`。
- 前端 `npm run lint`、`npm run typecheck`、`npm run build`、全部 10 份现有 Node helper 测试通过，记录 `.local/qa/frontend-gate-summary-2026-09-07.log`。
- 视频占位修复后重新通过 lint、typecheck、build，以及 `private-media`、`replay-diagnostics`、`replay-quality-fixtures` 回归。构建使用获准的 Google Fonts 网络访问，并仅对构建进程设置 `NEXT_TELEMETRY_DISABLED=1` 避开本地用户配置文件的 EXDEV 错误。
- Dust2 严格 smoke 通过，健康检查及 diagnostics 显示数据库、Redis、存储、worker 依赖正常，worker heartbeat 存活、队列长度为 0；记录 `.local/qa/http-smoke-dust2.log`。Mirage、Ancient 的完成记录为 `.local/qa/http-import-other.log`；Nuke 网页上传记录为 `.local/qa/library-after-upload.txt`。
- 最终 API 逐个读回四场完成比赛的 replay 和 coaching；另一开发 owner 读取每场 replay 均为 HTTP 404，其 Demo Library 返回空数组，记录 `.local/qa/final-api-check.json`。这是本地 owner 隔离验证，不是生产认证验收。
- 浏览器播放/暂停通过：tick 从 326 前进到 332，暂停后保持不变；速度可切换为 2x。
- 点击第 2 回合跳至 tick 14889，点击 `Live start` 跳至冻结结束 tick 16169，时间轴键盘 seek 前进至 16170。
- 教练时间轴标记跳至 tick 11702；transport 显示 R1 和相同 tick，战术地图显示 round 1，确认共享状态同步。教练卡片展开及 `Locate at Tick` 操作已验证。
- 390px 窄屏下文档和 body 宽度均为 390px，无横向溢出，播放控件可见；记录 `.local/qa/mobile-width-check.log`。
- 浏览器控制台错误数为 0。详细回放动作证据为 `.local/qa/browser-replay-check-final.log`；截图为 `output/playwright/demo-library.png`、`output/playwright/dust2-tactical-review.png`、`output/playwright/dust2-mobile.png`，均已忽略提交。

网页验收发现真实 `.dem` 的视频元数据为 `source=mock, status=ready, url=null` 时，旧占位文案误称“渲染已完成”。`FirstPersonReplay` 现将其显示为 `2D replay ready` / `2D only`，说明尚未生成第一人称视频；缺帧时不宣称 2D 可用。实际 rendered/manual-upload 视频缺少 URL、排队和不可达状态的降级提示及全部 controls 保留。React SSR 回归覆盖这些分支，记录 `.local/qa/frontend-placeholder-fix-summary-2026-09-07.log`。

严格 smoke 创建的 synthetic demo `8bf13614-95dc-4e05-bd6a-9320599deaaf` 已 soft archive，避免混入四份真实比赛。其 `render_clip` job 为 `b399ac61-2e4f-481a-88db-66912c755672`；本环境未连接 GPU worker，该任务按预期失败降级。浏览器直接打开已归档比赛，确认显示 `GPU worker not connected` 和 RenderOperator 的 `Failed` 状态，记录 `.local/qa/mock-fallback-snapshot.txt`。smoke 中媒体路由返回预期 HTTP 404；这些结果不代表生成了真实视频。

## 启动与继续使用

在仓库根目录使用本次本地配置启动或更新服务：

```powershell
docker compose -f docker-compose.yml -f .local/qa/compose-loopback.yml up --build -d
```

覆盖配置只将前端和 API 分别发布到 `127.0.0.1:3000` / `127.0.0.1:8000`；它是被忽略的本机 QA 文件。打开 [Demo Library](http://localhost:3000/dashboard)，即可复盘已导入的四场比赛，无需重新上传。

下一阶段应先核对真实比赛数据质量和个人复盘：提高位置采样、补炸弹状态、验证 Nuke 楼层/地图校准，并让玩家选择联动教练内容；这些完成前不扩大为公开生产服务。

## 已知限制与历史环境问题

- 当前使用本地 `dev-user` 边界，不是生产账户或 Steam 登录验收。
- 真实可用的是解析后的 2D 复盘；第一人称区域仍有明确标识的 mock shell，未生成真实 CS2 游戏画面。
- 常规位置采样约 4 秒、炸弹帧状态仍为 `carried`、Nuke 未保留楼层高度、三张非 Dust2 地图为 `approximate` 校准、个人玩家筛选尚未联动教练面板。未将这些功能列为已验证。
- 每场 24 条规则候选达到分析器上限，不能据此认定建议正确或完整，仍需人工逐项核对比赛证据。
- 首轮 Windows 原生完整后端测试运行 424 项，20 failures / 104 errors，主要涉及本地存储目录文件描述符操作及 Unix 权限断言，日志 `.local/qa/backend-tests.log`。后续 Linux 容器的 424 项通过，解决了部署目标环境的验证缺口，不表示 Windows 原生运行兼容性已修复。
- 首轮 Docker Desktop 4.67.0 曾因 `dockerInference` socket 错误无法就绪；用户恢复 Docker 后，本轮已成功启动服务并完成上述验收，无需重复重启 Windows。
- 先前 Docker 临时目录备份 `C:\Users\Jxx\AppData\Local\Docker\run.cs2-qa-backup-20260906-193657` 保留为历史恢复记录，本轮未删除。

四份文件继续由 `.gitignore` 排除。本地聚合证据在 `.local/qa/<文件名去掉.dem>.json`；升级前结果在 `.local/qa/baseline-0.41.0/`，升级后、回合修复前的结果在 `.local/qa/baseline-0.42.0/`。
