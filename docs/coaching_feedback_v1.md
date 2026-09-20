# Coaching Feedback V1

`docs/project_status_2026-09-13.md` 把「验证建议是否有用」列为体验迭代的第一项：用现有四场比赛，邀请玩家逐条判断**有帮助 / 无关 / 判断不足**，据此调整去重、持续时间和排序。本文档记录为此提供的机制，以及规则阈值的来源与状态。

核心前提不变：一条建议通过事实核对，只能证明它**符合规则条件**（`docs/real_review_quality_acceptance_2026-09-12.md`），不能证明那次选择是战术错误。**规则建议数量不是教练准确率**；只有玩家逐条判定后的"有帮助率"才是。

## 判定模型

| 判定 | 含义 |
| --- | --- |
| `helpful`（有帮助） | 这条建议指向了值得复盘的选择 |
| `irrelevant`（无关） | 事实没错，但对这一局的复盘没有价值（例如日常站位、开局重复提醒） |
| `unsure`（判断不足） | 采样距离/时间不足以判断，需要视线、路线或沟通等证据 |

- 每条建议、每个 owner 至多一条判定，可改可清除；可选 `note`（≤ 240 字符）记录原因。
- 判定以**事件 id** 为键。事件 id 由 `(demoId, ruleId, playerId, round, tickStart, tickEnd)` 确定性生成（`backend/app/analysis/rules.py`），重解析同一场比赛会重建同样的 id，所以判定跨重解析保留——阈值调整前后的对比正需要这个。规则变化后不再产生的建议，其判定不显示、不计入汇总。
- 判定是用户数据，**不改变规则输出**；规则仍是确定性的。
- 例外：mock demo 的建议 id 每次生成都是随机的（`mock_replay_service.py`），所以对模拟比赛的判定不会跨重解析保留——它们只是 UI 冒烟数据。
- 并发：同一条建议的两次判定同时到达时，唯一约束会拦下第二次插入，服务端把它当作更新处理；前端只落地仍与当前选择一致的响应，快速连点不会把界面退回旧判定。

## 界面

- Demo Detail 的每张建议卡片下方有「对你有帮助吗」三个按钮（`aria-pressed` 表示当前判定；再点一次当前判定即清除）。判定即时显示，服务端拒绝时回滚并提示。
- 「重点建议」面板头部显示当前复盘玩家的评价进度：`已评价 x/n`。进度只统计当前玩家的建议，与 project_status 的"邀请玩家逐条判断"对应。

## API

所有路由 owner-scoped（session / 开发头 `X-Dev-User-Id`），响应带 `Cache-Control: private, no-store`。

| 路由 | 说明 |
| --- | --- |
| `GET /demos/{demo_id}/coaching` | 每条事件附带当前 owner 自己的 `feedback`：`{ "verdict", "note", "updated_at" }` 或 `null` |
| `PUT /demos/{demo_id}/coaching/{event_id}/feedback` | body `{ "verdict": "helpful" \| "irrelevant" \| "unsure", "note"?: string }`；事件不属于该 demo → `404`；非法 verdict / 过长 note → `422` |
| `DELETE /demos/{demo_id}/coaching/{event_id}/feedback` | `204`，幂等 |
| `GET /coaching/feedback/summary?demo_id=` | 当前 owner 全部（或指定）demo 的按规则汇总：`{ demo_count, total, rated, helpful, irrelevant, unsure, rules: [{ rule_id, total, rated, helpful, irrelevant, unsure }] }` |

另一个 owner 对同一 demo/事件的任何操作都是 `404`，其判定也不会出现在他人的列表或汇总里。

存储：`coaching_feedback` 表（`backend/app/models/coaching.py`），由 `Base.metadata.create_all` 在启动时创建；`(owner_id, event_id)` 唯一；`event_id` 有意不设外键（原因见上）。

## 评估流程（四场比赛）

1. 以 xelex 为复盘玩家打开四场比赛，对每条建议打判定；`已评价 x/n` 到 `n/n` 为止。
2. 读 `GET /coaching/feedback/summary`：按规则看 `helpful / rated`。样本小，先看方向而不是精确比例。
3. 先动**去重、持续时间和排序**，再动距离阈值：2026-09-12 的核对已提示 120 条个人候选里 78 条是单次采样的站位提示、35 条出现在冻结结束后 15 秒内——这些最可能被判为「无关」，对应 `RuleConfig` 里的 `dedupe_tick_window_seconds`、`max_events_per_round_per_rule`、`max_position_age_seconds` 与排序，而不是距离常数。
4. 调整后重解析：判定保留，再看同一批建议的判定分布如何变化；规则不再产生的建议自动退出汇总。
5. 保留证据与规则局限的展示；不因判定数据引入 LLM 生成式分析。

## 阈值来源与状态

`backend/app/analysis/rules.py` 的 `RuleConfig`。距离单位是 CS2 世界单位（wu）：16 wu = 1 英尺 ≈ 30.5 cm，即 1 wu ≈ 1.9 cm；玩家约 32 wu 宽、72 wu 高，跑动约 250 wu/s。米制换算只作直觉参考。

| 阈值 | 值 | 用途 | 来源 | 状态 |
| --- | --- | --- | --- | --- |
| `trade_window_seconds` | 5.0 s | 死亡后多长时间内队友击杀凶手算被补枪 | 社区惯例 | 经验值，未经判定校准 |
| `same_area_distance` | 540 wu（≈ 10 m） | 「同一区域」判定 | 12 雷达百分点 × 45.056 wu/% | 经验值，未经判定校准 |
| `isolated_teammate_distance` | 990 wu（≈ 19 m） | 开局死亡时最近存活队友超过此距离 → 孤立进场 | 22 % × 45.056 | 经验值，未经判定校准 |
| `poor_spacing_min_distance` | 112 wu（≈ 2 m） | 两人近于此 → 站位过近（受高度差保护） | 2.5 % × 45.056 | 经验值，未经判定校准 |
| `poor_spacing_max_distance` | 1260 wu（≈ 24 m） | 最近队友远于此 → 站位过远 | 28 % × 45.056 | 经验值，未经判定校准 |
| `max_stacked_vertical_distance` | 128 wu（≈ 2.4 m） | XY 相近但高度差超过此值不算过近 | 2026-09-12 Nuke 平台案例（实测高度差 192–289 wu），取保守值 | 有案例依据，仍属保守筛选 |
| `post_plant_cluster_distance` | 270 wu（≈ 5 m） | 下包后存活 T 全部在此距离内且持续 ≥ 4 s、≥ 3 人 → 扎堆 | 6 % × 45.056 | 经验值，未经判定校准 |
| `retake_site_distance` | 540 wu（≈ 10 m） | CT 距炸弹在此距离内算到点，用于 4 s 回防不同步 | 12 % × 45.056 | 经验值，未经判定校准 |
| `execute_utility_window_seconds` / `min_execute_utility_events` | 12 s / 2 | 下包前 12 s 内道具少于 2 个 → 进攻缺道具 | 经验值 | 未经判定校准 |
| `post_plant_utility_grace_seconds` | 6 s | 下包后 6 s 内无道具 → 下包后道具迟 | 经验值 | 未经判定校准 |
| `dedupe_tick_window_seconds` / `max_events_per_round_per_rule` / `max_position_age_seconds` | 3 s / 1 / 1 s | 去重、每回合每规则上限、位置采样新鲜度 | 经验值 | **判定数据首先应作用于这一组** |
| `max_events_total` / `max_events_per_player` | 480 / 48 | 全场与单人建议上限 | 经验值 | 未经判定校准 |

「x % × 45.056」表示这些常数原本以雷达百分比定义，后按 Dust II 的 45.056 wu/% 换算为世界单位，使 Dust II 行为不变、其他地图按同一物理尺度衡量（Dust II 雷达跨 4506 wu，Nuke 跨 7168 wu）。换算解决的是"同一个阈值在不同地图代表不同距离"的问题，**没有解决**"这个距离是否真的意味着战术问题"——后者只能由上面的判定数据回答。

## 不在本版范围

- 不按判定自动调参：阈值仍是代码里的常数，改动经过测试与验收记录。
- 不做跨用户的汇总或排行：判定只对 owner 自己可见。
- 不引入 LLM：判定用于校准确定性规则，不生成文案。
