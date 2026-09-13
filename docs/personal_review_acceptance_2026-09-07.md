# 个人复盘与回放精度验收

日期：2026-09-07 UTC（本机 America/Los_Angeles 为 2026-09-06）。范围为本机 Docker 开发环境中的 `.dem` 上传、解析、2D 复盘和确定性规则建议。分支 `main`，基线 SHA `3016207145c37f0a8fea0f00b2da80c1165f5b73`，包含本次未提交修改；工作区状态记录于 `.local/qa/personal-review-git-status.txt`。

## 实现范围

- 默认复盘身份为 `xelex`；支持完整昵称或 Steam ID 匹配，忽略大小写，可在浏览器保存新的偏好。未匹配或重名时明确提示，通过比赛名单选择；选择复盘对象不改变登录身份或 owner。
- 所选玩家控制建议列表、前后建议、首条建议、时间线标记、地图事件和回合建议数量；个人建议严格匹配 `player_id`，证据参与者不会被误认为建议对象。回合胜负、全场事件统计与炸弹上下文保留。
- 常规位置采样从旧的约数秒间隔提升为 0.25 秒，并加入回合边界及事件前后 tick。常规采样预算约 30,000 tick，长比赛可能放宽间隔；采样回放不等于完整逐 tick 记录。
- 保留玩家、事件及炸弹的 Z 坐标，Nuke 使用上下层 radar 和集中配置的 overview 变换，支持手动切层或跟随所选玩家。缺失高度时明确提示。
- 炸弹状态覆盖 `unknown`、`carried`、`dropped`、`planted`、`defused`、`exploded`。位置可插值；生命值、存活、持弹状态不提前发生变化，不跨回合或旧稀疏帧推断连续移动。
- 教练卡片增加具体 `action` 和 `limitation`，标为 `review_candidate`。默认每人最多 48 条、每场最多 480 条，并保留 tick、规则和相关事件证据。
- 规则约束包括同一击杀者与完整 5 秒补枪窗口、事件之前不超过 1 秒的位置证据、已知 T 阵营的道具事件。距离使用 radar 百分点；Nuke 缺失高度或跨层时跳过相关几何判断。
- 回合摘要排除回合间击杀，炸弹拾取和掉落不替代下包快速跳转。

## 已完成的前端检查

以下检查与真实 API、浏览器验收均已完成。

- 全部 12 份 `frontend/lib/*.test.mjs` helper 测试通过，包括新增 `personal-review` 和 `replay-frames`。
- `npm run lint`、`npm run typecheck` 和 `npm run build` 通过；构建仅对进程设置 `NEXT_TELEMETRY_DISABLED=1`。
- 身份测试覆盖大小写、精确匹配、Steam ID、无匹配、重名、保存偏好、损坏数据和浏览器存储异常。
- 建议归属测试证明仅按 `player_id` 筛选，即使另一条建议的 `involvedPlayerIds` 或附加 metadata 包含该玩家，也不会混入个人建议。
- UI 输出测试覆盖未匹配、重名、明确查看其他玩家，以及建议的操作方向和证据局限展示。
- 回放 helper 测试覆盖离散状态在确切 tick 变化、不跨回合插值、不对旧稀疏帧插值、大量帧查找和空帧。
- 代码复核发现的楼层事件截断顺序已调整为先按显示楼层过滤，再限制附近事件数量；所选地图玩家已增加 `aria-pressed`。
- `scripts/verify.sh` 已包含所有 `lib/*.test.mjs` helper 测试。

## 最终真实数据与运行验收

四份现有比赛已经通过存储完整性校验、正常 Redis 队列和 worker 重新解析，保留原有 Demo ID 和名称，均为 `completed`。用户无需重新上传。其他旧录制文件仍需要重新解析才能获得新数据。

| 地图 | 回合 | 帧数 | 最大相邻间隔 | xelex 建议 | 全场建议 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dust2 | 21 | 13,820 | 0.25 秒 | 31 | 293 |
| Mirage | 24 | 15,462 | 0.25 秒 | 35 | 313 |
| Ancient | 16 | 11,456 | 0.25 秒 | 27 | 234 |
| Nuke | 19 | 12,262 | 0.25 秒 | 27 | 246 |

四场中 `xelex` 的 Steam ID 均为 `76561198998266210`。所有玩家帧都保留有限 Z，全部事件 tick 有对应帧。有效战斗死亡状态及炸弹同 tick 最终状态与事件一致；Ancient 中场的自杀并立即重生保留原始事实，不作为实战死亡验证。

| 验收项 | 当前记录 |
| --- | --- |
| 四份真实 `.dem` 的重新解析和队列完成状态 | 全部完成；`.local/qa/personal-review-reprocess.log` |
| 新帧数、回合数、玩家、建议数量及 API 健康 | 全部通过；`.local/qa/personal-api-check.json` |
| 炸弹状态、事件 tick 和有限 Z 核对 | 无不符；`.local/qa/precision-metrics.json` |
| 建议对象、回合、tick、源事件与补枪定义 | 1,086 条全部通过；公开 API 回放重新生成的完整 ID 集合及所有字段均与存储结果一致；`.local/qa/coaching-evidence-audit.json` |
| 最终 Linux 后端完整测试 | 452 项通过；`.local/qa/backend-public-precision-tests.log` |
| 前端检查 | 12 份 helper、lint、typecheck、生产 build 通过；`.local/qa/personal-review-final-frontend-roster-2026-09-07.log` |
| Docker 更新 | API、worker、frontend 已重建并启动；`.local/qa/personal-review-projection-build.log`、`.local/qa/personal-review-frontend-final-build.log` |
| 身份保存、玩家切换、建议跳转、回合联动 | 默认 xelex 27 条；切 Spinx 28 条；无匹配时不自动选别人；保存并刷新仍为 xelex；首条和卡片 Locate 跳至 R1/tick 6683；`.local/qa/personal-browser-check.log` |
| 播放控制 | tick 347 前进至 353，暂停后停止；2x 倍速可选；同上日志 |
| Nuke 自动/手动切层和炸弹状态 | R3/tick 21819 自动 Lower；上下层手动切换通过；23177 仍 carried、23178 为 planted；65309 为 defused；`.local/qa/floor-browser-check.log` |
| 窄屏、控制台错误及服务健康 | 390px 下文档/body 均 390px；浏览器 0 errors / 0 warnings；API health ok |

浏览器入口：[xelex 的 Nuke 复盘](http://localhost:3000/demos/3b728e96-3d5b-4f15-9039-b4d01a561a51)。主要控件为 `My name or Steam ID`、`Save identity`、`Player to review`、`First finding` 和 `Tactical map floor`。截图已人工查看：`output/playwright/xelex-personal-review.png`、`output/playwright/xelex-nuke-lower.png`、`output/playwright/xelex-mobile.png`。

验收实际发现并修复了公开 replay 字段白名单丢弃 Z 和楼层 metadata 的问题；新增回归经过持久化 artifact 与公开 `get_replay` 入口，仍验证内部路径不泄露。另修复地图标记编号与阵营名单编号不一致，以及楼层筛选晚于附近事件截断的问题。

保持 Docker Desktop 开启即可使用。需要重新启动时，在仓库根目录运行 `docker compose -f docker-compose.yml -f .local/qa/compose-loopback.yml up -d`。此本地覆盖配置绑定 `127.0.0.1:3000` 和 `127.0.0.1:8000`。

## 实际边界

这是本地 2D 复盘功能验收，不构成生产认证、公开部署、可靠任务恢复或专业教练判断准确率的验收。规则候选依赖可用事件和采样帧；视线、路径、沟通和意图不在证据中，数量上限也不保证涵盖所有问题。没有真实 CS2 第一人称视频或 GPU 自动渲染，没有 LLM 调用。原始 `.dem` 和大 replay artifact 不进入 Git。

丢弃炸弹的位置取事件玩家位置，不模拟空中轨迹；旧/未知高度不假定楼层。除 Dust2、Nuke 外的地图仍为 approximate 校准。Nuke 的 overview 与楼层阈值来源及雷达素材说明见 `frontend/public/maps/ATTRIBUTION.md`。本次未修改 render-worker，未扩大为 GPU 渲染验收。
