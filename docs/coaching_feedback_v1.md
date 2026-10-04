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
- 判定以**事件 id** 为键。事件 id 由 `(demoId, ruleId, playerId, round, tickStart, tickEnd)` 确定性生成（`backend/app/analysis/rules.py`；`extraReasons` 等附加字段不参与），重解析或后台重算同一场比赛时，规则没变的建议得到同样的 id，所以判定跨重解析、跨重算保留——阈值调整前后的对比正需要这个。
- 规则变化后不再产生的建议：判定行保留（`event_id` 不设外键），但不显示、不计入汇总；以后的规则版本重新产生同一个 id 时，它会再次出现。这些行和其他判定一样，随比赛或账户一起删除。
- 判定是用户数据，**不改变规则输出**；规则仍是确定性的。
- 例外：mock demo 的建议 id 每次生成都是随机的（`mock_replay_service.py`），所以对模拟比赛的判定不会跨重解析保留——它们只是 UI 冒烟数据。
- 并发：同一条建议的两次判定同时到达时，唯一约束会拦下第二次插入，服务端把它当作更新处理；前端只落地仍与当前选择一致的响应，快速连点不会把界面退回旧判定。
- **判定随比赛一起删除**：
  - 永久删除一场比赛时，它的全部判定在同一个事务里删除，不保留匿名计数或其他残留；
  - 删除账户时，这个 owner 的全部判定都会删除；
  - 删除后，`GET /coaching/feedback/summary` 里对应的计数随之减少。用汇总调阈值时，要记得样本可能因为删除而变少。
  - 软归档不影响判定。
  - 协议见 [data_deletion_v1](data_deletion_v1.md)。

## 界面

- Demo Detail 的每张建议卡片下方有「对你有帮助吗」三个按钮（`aria-pressed` 表示当前判定；再点一次当前判定即清除）。判定即时显示，服务端拒绝时回滚并提示。
- 「重点建议」面板头部显示当前复盘玩家的评价进度：`已评价 x/n`。进度只统计当前玩家的建议，与 project_status 的"邀请玩家逐条判断"对应。
- 当前玩家的建议多于 5 条时，「全部回合」视图（没有筛选和搜索时）顶部的「本场最值得回看」先列出最重要的 5 条，下面的回合分组只列其余的，不重复。回合条、时间轴、顶部、`已评价 x/n` 和「全部回合 N 条」仍是总数；回合标题的条数只算下面列出的那几条。重要性依次看：回合输了、本回合第一个阵亡、阵亡后人数劣势、附加原因的条数，再按严重程度、回合、tick；没有 `impact` 的旧建议排在后面。
- 判定保存返回 `404` 时（通常是比赛页打开期间，这条建议在后台重算里消失了），卡片提示「建议已按新规则更新，请刷新页面」，不提供重试。

## API

所有路由 owner-scoped（session / 开发头 `X-Dev-User-Id`），响应带 `Cache-Control: private, no-store`。

| 路由 | 说明 |
| --- | --- |
| `GET /demos/{demo_id}/coaching` | 每条事件附带当前 owner 自己的 `feedback`：`{ "verdict", "note", "updated_at" }` 或 `null` |
| `PUT /demos/{demo_id}/coaching/{event_id}/feedback` | body `{ "verdict": "helpful" \| "irrelevant" \| "unsure", "note"?: string }`；事件不属于该 demo（包括后台重算后已不存在的建议），或比赛在保存过程中被删除 → `404`；非法 verdict / 过长 note → `422` |
| `DELETE /demos/{demo_id}/coaching/{event_id}/feedback` | `204`，幂等 |
| `GET /coaching/feedback/summary?demo_id=` | 当前 owner 全部（或指定）demo 的按规则汇总：`{ demo_count, total, rated, helpful, irrelevant, unsure, rules: [{ rule_id, total, rated, helpful, irrelevant, unsure }] }` |

另一个 owner 对同一 demo/事件的任何操作都是 `404`，其判定也不会出现在他人的列表或汇总里。

存储：`coaching_feedback` 表（`backend/app/models/coaching.py`），由 `Base.metadata.create_all` 在启动时创建；`(owner_id, event_id)` 唯一；`event_id` 有意不设外键（原因见上）。

`demo_id` 外键带 `ON DELETE CASCADE`，但删除流程不依赖它：删除比赛时先显式删掉 `coaching_feedback`，再删 `coaching_events` 和 `demos`。SQLite 测试默认不检查外键，只靠级联在测试里会留下残行。

## 评估流程（四场比赛）

1. 以 xelex 为复盘玩家打开四场比赛，对每条建议打判定；`已评价 x/n` 到 `n/n` 为止。
2. 读 `GET /coaching/feedback/summary`：按规则看 `helpful / rated`。样本小，先看方向而不是精确比例。
3. 先动**去重、持续时间和排序**，再动距离阈值：2026-09-12 的核对已提示 120 条个人候选里 78 条是单次采样的站位提示、35 条出现在冻结结束后 15 秒内——这些最可能被判为「无关」，对应 `RuleConfig` 里的 `dedupe_tick_window_seconds`、`max_events_per_round_per_rule`、`max_position_age_seconds` 与排序，而不是距离常数。
4. 改规则 → 提升 `COACHING_RULES_VERSION` → worker 在后台逐场重算已有比赛的建议（见下一节），不需要重新上传或重解析。id 没变的建议保留判定，再看同一批建议的判定分布如何变化；规则不再产生的建议自动退出汇总。
5. 保留证据与规则局限的展示；不因判定数据引入 LLM 生成式分析。

## 规则更新后的后台重算

- **版本号**：`backend/app/analysis/version.py` 的 `COACHING_RULES_VERSION`（当前 `coaching_rules_v3`）。只要分析器的输出变了（出哪些建议、tick、id 或存下的上下文），就要提升它；只改前端文案不需要。
- **标记**：比赛最新解析任务的元数据里记 `coachingRulesVersion`，解析完成时写入当前版本；没有标记的比赛算作旧版本。后台补解析回放（回放升级）不重新分析，会保留这个标记。
- **哪些比赛会重算**：状态是 `completed`，最新解析任务是已完成的真实解析（含 Steam 导入；示例比赛的建议 id 是随机的，不重算），标记不是当前版本，没有用完尝试次数，到了重试时间，并且回放升级不在排队（没有待升级的回放，或升级已用完次数）——这样回放刚升级的比赛只在新回放落地后重算一次。未归档的优先，新的优先。
- **怎么跑**：worker 空闲时，在回放升级之后运行，约 30 秒一轮，每轮最多一场，队列里有任务就让出；出错不会让 worker 退出。先给这次尝试计数，再在事务之外经存储包读回放（核对回放里的 `demoId` 就是这场比赛）、运行 `analyze_replay`；然后在一个短事务里先锁任务、再锁比赛（与删除和回放升级的加锁顺序相同），确认比赛还在、仍是 `completed`、回放引用、最新解析任务和它的元数据都没变，才删掉这场比赛的旧建议、写入新建议、更新 `coaching_event_count`、写入新标记。任何一项变了就回滚，什么都不写，并退还这次计数；比赛在这期间被删除时同样如此，不会把建议写回来。
- **不变的**：比赛的 `status`、`completed_at`、`updated_at` 不变；不写上传账本；事务里不做存储 I/O；不碰 `coaching_feedback`。
- **失败**：回放读不出（`REPLAY_UNREADABLE`）、回放的 `demoId` 对不上（`REPLAY_ID_MISMATCH`）或分析出错（`ANALYZE_FAILED`）时保留旧建议，记一个短错误码（不存堆栈），按 `COACHING_RECOMPUTE_RETRY_SECONDS` 起指数退避；`COACHING_RECOMPUTE_MAX_ATTEMPTS` 次后不再重试，这场比赛保留旧建议。次数上限按规则版本计：下次提升 `COACHING_RULES_VERSION` 后，之前用完次数的比赛会重新获得完整的尝试次数。worker 中途退出、没跑完的那次也计数，最后记为 `RECOMPUTE_ABANDONED`。
- **开关**：`COACHING_RECOMPUTE_ENABLED`（默认开），见 [Configuration Reference](configuration_reference_v1.md)。
- **判定**：id 不变的建议照常显示判定并计入汇总；消失的建议见上文「判定模型」。

## 当前规则（`coaching_rules_v3`）

分析器现在有 9 条规则：`untraded_death`、`isolated_entry`、`poor_spacing`（只剩站位过近）、`post_plant_spacing_with_bomb_event`、`post_plant_spread_issue`、`retake_desync`、`weak_utility_before_execute`，以及 v3 新增的两条射击规则 `moving_shots`（移动射击）和 `no_counter_strafe`（第一枪没急停）。v3 没有改前七条规则的判定；射击卡片同样计入全场和单人的建议上限（`max_events_total` / `max_events_per_player`）。

### v3：两条射击规则

两条规则只看回放 v5 的开枪记录（`shots`，每一枪的 tick、水平速度、是否在空中和武器，见 [API Reference](api_reference_v1.md#开枪记录shotsv5)），再加上已有的 `damage` 和击杀事件，有按键记录（v3 `inputs`）时还读按键。没有开枪记录的回放（v1–v4，或抽取失败）不出这两类建议；整场一个 `damage` 事件都没有时也不出（分不清是没打中还是没记录）。较早上传的比赛先由后台回放升级重新解析成 v5，再由建议重算算出它们。类别都是 `mechanics`，严重程度都是 `low`。

- **只判这些枪**：步枪 AK-47、M4A4、M4A1-S、Galil AR、FAMAS、AUG、SG 553；狙击枪 AWP、SSG 08、SCAR-20、G3SG1；沙鹰和 R8 左轮。冲锋枪、霰弹枪、其他手枪和机枪本来就常边跑边打，一律不判，免得刷屏。
- **稳定线**：CS2 里移动速度不超过这把枪最大移动速度的 34 % 时开枪是准的，稳定线 = 最大速度 × 34 % 取整（每把枪的值见下方阈值表）。
- **一串**：同一名玩家在同一回合里用同一把（上面这些）枪开的枪，相邻两枪间隔不超过 0.5 秒，算同一串；换枪、中间夹了一枪别的枪、或间隔超过 0.5 秒，就开始新的一串。只看回合进行中的枪（冻结时间结束到回合结束），热身和回合之间的枪不算。
- **首串**：这一串第一枪之前 1 秒内，这名玩家没有开过任何一枪（不判的枪也算）。
- **移动中开枪**：速度高于稳定线，或者在空中。
- **打中**：开枪的 tick 到其后 2 tick 之内，有这名玩家作为攻击者、用枪造成的 `damage` 事件（道具、火和自伤不算）。
- **阵亡**：这一串最后一枪之后 2 秒内，这名玩家阵亡。
- **`moving_shots`（移动射击）**：一串里至少 3 枪是移动中开的，而且这些枪一枪都没打中。
- **`no_counter_strafe`（第一枪没急停）**：首串的第一枪速度高于稳定线 + 40，或者在空中；这一串不满足移动射击；并且结果不好：整串一枪没中，或者打完 2 秒内阵亡。
- **每名玩家每回合最多一张**：两条规则合起来，同一回合同一名玩家只出一张卡，有移动射击就出它，否则出这一回合最早的第一枪没急停；`occurrencesInRound` 记这一回合符合任一规则的串数。卡片从第一枪前 0.5 秒开始（不早于回合的冻结时间结束，没有这个 tick 时不早于回合开始），到最后一枪后 0.25 秒结束。
- **第一枪没急停每场最多 3 张**：每名玩家整场最多留 3 张第一枪没急停，先留打完 2 秒内阵亡的，再留第一枪速度更快的，速度相同时留更早的；移动射击不设整场上限。在四场样例上（2026-10-04，Spirit vs MOUZ），不设这个上限时单人一场最多 5 张第一枪没急停；设上限后两条规则合计每场 18–30 张、平均每人 1.8–3 张，单人最多 6 张（其中 4 张是移动射击）。
- **按键**：按键记录覆盖第一枪时，卡片记下第一枪时按着的移动键（`keysAtShot`），以及第一枪前 0.15 秒内有没有反向急停（`counterStrafe`：这段时间里按下某个移动键时，它的反方向键在这段时间里按过）。没有按键记录时这两项省略。按键只作说明，不影响是否出卡。
- **局限**：速度取自每一枪记录的移动速度；没有计算弹道恢复、蹲下、开镜和对手的移动，没打中也可能有别的原因。卡片的"规则局限"写的就是这一句。

字段形状见 [API Reference](api_reference_v1.md#建议事件的结构化上下文structured_context_json)。

### v2 相对上一版的变化

旧规则在四场真实比赛上共出 1,028 条，其中站位过近 431 条、站位过远 233 条，v2 主要减少这两类。相对上一版的变化（严重程度、`untraded_death` 的 5 秒补枪窗口、`isolated_entry` 的触发条件都不变）：

- **站位过近**：冻结时间结束 15 秒之后才开始检查（原来是 8 秒）；同一对队友要连续过近至少 3 秒，或者这两人在 3 秒内被同一名敌人先后击杀，才出建议；仍是每回合每名玩家最多一条。建议的起点是这段的开始，并带 `durationSeconds`；连杀触发的另带 `stackedMultikill`。
- **站位过远不再单独出建议**，只作为阵亡卡片的附加原因（`extraReasons`）：阵亡者有一段持续至少 3 秒的站位过远（同样从冻结结束 15 秒后算，T、CT 都算），并且和阵亡前 5 秒有重叠时才附上。
- **每次阵亡一张卡**：有 `untraded_death` 时它就是这张卡，同一次阵亡的孤立进场并成它的附加原因；没有时 `isolated_entry` 是这张卡。按击杀事件的 id 对应。阵亡卡片带 `impact`（回合输赢、是否本回合第一个阵亡、阵亡前后双方存活人数、是否因此陷入人数劣势）和击杀武器 `weapon`。
- **最后一名存活者的阵亡不再报"未被补枪"**：阵亡前最后一帧里已经没有存活队友，补不了枪。
- **删除 `late_post_plant_utility`（下包后道具迟）**。旧比赛重算前或重算失败时，界面仍能显示这类旧建议。
- **`post_plant_spread_issue` 并入 `post_plant_spacing_with_bomb_event`**：有下包事件的回合只由后者判断，前者只看没有下包事件的回合。
- **回防不同步（`retake_desync`）**：首个下包帧里已经在炸弹附近（`retake_site_distance` 以内）的 CT 不算"到点"；最后一个计入的 CT 到点时已经没有存活的 T，这个回合不报。
- **进攻缺道具（`weak_utility_before_execute`）**：T 方整回合一个道具都没用时也报（原来跳过），只有 T 方这一回合是 ECO 时不报；记下 `tBuyKind`。经济类型由 `backend/app/analysis/round_economy.py` 判定，它是 `frontend/lib/round-economy.ts` 的移植，队伍与阵营沿用比分摘要的阵营规则；两边由共享用例 `fixtures/round-economy/` 固定。

字段形状见 [API Reference](api_reference_v1.md#建议事件的结构化上下文structured_context_json)。

## 阈值来源与状态

`backend/app/analysis/rules.py` 的 `RuleConfig`。距离单位是 CS2 世界单位（wu）：16 wu = 1 英尺 ≈ 30.5 cm，即 1 wu ≈ 1.9 cm；玩家约 32 wu 宽、72 wu 高，跑动约 250 wu/s。米制换算只作直觉参考。

| 阈值 | 值 | 用途 | 来源 | 状态 |
| --- | --- | --- | --- | --- |
| `trade_window_seconds` | 5.0 s | 死亡后多长时间内队友击杀凶手算被补枪 | 社区惯例 | 经验值，未经判定校准 |
| `same_area_distance` | 540 wu（≈ 10 m） | 「同一区域」判定 | 12 雷达百分点 × 45.056 wu/% | 经验值，未经判定校准 |
| `isolated_teammate_distance` | 990 wu（≈ 19 m） | 开局死亡时最近存活队友超过此距离 → 孤立进场 | 22 % × 45.056 | 经验值，未经判定校准 |
| `poor_spacing_min_distance` | 112 wu（≈ 2 m） | 两人近于此 → 站位过近（受高度差保护，还要满足下面的持续时间或连杀条件） | 2.5 % × 45.056 | 经验值，未经判定校准 |
| `poor_spacing_max_distance` | 1260 wu（≈ 24 m） | 最近队友远于此 → 站位过远（只作为阵亡卡片的附加原因） | 28 % × 45.056 | 经验值，未经判定校准 |
| `poor_spacing_eval_delay_seconds` | 15 s | 冻结时间结束后多久才开始检查站位过近和过远 | 方案 A（2026-10-02）；原为写死的 8 s | 未经判定校准 |
| `poor_spacing_min_duration_seconds` | 3 s | 站位过近（同一对队友）或过远要连续这么久才算 | 方案 A；原为单次采样即算 | 未经判定校准 |
| `stacked_multikill_window_seconds` | 3 s | 站位过近的两人在此时间内被同一名敌人先后击杀 → 不满 3 s 也出建议 | 方案 A | 未经判定校准 |
| `max_stacked_vertical_distance` | 128 wu（≈ 2.4 m） | XY 相近但高度差超过此值不算过近 | 2026-09-12 Nuke 平台案例（实测高度差 192–289 wu），取保守值 | 有案例依据，仍属保守筛选 |
| `post_plant_cluster_distance` | 270 wu（≈ 5 m） | 下包后存活 T 全部在此距离内且持续 ≥ 4 s、≥ 3 人 → 扎堆 | 6 % × 45.056 | 经验值，未经判定校准 |
| `retake_site_distance` | 540 wu（≈ 10 m） | CT 距炸弹在此距离内算到点，用于 4 s 回防不同步；首个下包帧里已在此距离内的 CT 不算 | 12 % × 45.056 | 经验值，未经判定校准 |
| `execute_utility_window_seconds` / `min_execute_utility_events` | 12 s / 2 | 下包前 12 s 内 T 方道具少于 2 个 → 进攻缺道具（整回合没用道具也算，ECO 回合除外） | 经验值 | 未经判定校准 |
| `shot_weapon_max_speeds` / `shot_accurate_speed_ratio` | AK-47 215 → 73；M4A4、M4A1-S 225 → 76；Galil AR 215 → 73；FAMAS、AUG 220 → 75；SG 553 210 → 71；AWP 200 → 68；SSG 08 230 → 78；SCAR-20、G3SG1 215 → 73；Desert Eagle 230 → 78；R8 220 → 75（最大移动速度 → 稳定线，u/s）/ 0.34 | 哪些枪参与射击规则；速度高于稳定线（= 最大速度 × 0.34，Python `round` 取整）算移动中开枪 | CS2 各武器的最大移动速度；"速度不超过最大速度的 34 % 时开枪是准的"是 CS2 的常用说法 | 未经判定校准 |
| `shot_burst_gap_seconds` | 0.5 s | 同一把枪相邻两枪间隔不超过此值算同一串 | 经验值（2026-10-04 在四场样例上的研究） | 未经判定校准 |
| `shot_opener_quiet_seconds` | 1 s | 第一枪之前这么久没开过枪 → 首串 | 经验值 | 未经判定校准 |
| `counter_strafe_margin` | 40 u/s | 首串第一枪速度高于稳定线 + 此值（或在空中）→ 第一枪没急停 | 经验值，给急停的减速留余量 | 未经判定校准 |
| `moving_shots_min` | 3 | 一串里移动中开枪至少这么多且都没中 → 移动射击 | 经验值 | 未经判定校准 |
| `shot_hit_window_ticks` | 2 tick | 开枪的 tick 到其后这么多 tick 内有自己用枪造成的 `damage` → 这一枪打中 | 经验值 | 未经判定校准 |
| `shot_death_window_seconds` | 2 s | 最后一枪后这么久内阵亡 → 结果不好 | 经验值 | 未经判定校准 |
| `counter_strafe_window_seconds` | 0.15 s | 第一枪前这么久内反向急停 → `counterStrafe: true`（只作说明，不影响是否出卡） | 经验值 | 未经判定校准 |
| `shot_card_lead_seconds` / `shot_card_tail_seconds` | 0.5 s / 0.25 s | 射击卡片从第一枪前多久开始、到最后一枪后多久结束 | 经验值 | 只影响"查看这一刻"的位置 |
| `no_counter_strafe_max_per_match` | 3 | 每名玩家整场最多这么多张第一枪没急停（阵亡的优先，其次第一枪更快的） | 2026-10-04 四场样例：不设上限时单人最多 5 张 | 未经判定校准 |
| `dedupe_tick_window_seconds` / `max_events_per_round_per_rule` / `max_position_age_seconds` | 3 s / 1 / 1 s | 去重、每回合每规则上限、位置采样新鲜度 | 经验值 | **判定数据首先应作用于这一组** |
| `max_events_total` / `max_events_per_player` | 480 / 48 | 全场与单人建议上限 | 经验值 | 未经判定校准 |

「x % × 45.056」表示这些常数原本以雷达百分比定义，后按 Dust II 的 45.056 wu/% 换算为世界单位，使 Dust II 行为不变、其他地图按同一物理尺度衡量（Dust II 雷达跨 4506 wu，Nuke 跨 7168 wu）。换算解决的是"同一个阈值在不同地图代表不同距离"的问题，**没有解决**"这个距离是否真的意味着战术问题"——后者只能由上面的判定数据回答。

## 不在本版范围

- 不按判定自动调参：阈值仍是代码里的常数，改动经过测试与验收记录。
- 不做跨用户的汇总或排行：判定只对 owner 自己可见。
- 不引入 LLM：判定用于校准确定性规则，不生成文案。
