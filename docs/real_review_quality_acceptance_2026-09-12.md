# xelex 真实复盘质量与端到端验收

日期：2026-09-12。分支 `main`，基线 HEAD `3016207145c37f0a8fea0f00b2da80c1165f5b73`。工作区包含此前及本轮未提交修改；没有提交或推送。最终工作区状态记录位置为 `.local/qa/quality-20260911/quality-final-git-status.txt`。

用户批准本轮先检查现有四场比赛的真实建议，再跑通 `.dem` 上传、解析、个人复盘与片段生成/复用流程。该本机流程已完成验收，发现的五项问题均已修复、测试和部署。默认个人身份是 `xelex / 76561198998266210`。本轮不构成公开上线、生产认证或专业战术判断准确率的验收。

## 当前结果与建议数量

四场原比赛均保留原 ID、名称、源文件、replay 和任务。独立核对先检查原有 120 条 xelex 建议的玩家、回合、tick、关联事件和可计算事实，再修复误导性的几何筛选及文案，更新已存 coaching。

| 原比赛 | Demo ID | 回合 | 全场建议：前 → 后 | xelex 建议：前 → 后 |
| --- | --- | ---: | ---: | ---: |
| Dust2 | `218476df-2c11-4cd2-8c1f-ac8041a99f50` | 21 | 293 → 293 | 31 → 31 |
| Mirage | `31f2d859-ca55-495d-95ce-153cf01cedd0` | 24 | 313 → 309 | 35 → 35 |
| Ancient | `9164fc0a-caf7-4ff0-a4c8-3172cf8acd4b` | 16 | 234 → 234 | 27 → 27 |
| Nuke | `3b728e96-3d5b-4f15-9039-b4d01a561a51` | 19 | 246 → 230 | 27 → 25 |

原四场全场建议合计由 1,086 条降至 1,066 条，xelex 由 120 条降至 118 条。数量变化来自筛选修复，不表示剩余建议已被认定为战术错误。

## 发现的问题及修复

1. **同一地图楼层内的平台高度差，被忽略为平面上的过近站位。** Nuke 三条 xelex 候选位于 R3/tick 20507、R10/tick 74235、R17/tick 147147，雷达 XY 距离小于 2.5 百分点，但原始 Z 高度差分别为 192、269.641、288.781 世界坐标单位。`poor_spacing/stacked` 现在加入可配置的 128 世界单位高度差上限，保留已有 Nuke 上下层和缺高度处理。实际高度差和阈值已显示在证据面板中。这个上限是保守筛选条件，不宣称能证明视线或共同受枪线威胁。
2. **“离队友过远”的建议却提示继续拉开距离。** `poor_spacing/too_far` 的后端 action 和中文操作提示已改为检查队友能否跟进、路线及补枪时机；过近与过远使用各自对应的建议。
3. **炸弹包点出现内部实体编号。** 例如 Nuke `bomb_planted` tick 88208 的 `313` 被显示成包点名称。解析契约与前端展示现在仅接受明确的 A/B 包点，未知编号显示一般的下包/炸弹状态，不猜测编号与 A/B 的映射。旧 replay 在读取规范化时同样适用。
4. **解析完成会覆盖文件名或用户名称。** 新上传验收发现解析完成时可能将名称覆盖成 `parser spike` 调试名。已移除该覆盖并部署：默认名称保留 intake 接受的 `accepted.display_filename`，另一会话在 parsing 或 analyzing 阶段设置的名称也不会被 worker 覆盖，mock 默认名称保持原行为。三项新增回归已包含在最终 487 项后端测试中。
5. **同一片段的“重新观看”从暂停处继续。** 真实新片段暂停在第 20 秒时，点击已保存片段的“重新观看”，共享 tick 短暂变为 5403，但原视频时钟随即将其带回约 6683，画面仍从第 20 秒继续；复现记录 `same-video-restart-before.log` 的 `startedFromClipBeginning=false`。`playSavedClip` 已修复：媒体身份相同时，先通过现有 seek 同步视频位置再播放；切换不同片段时，由新播放器挂载应用共享 tick。新增 `saved-clip-playback.test.mjs` 回归及最终构建通过，已部署。同一浏览器脚本修复后验证暂停第 20 秒 → 精确第 0 秒 → 正常播放至 0.586294 秒，`startedFromClipBeginning=true`，记录 `same-video-restart-after.log`。

更新后独立检查了全部 511 条保留的过近站位候选：来源坐标高度差及证据字段均满足新条件。三个已确认的 Nuke 问题 ID 均已移除；一个稍后的合格候选补入，因此 xelex 净减少两条。全部 180 条过远站位 action 与 537 个炸弹事件的包点展示核对通过，未再出现已发现的问题。

## 建议事实、回放和地图证据

建议事实核对不导入生产 analyzer，独立计算采样距离和到达时间，检查 player ID、当时阵营、有效回合、证据 tick 及关联事件。原有 39 条 xelex 未补枪死亡候选均对应确切对手击杀和完整五秒观察窗口，窗口内没有队友击杀该名击杀者。

修复前浏览器抽查 Nuke 的 10 条建议，覆盖 T/CT 两半场、死亡、远近站位、下包后证据和三个高度反例；另外三图各抽查一条。修复部署后，在 Nuke 已为 25 条建议的状态下再次完成跨四图的 13 条点击验证，卡片、证据、回合与时间轴定位一致，测试页异常为 0、render POST 为 0；首条建议仍正确打开原有视频第 5 秒。最终记录为 `browser-evidence-after-result.log`，修复前记录单独保留。

| 地图 | 位置帧 | 有效回合事件精确帧检查 | 实战死亡状态检查 | 最大常规采样间隔 |
| --- | ---: | ---: | ---: | ---: |
| Dust2 | 13,820 | 1,235 | 130 | 0.25 秒 |
| Mirage | 15,462 | 1,476 | 153 | 0.25 秒 |
| Ancient | 11,456 | 1,099 | 115 | 0.25 秒 |
| Nuke | 12,262 | 1,091 | 128 | 0.25 秒 |

四份原始 `.dem` 重新只读解析的聚合指标与公开 API 一致；四场均为 64 tick/s、10 个稳定玩家 ID。重复帧 tick、回合重叠、有效事件/帧编号错位、回合边界缺帧、非有限 XYZ、实战死亡及炸弹最终同 tick 状态不符均为 0。实际前端 helper 在全部 526 次实战死亡中，死亡前半 tick 仍显示存活，确切死亡 tick 才改变状态；共 934 个实际玩家事件或回合端点生成的片段范围均在对应回合内且为正时长。

Nuke 直接 demoparser2 世界坐标检查与公开帧相符；前后端共用 `Z <= -495` 下层条件及集中 overview 变换。tick 6683 位于上层，21819 位于下层；23177.5 炸弹仍为 carried，23178 切为 planted，65309 为 defused。Ancient 中场有 740 个采样没有 xelex，全部处于第 12、13 回合之间，有效回合内缺失为 0。

Dust2、Nuke 当前配置为 calibrated；Mirage、Ancient 仍为 approximate，界面显示“参考坐标”。本轮没有把参考坐标验收为精确雷达校准。

## 仅更新 coaching，保留已有媒体

本轮通过正常 storage 读取原四场已存 replay，应用最新规范后重新执行确定性 analyzer，在单一数据库事务中仅更新对应 `CoachingEvent` 与 `Demo.coaching_event_count`。没有重解析写回原比赛，没有调用 `complete_parse_job`，没有新建原比赛的 parse/render 任务。

刷新前后确认源文件与 replay artifact 引用、读取后的 replay 内容摘要、已有视频信息和全部任务保持不变。没有对源文件执行写操作。原 Nuke 两段可用视频继续保留：

| job ID | tick 范围 | 文件与时长 | 本轮检查 |
| --- | --- | --- | --- |
| `fb347c00-f7e2-415e-aef4-e1435c07a229` | `[6363, 7643)` | H.264 / 1280×720 / 30 fps / 20 秒 | tick 6683 对应第 5 秒 |
| `c497d087-bc9f-4a35-b320-8620cc3c93a7` | `[15288, 17517)` | H.264 / 1280×720 / 30 fps / 34.833333 秒 | 文件比 tick 时长多 0.005208 秒，小于一帧；按 tick 边界结束 |

ffprobe 直接读取本机稳定 job 媒体路由确认文件参数。两段 HEAD 200、Range 206、另一 owner 404、private/no-store 均通过；POV、job ID、范围与任务一致。两段共 3,511 个整 tick 往返映射通过，末端 tick 不继续算覆盖，旧片段显式选择可保留。以上是原比赛既有媒体保留证据，本轮新副本生成的片段单列如下。

## 新上传的完整解析流程

通过真实浏览器文件控件重新上传本机 Nuke `.dem`，文件大小 **306,989,430 字节**，创建独立验收副本 `6b2f209b-5ed6-415f-b07e-7c61bcfecd9d`，没有覆盖原 Nuke。

- 伪装为 `.dem` 的归档文件返回 HTTP 400，网页显示错误后可以继续选择文件。
- 有效 `.dem` 上传返回 HTTP 201，经过后端存储、解析和分析后状态为 completed。
- 本机单次浏览器“开始上传 → 可进入个人复盘”记录为 **14.293 秒**，含本轮脚本的页面导航与状态核对；不是通用性能承诺。
- 新副本为 19 回合、25 条 xelex 建议，点击首条定位到 tick 6683，存在相同 tick 的已存个人建议。
- 此上传阶段未触发 render，随后通过独立 worker 生成了以下新片段。

## 新副本的真实第一人称片段

新副本 `6b2f209b-5ed6-415f-b07e-7c61bcfecd9d` 围绕建议 `cc5c3f34-9b90-51e3-bc13-ac899cf35ca4` 创建真实任务 `d62230be-6441-4bfc-8ebe-fd0137d5c71a`，范围 `[5403, 7963)`，64 tick/s，目标时长 40 秒，POV 为 xelex。

任务于 `2026-09-12T07:21:33.350815Z` 提交，`07:23:19.422932Z` 完成，提交到 completed 单次耗时约 **106.072 秒**。独立录制 worker 执行并上传产物，结束后 CS2 正常退出；API 与解析容器没有执行游戏录制。

生成文件为 **25,996,715 字节**，H.264、1280×720、30 fps、1,200 帧、40 秒，带 AAC 音轨。`timeOriginSeconds=0`，标定依据为 `csdm_tick_start_stop`；目标建议 tick 6683 对应片段第 20 秒。媒体校验记录为 `.render-worker-work/jobs/d62230be-6441-4bfc-8ebe-fd0137d5c71a/media-verification.json`。

主任务已查看第 0、20、39 秒截图，均为带 xelex HUD 的真实 Nuke 游戏画面，没有加载屏；截图保存在 `output/playwright/quality-real-video-0.png`、`quality-real-video-20.png`、`quality-real-video-39.png`。这些抽样确认了产物身份与画面内容。

新片段在 App 内的最终验收记录为 `verify-real-clip-result.log`：

| 浏览器检查 | 实测结果 |
| --- | --- |
| 首条建议与媒体 | tick 6683 定位视频第 20 秒，实际 video ready 为 1280×720、40 秒 |
| 播放、倍速与同步 | 2x 时 tick 6726 / video 20.665118 秒，换算误差小于 1 tick；暂停与恢复可用 |
| 视频末端交接 | 越过片段 endTick 7963 后战术时钟继续至 tick 7982 |
| 无对应视频的回合 | R2 从 tick 12500 前进至 12519，使用战术回放，没有错误地播放旧片段 |
| 视频起点交接 | 从片段前进入有效范围，tick 5415 / video 0.181958 秒，继续同步 |
| 刷新和片段库重播 | 刷新后仍为同一稳定 job URL；点击当前已挂载片段从开头重新播放，采样为 0.203401 秒 |
| 私有媒体读取 | HEAD 200、Range 206、另一 owner 404 |
| 复用与页面异常 | 新副本 job 数 1 → 1，额外 render POST 0，页面异常 0 |

## 最终检查与本机状态

| 检查 | 当前状态 / 证据 |
| --- | --- |
| Linux 当前源码完整后端测试 | **487 项通过**；`.local/qa/quality-20260911/backend-current-source-tests.log` |
| 过近站位高度修复 focused regression | 35 项通过，包含三个真实坐标对与边界/降级情形 |
| 前端全部 17 份 helper、lint、typecheck | 包含同片段重新观看回归，当前源码通过 |
| 第五项修复后的生产 build | 通过并已部署；记录 `frontend-final-build.log` |
| 原四场 coaching 更新后独立核对 | 通过；`coaching-after-audit.json`、`coaching-after-summary.log` |
| 原 source/replay/video/jobs 保留 | 通过；`coaching-refresh-result.json` |
| 新真实 `.dem` 上传、错误恢复与个人定位 | 通过；`upload-flow-result.log` |
| 解析完成后保留名称 | 已部署，默认名称、另一会话处理中改名和 mock 名称回归通过，包含在 487 项中 |
| 修复后四图建议点击与既有视频定位 | 13 条通过，异常 0、render POST 0；`browser-evidence-after-result.log` |
| 新验收副本的真实 render_clip | completed，40 秒真实 xelex 视频，媒体参数及 0/20/39 秒画面抽查通过；见上文 |
| 新生成片段再次打开/复用 | 通过，同一稳定 job URL、job 数 1 → 1、额外 render POST 0；`verify-real-clip-result.log` |
| 同一片段从头重播 | 修复前失败、修复后相同脚本通过；`same-video-restart-before.log`、`same-video-restart-after.log` |
| 原片段切换、高级工具与比赛库 | 通过，原两个可用片段均从开头播放，原任务数仍为 4，高级工具可展开；`final-library-check-result.log` |
| 验收副本归档 | 已软归档，直接 replay GET 仍为 200；默认比赛库仅显示原四个 Demo ID |
| 最终依赖和任务状态 | diagnostics `status=ok`，队列长度 0、worker heartbeat alive；`final-diagnostics.json` |

Windows 原生完整后端测试曾遇到已知 POSIX 目录文件描述符存储兼容问题，不能作为通过结果；本轮完整后端 gate 使用 Linux 容器，并以 `--workdir /workspace` 运行当前源码，避免镜像内 `/app` 目录覆盖待验代码的导入路径。此前 484 项记录保留为中间基线，以最终 487 项为准。前端构建对本次进程设置 `NEXT_TELEMETRY_DISABLED=1`，避开 MSIX AppData 路径导致的 EXDEV 错误；最终 17 份 helper、lint、typecheck 和生产构建均通过，前端已部署到本机 Compose。

最后再次打开原 Nuke 的两个既有片段，从开头播放的采样分别为 0.130553 秒和 0.124964 秒，稳定 URL 各自对应原 job。原 Nuke 任务数仍为 4（2 个完成、2 个历史失败），没有增加新任务；高级 operator/video 工具两个面板可展开。该最后检查 render POST 为 0、页面异常为 0。

验收副本及其真实新视频保留在软归档比赛中，可以按 ID 直接打开；原四场继续作为默认比赛库内容。没有删除用户比赛或清除历史失败记录。最终 `final-runtime-state.json` 确认 API 正常、`rendererRunning=true`、`cs2Running=false`、`qaArchived=true`，验收副本 render 任务数为 1 且已完成；独立 renderer 继续等待任务。终版截图为 `output/playwright/quality-real-video-verified.png` 与 `output/playwright/quality-final-dashboard.png`。

## 解释范围与后续用途

本次能证明已检查的建议与解析事实、规则条件、时间和玩家身份相符，修复了三个会误导用户理解的建议问题，以及名称覆盖和片段重播两个使用问题。它不能证明一次决策是战术错误：视线、可走路线、沟通、意图和战术分工没有完整证据，未补枪也不能单独证明死亡可避免。

原 120 条个人候选中，78 条为单次采样的站位提示，其中 35 条在冻结结束后 15 秒内出现。这提示后续应与真实玩家评估排序、重复日常站位提醒和建议有用性，而不是将候选数量或通过事实核对等同于教练准确率。没有接入 LLM 生成式分析。

紧凑审查证据及浏览器脚本保存在忽略目录 `.local/qa/quality-20260911/`；目录名沿用本轮开始时间。主要文件为 `coaching-quality-conclusions.md`、`nuke-ten-samples.json`、`coaching-fact-audit.json`、`coaching-after-audit.json`、`contract-audit.md`、`contract-raw-checkpoints.json`、`contract-frontend-results.json`、`browser-evidence-after-result.log`、`request-real-clip-result.log`、`verify-real-clip-result.log`、`same-video-restart-after.log`、`final-library-check-result.log`、`final-diagnostics.json`。完整 `.dem`、replay API 缓存和媒体不作为 Git 测试 fixture 提交。

此前界面验收见 [中文复盘工作区改版验收](frontend_redesign_acceptance_2026-09-08.md)，已有片段链路背景见 [片段复用验收](saved_clip_reuse_acceptance_2026-09-07.md)。
