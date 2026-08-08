# 2026-07-30 待修问题清单（按优先级）

> 承接 `7_28.md` / `7_29.md`（均已并入）。本文按「能不能立刻动手」分层，不按发现顺序。
> 每条都标注了**已证实 / 已推断 / 未定**，别把推断当结论去改代码。
>
> **2026-08-07 全文重核**：基线从 `943392f` 推到 `6edbe43`（多了 !55 / !56 加三个未合分支的 commit）。
> 行号全部实测重取（原文行号已大面积漂移），已修的条目删掉或收成残口，新证伪的进「已证伪」。

## 状态基线

`main` = `5019d1c`（= !56 的 merge commit）。六轮 PR 全部 merge commit，分支侧 SHA 保留（未 squash）。
`HEAD` = `6edbe43`，在分支 `fix/skill-doc-encoding-and-python-channel` 上**领先 main 三个 commit**。

| PR | commit | 内容 |
|---|---|---|
| **!51** | `ad2c0ac` / `5ab77aa` / `9c5af5e`（merge `fd5ed31`） | 沙箱默认值 + 随包 CPython 3.13.12（`bundle-resources.json:71`）+ 6 个 SKILL.md 改写 + provenance 补齐 |
| **!52** | `1e58017` / `0aedb3d`（merge `95eea10`） | 重试原子化（不再闪回首页）+ manager lockfile |
| **!53** | `294c3c7`（merge `6fbf544`） | 对话 id 唯一化 + 删除单数化 + 拒删运行中的对话 |
| **!54** | `96667c1`（merge `943392f`） | 用户意图与技能身份成为一等状态（附件/技能不再消失 + 文案统一 + 运行中可浏览） |
| **!55** | `5f4be33`（merge `ff5016e`） | 启动时刷新 `~/.codex/plugins/cache/` 里的 SKILL.md（新增 `main.rs:482` `refresh_cached_skill_docs`）—— `plugin marketplace add` 只改源指针，批量启用又被一次性 marker `.wa-initialized`（`main.rs:641`）挡着，老装机永久钉在首次启动那天的 SKILL.md |
| **!56** | `d8fc6a4` / `7e50fb9`（merge `5019d1c`） | `attachments.ts` 按附件扩展名生成读法；对抗审查后改掉四处：图片走 codex 自带 `view_image`、纯文本改由随包 Python 按 utf-8-sig→utf-8→gb18030 探测（`Get-Content -Raw` 在 PS 5.1 下按 ACP 解码、退出码仍是 0）、目录附件、脚本落到 `$env:TEMP` |

**下面三条已 commit、还没进 main**（都在 `fix/skill-doc-encoding-and-python-channel` 上）：

| commit | 内容 |
|---|---|
| `feea75a` | 8 份 SKILL.md 加纯 ASCII 硬规则段 + description 里塞读取编码提示；`{{WA_PYTHON_ABS}}` 占位符（6 份产出型技能），刷新时渲染绝对路径并剥掉 `\\?\` 前缀；新增门禁 `scripts/skill-docs.test.mjs`（BOM / 开头空行 / GBK 三种「静默整条丢技能」的写法全部钉死，变异测试验过真能挂） |
| `db96b78` | 新增 `main.rs:1953` `stage_attachments`，上传文件由应用侧复制进 `<产出目录>/上传文件/`（`main.rs:1923` 的 `ATTACHMENT_STAGE_DIR`），`detectDeliverables` 排除该目录 —— 修的是 `elevated` 档位下 codex 以独立本地账户跑命令、别的盘/搬迁过的目录读不到的身份不对称 |
| `6edbe43` | `enable_all_p0_skills`（`main.rs:693`）在 `plugin add` 循环之后补一次渲染 —— 否则全新安装的**首轮**会话里 `{{WA_PYTHON_ABS}}` 字面量原样送到模型眼里 |

**!51 的标题会误导**：gitee 上显示的是分支最后一个小补丁的标题，主体（随包 Python）在 `5ab77aa` 里。

安装包：桌面 `工作管家_0.1.0_x64-setup_6edbe43-PPT与D盘附件修复.exe`，99,627,747 字节（95.0 MiB），
SHA-256 `91d46b6d8ca4d6053c9d7500afb2c548437dea65878029a8b12073e4cd0f6f67`，出包时间 2026-07-31 15:59。
按文件名基于 `6edbe43`，即**含 !54 / !55 / !56 以及三个未进 main 的 commit** —— F1/F2/F3 已经可以真机验，
不必再为它们出包。（只核到文件名与字节哈希，没从包内容反证构建 SHA。）
旧的 `fix8`（基于 `6fbf544`、不含 !54）已从桌面移除。

测试基线（2026-08-07 在 `6edbe43` 上实跑）：**480 vitest（18 文件）+ 40 node 测试（3 文件）+ 217 cargo**，
`tsc -p tsconfig.json --noEmit` 干净（退出码 0、无输出）。
（`npm test` = `vitest run --exclude "scripts/*.test.mjs" && npm run test:bundle`，
`test:bundle` = `node --test scripts/*.test.mjs`；cargo 侧在 `apps/working-agent/src-tauri` 下 `cargo test`）
原文写的「455 vitest + 2 个 node 测试文件」是 `943392f` 时的数：vitest 文件数没变、条数 +25；
node 侧多出 `scripts/skill-docs.test.mjs`（8 条）；cargo 当时没记，轨迹是 211（`feea75a`）→ 216（`db96b78`）→ 217（`6edbe43`）。
**`cargo test` 第一次跑常挂在 `os error 32`**（build.rs 撞上 `python-runtime\libcrypto-3.dll` 被占用），重跑即过。

---

# 第零层 · P0，出包前必须堵

## 0.1 任意网页能在用户机器上起一个无审批的 agent

**这条是外部审计（`Working-Agent-Bug.md` WA-003）报的，但它的立论是错的、结论偏低。**
它写的是「同一台机器上的其他进程可以伪装成 helper」—— 那个方向**立不住 P0**：同用户的本地攻击者
没有跨越任何权限边界，它能拿到的（key、会话原文、杀进程）直接读文件或 `taskkill` 全都能拿到。

**真正的高危方向审计只当小项一笔带过：攻击者可以是一个网页，不需要任何本地代码执行。**

三条路径，逐条都是实的（基线之后的 8 个 commit 一个都没碰过 `launcher.rs` / `protocol_proxy.rs`，
全仓 `helper_token` / `X-Working-Agent` 之类零命中 —— 随机 token 方案完全没落地）：

1. **WebSocket 不受 CORS 约束，而我们主动把 Origin 头删了。**
   `launcher.rs:1968-1970` 转发前**主动剥掉** Origin（连 `Sec-WebSocket-Protocol` 一起剥）——
   不是「忘了检查」，是把上游可能存在的 Origin 门也一并废掉。处理函数
   `handle_app_server_websocket_proxy_connection`（`launcher.rs:1922-1955`）除了
   `ensure_app_server_runtime()` 之外**没有任何来源或凭据校验**，改写完请求就
   `copy_bidirectional` 直通 app-server。于是任意恶意网页可直连
   `ws://127.0.0.1:57321/app-server/ws`，按 `engine.ts:1016-1017` 的
   `sandbox:'workspace-write'` + `approvalPolicy:'never'` 起 thread、投喂 `turn/start`。
   **从访问一个网页，直达在用户机器上跑一个无审批的 workspace-write agent。**
2. **`ACAO: *` 把付费额度和补全内容开放给任意网页。**
   OPTIONS 预检在 `launcher.rs:1059-1062` 一律回 `204` + `Access-Control-Allow-Origin: *`
   + `Allow-Headers: Content-Type, Authorization`，**与 path 无关** —— 连 404 unknown_path
   分支也照发（`1065`）。而 `POST /v1/responses` 早在 `launcher.rs:938` 就被
   `is_responses_proxy_path` 截进 `handle_protocol_proxy_connection`，它的响应头由
   `write_http_stream_headers`（`launcher.rs:3407`）/ `write_http_response`（`3369`）/
   `write_http_no_store_response`（`3384`）发出 —— **这三处 ACAO 同样是 `*`**。
   任意页面能跨源 POST `/v1/responses` 并读到返回；而 helper 会用
   `protocol_proxy.rs:769-772` 的 `bearer_auth` 附上用户**真实 ShrimpAI key**。
   `open_responses_proxy_request`（`protocol_proxy.rs:479-528`）不看 Origin、也不拿
   User-Agent 做鉴权（UA 只是原样透传给上游）。
   **2026-08-07 补充，这条比原先写的更重**：代理**完全不看调用方的 `Authorization` 头** ——
   key 一律从本地 SettingsStore 取出来贴上（`protocol_proxy.rs:686-717`，
   `validate_upstream` 在 `:781-789`），**调用方连一个假 token 都不用带**。
   而 `logout` 不回收本地 key（见第五层），所以这个面在**退出登录之后依然开着**。
   净效果：用户浏览器里打开的任意网页 = 无限额度 + 能读回全部内容，无需任何本机代码执行。
3. **`/shutdown` 是 simple request，任意网页能直接把引擎打死。**
   `launcher.rs:988` 有 `remote_addr…is_loopback()` 来源检查（所以审计说「完全无鉴权」不精确），
   但对浏览器而言这个检查天然通过 —— 请求确实来自 loopback。
4. **未登录的机器上，WS 那条路会去借用户自己的官方 codex 凭据**（2026-08-07 新增）。
   从没登录过工作管家时 `ensure_provider` 读不到 key 就 `Ok(false)` 什么都不写
   （`relay_config_tx.rs:271-279`），于是 codex 用它**自己的默认 provider**；而 helper spawn
   `codex app-server` 时**不注入任何环境变量、不设 `CODEX_HOME`**
   （`launcher.rs:2097-2114` / `2116-2131`），进程直接继承默认 `~/.codex`。
   **所以在装过官方 codex CLI / 登过 ChatGPT 的机器上，任何本机进程（或任意网页，见第 2 条）
   打 `ws://127.0.0.1:57321/app-server/ws` 就能跑完整 agent 轮次，花的是用户自己的 OpenAI 额度，
   全程零登录、根本走不到「上游 Key 不能为空」那道检查。**
   原先以为「没 key 就会被 helper 502 拒掉」（`protocol_proxy.rs:786`）—— 那只在
   「既没登录过工作管家、又没有官方 codex 凭据」这个交集里成立。
   **另有一个放大器**：`CODEX_PLUS_HELPER_BIND`（`launcher.rs:459-465`）能把这个零鉴权监听面
   从 loopback 放宽到任意网卡，release 生效。

**另外**：release 构建下 `allow_external` 是**恒真**而非条件真 —— `main.rs:1612` 的
`active_dev_codex_paths().is_none() || app_owns_running_launcher(app)` 短路，配
`main.rs:2969-2972` 的 `#[cfg(not(debug_assertions))] fn active_dev_codex_paths() -> None`，
左操作数在出包构建里恒为 `true`，`app_owns_running_launcher`（`main.rs:1264-1274`）那半个条件
**永不被求值**。于是 `allow_external_helper()`（`main.rs:1550-1551`）恒真，
`engine_runtime.rs:336` 的 `HelperProbe::Ready if backend.allow_external_helper()`
会无条件收养端口上任何长得像 helper 的服务。

**修的方向**（按性价比排）：每次启动生成随机 token → health/WS/shutdown 三处都校验；
`ACAO` 去掉 `*` 改成显式白名单或直接不发；WS 握手校验 Origin（**不是**删掉它）。

> **定级说明**：按「网页 → 无审批 agent」这条链定 P0。**此结论待你复核**——
> 我没有实际写页面打一发验证过，是纯代码链路推断（三个 refuter 里两个认可、一个认为
> 「浏览器可能拒绝连 ws://localhost 混合内容」，但 WS 从 http 页面连 loopback 是允许的）。

---

# 第一层 · 确认的代码 bug，可立刻动手

## 1.1 前端看门狗漏了重新布防

`engine.ts` 里 `armWatchdog` 上方注释写明设计意图：

```
// 滚动看门狗：每来一帧重置。首帧前 15s（引擎无响应），首帧后 90s（流中途死掉）
```

但 `acceptFrame()`（`engine.ts:523-527`）只有 `clearTimeout(watchdog); watchdog = null;` ——
**没有重新布防**。`turn/started` 几毫秒就到，一到就把看门狗清了，之后永不再响。上游卡死时 UI 一直转圈。
（实际首帧看门狗是 25s，全前端唯一的布防点是 `engine.ts:1029` 的
`if (get().running && !receivedTurnFrame) armWatchdog(25000)`；`engine.test.ts:2435` 还有一条测试把
「有效帧撤销看门狗」钉成期望行为 —— 但它只 advance 25s 并断言 `status` 仍是 streaming，
**约束力被高估了**：新增一个「首帧后滚动布防、只失败当前轮不重启引擎」的看门狗（时长 > 25s）
不会让这条测试变红。）

**这句注释本身是活的误导源**：15s / 90s 两个数字都不对应任何代码。改动时一并修正。

**新增一条更要紧的**（原先没写）：`error` 事件在 `willRetry` 门**之前**就调用了 `acceptFrame()`
（`engine.ts:546` 在 `548` 的 `if (p.willRetry === true) return;` 之前）。
**于是「上游正在重试」这个最该盯着的场景，恰好把看门狗撤掉并把 turn 留在 streaming。**
外部审计实测到的现象（`claude-opus-4-6` 连续 503/520，收到 retry error 后 180 秒没有终止事件，
只能显式 interrupt）就是这条。

**不能简单补一行 `armWatchdog(90000)`**：

1. **会杀掉正常长命令** —— 90 秒意味着「90 秒内一帧都没有」，而 agent 跑耗时命令时
   `item/started` 到 `item/completed` 之间可能长时间无帧。
   **比这更根本**：`onEvent` 只白名单 9 种方法名，其余帧一律不算存活证据
   （`engine.test.ts:2152` 钉死），所以任何基于 `acceptFrame` 的滚动看门狗**天然会误杀长命令**，
   必须用 `stepByItem` 区分档位，否则一定回归。
2. **动作是错的** —— 现在 `watchdogFn` 是「重启引擎 + 报 `engine_unresponsive`」。上游卡死时
   本地引擎完全健康，重启没用又有害（丢 threadId），文案还把上游故障甩锅给用户机器。
3. ~~**90 秒比下游超时还短**~~ —— **数字对，机制说错了，见下。**

> **纠正**：原先写「relay 单次 120 秒，前端 90 秒会在 relay 第一次超时之前掐掉请求」。
> 120s 是 `UPSTREAM_STREAM_HEADER_TIMEOUT`（`protocol_proxy.rs:18`，经 `736-741` 的
> `response_header_timeout` 取用），**只包住 `request.send()` 等响应头**
> （`protocol_proxy.rs:338-347`，`tokio::time::timeout` 在 343）。而**「上游先返 200 + SSE 头、
> 再中途静默」这个最常见形态下，body 转发循环没有任何超时，relay 无限期等待** ——
> 三个循环全是裸 `while let Some(chunk) = bytes_stream.next().await`：
> `launcher.rs:3173-3180`（Responses wire）、`3199-3219`（chat→responses 转换）、
> `3331-3333`（chat/completions 直转）。`launcher.rs` 里唯一的 `tokio::time::timeout` 是
> app-server RPC 的 `read_app_server_rpc_response`（1803-1814）。**根本不存在「relay 第一次超时」
> 这个时间点。**
>
> **推论：3.1 想把 header 超时降到 45~60 秒，打不到这个靶。** 要治的是 body idle，不是 header。
> header 超时那条路径本来就能自愈（触发后 relay 返 502 → codex 收到错误 → 前端收到 error 帧 →
> `finishTurn` 正常收尾）。前端侧唯一像超时的 `engine.ts:318`（`turn/start` 180s / 其余 30s）
> 只管 RPC ack —— app-server 收下 turn 立刻 ack，之后的流它一秒都不看。
> **我方全链路对「流中途死掉」零超时，前端看门狗是唯一防线，而它不存在。**

**方案：拆成两个看门狗，两种动作**

- 看门狗 1（**保持不动**）：首帧前 25 秒 → 引擎真没响应 → 重启 + `engine_unresponsive`。有测试保护。
- 看门狗 2（新增）：首帧后滚动布防 → **只失败当前轮，不重启引擎**，新 failure kind
  （如 `stream_stalled`），文案指向上游；触发前先发 `turn/interrupt`。
- **`error` + `willRetry=true` 时必须重新布防**，不能只是 `acceptFrame` 了事。
- **长命令不误杀**：用现成的 `stepByItem` 区分「在等模型」（短）和「命令在跑」（长）。

改动位置：`acceptFrame()` / `watchdogFn` / `lib/turnFailure.ts` / `engine.test.ts`。

---

## 1.1b 「停止」按钮在首帧后没有任何本地保证

**和 1.1 同源，但补看门狗**不会**顺带修掉它，得单独改。**

`stop()`（`engine.ts:1037-1049`）在 `currentTurnId` 非空时只发一发 `turn/interrupt`，
且 `.catch(() => {})` 把失败全吞掉（1045）；本地兜底 `finishTurn('stopped')` 被 `if (currentTurnId)`
挡在 else 分支里（1046-1048），**首帧后结构性不可达**。

**即便 interrupt 的 RPC 完全成功**（ack 正常返回），只要卡死的 app-server 后续不发
`turn/cancelled` / `turn/completed`，这一轮就永不在本地终结 —— 1200ms 的 `stopTimer`
（`engine.ts:1044`）回调只有 `stopping = false; stopTimer = null`，**不碰 turn 状态**。

用户可以反复点「■ 停止」，每次只重发一帧 interrupt，转圈不停。

**连带后果比「转圈」严重**：`running` 永久为 true 会卡住 `app.ts:192/210/222/298` 四处 running
守卫（新建 / 切换 / 删除对话），同时 `phase` 仍为 `ready`，而「重启引擎」按钮的门是
`Composer.tsx:147` 的 `phase === 'failed' && engineFailure`（按钮在 151，`canRestart` 在 135-139），
于是按钮不出现 —— **整个应用变砖，只能杀进程。**

**修法**：`stop()` 发出 interrupt 后自带本地超时，N 秒内没等到终结帧就 `finishTurn('stopped')`。

---

## 1.2 `missing_output` 判据太弱，会把「什么都没产出」标成成功

**已证实，这条是最容易让用户白等的失败形态。**

判据（`engine.ts:480`）只看 `!t.reply.trim()`：

```
16:06  第2轮 reasoning 6099 字, 无正文 → reply 空   → 报 missing_output ✓
16:04  第1轮 有句 45 字开场白"好的，我先读取 skill 的说明"
       第2轮 reasoning 4936 字, 无正文 → reply 非空 → 判定为「成功」✗
```

**16:04 同样什么都没产出，却被标成成功。** 一句开场白就能骗过 `missing_output`。

准确说这里是两道闸串联，`missing_output` 只管第一道：`engine.ts:481-482`
`if (!emptyReply && !expectedFile) return {};` —— reply 非空时还得 `expectsFile` 为 false
才判成功。所以 16:04 能过关，靠的是下面那个 `files.length` 短路（`build_doc.py` 已落盘），
或 1.3 里 `wantsFile` 漏判导致的 `expectsFile=false`。**三个洞是串在一起的，只补一个没用。**

同时 `lib/turnFailure.ts:90` 的文案「可能是模型/网络波动」是误导 —— 实际稳定复现，重试没用。

**更早的一道短路口（原先没写，且它会让 1.2 的修复失效）**：
`engine.ts:475` 的 `if (files.length) return { files };` **位于 `missing_output` /
`missing_deliverable` 两条校验之前**（校验体在 476-493）。而 `detectDeliverables`
（`engine.ts:430-443`）只做 cwd 快照 diff 加两条黑名单，**分不清剩下的文件是交付物还是
中间产物**。于是产出目录里任何一个不在黑名单里的新文件都会让两条校验一起失效。

`db96b78` 只堵了其中一个入口：`isStagedAttachment`（`engine.ts:401-403`，调用点 `438`）把
应用替用户暂存的 `上传文件/` 排除掉，并有两条前端测试钉住「暂存文件不算产出、不掩盖
missing_deliverable」。**这解决的是「输入被当成产出」，不是「中间脚本被当成产出」。**
引擎里其实有 `aiWrites`（`collectAiWrites`，`engine.ts:422-428`）知道哪些文件是 AI 写的，
但 `detectDeliverables` 根本不查它 —— 而且查了也没用：`build_doc.py` 和 `.docx` 都是 AI 写的。

这不是理论风险 —— **六份 SKILL.md 至今仍主动指示模型把中间脚本写进产出目录**（`feea75a`
重写了 8 份 SKILL.md，这一条一个字没改）：

- `meeting-notes/SKILL.md:142` / `resume-tailor:147` / `short-video-script:146` /
  `weekly-report:127` —— 「把脚本写进产出目录再执行」+ `& $env:WA_PYTHON build_doc.py`
- `report-ppt/SKILL.md:106` —— 「先把脚本写进产出目录，再执行」+ `build_deck.py`（109）
- `excel-analysis/SKILL.md:121` —— `& $env:WA_PYTHON analyze.py`，相对路径即 cwd 即产出目录

`report-ppt` 还**变严重了**：基线 `943392f` 里落盘是二选一（先给 `& $env:WA_PYTHON -c "..."`
单行版，「复杂一点的」才写脚本），`feea75a` 把单行版删了，落盘成了唯一路径。唯一的反向缓解是
`5f4be33` 在 `report-ppt/SKILL.md:159` 加的一条反面清单「中间文件用完删掉，产出目录只留最终
交付物」—— 只有这一份有，是软约束，也不在 ASCII 硬规则段里。

`isSystemNoise`（`engine.ts:389-395`）依旧不滤 `.py`。所以「脚本写出来了、python-docx 调用
失败、没有 .docx」会被判成**成功**，并把 `build_doc.py` 当产出卡推给非技术用户
（`TurnView.tsx:230-233`，标签由 `lib/files.ts:8` 的兜底分支算成 `PY`）。
**这才是「没出文件却报完成」的真实机制，仍然完整成立。**

`7e50fb9` 只把**另一类** `.py` 引开了：读附件用的脚本现在被要求写进 `$env:TEMP`，提示词里明写
「**不要写进产出目录** —— 那里的新文件会被当成你这一轮的交付物」（`lib/attachments.ts:110-113`）。
**同一句话没有同步进任何一份 SKILL.md。**

修法：按用户意图校验交付物扩展名（ppt→`.pptx` / excel→`.xlsx` / report→`.docx`），
或把 skill 自己写的脚本从交付物候选里排除。

> **纠正第三条**：原先写「模型产出的 5000+ 字全在 reasoning 里被丢弃，**界面零显示**」——
> **后半句是错的。** `TurnView.tsx:206-211` 把 `turn.thinking` 渲染在可折叠的
> 「💭 思考过程」`<details>` 里（样式在 `styles.css:581-585`），基线 `6fbf544` 就有。
>
> 真正的缺陷是**不落盘**：`Thread.turns` 的 `Pick` 类型（`app.ts:18`）不含 `thinking`，
> 恢复时硬编码 `thinking: ''`（`app.ts:131`）。**实时能看，重开对话就没了。**

---

## 1.3 附件/技能一挂就要求出文件，纯问答被判「未完成」

**这是 !54 有意留下的那条，现已确认是 bug，且触发面比预想的大。**

之后 `d8fc6a4` / `7e50fb9` / `db96b78` 三个 commit 反复改 `Composer.tsx` 与 `attachments.ts`，
**一条都没碰这个耦合** —— `d8fc6a4` 的 commit message 自己写着「前缀保持非空，engine 侧
`expectsFile` 的推导与重试重放行为不变」。

`engine.ts:984`：`expectsFile: !!skillPrefix.trim() || wantsFile(t)`。而 `Composer.tsx:112`
（`if (staged.length) prefix += buildAttachmentPrefix(staged);`）**一挂附件就让 `skillPrefix` 非空**
—— 于是**任何带附件的轮次**都被标成「用户要求产出文件」。配合 `engine.ts:475-492`（`481` 行
`const expectedFile = !!t.expectsFile`、`484` 行落 `missing_deliverable`），这一轮若只是回答问题、
没写出文件，就被翻成硬失败「AI 只输出了文字，没有生成产出文件」（`turnFailure.ts:91`）。

更糟的是这条耦合已经被写成断言：`attachments.test.ts:343` 专门钉住「前缀必须保持非空，
否则挂了附件这一轮的产出校验行为会跟着变」—— **误报被当成契约保护起来了。**

**「拖个 PDF 进来问它讲了什么」是最自然的用法，而它必然被判失败。** `7e50fb9` 之后是**字面意义上
的必然**：`attachments.ts:80` 现在明确命令模型「如实告诉用户 PDF 暂时读不了……绝不要凭文件名编造
内容」—— 于是这一轮**保证**只有文字、零文件，而 `expectsFile` 恒为 true。`feea75a` 的 commit
message 自己记着「附件挂 PDF 仍会撞 `missing_deliverable` 误报」。

代码注释（`engine.ts:979-983`）自己承认了这块待解耦（原话：「附件是否真要求产出文件将在独立修复中
解耦，这里不能声称非空即来自场景卡」）。!54 加的 `Turn.skill`（`engine.ts:79`）与后来加的
`Turn.attachments`（`engine.ts:78`）都在 `978` 行随 `...intent` 一起落进 turn，本可用来区分
「选了技能」和「只挂了附件」，但 `984` 行**一个都没用**。

`db96b78` 反过来给这条误报上了保险：`detectDeliverables` 专门排除 `上传文件/`
（`isStagedAttachment`，`engine.ts:401-403`，调用点 `438`），注释理由是「否则会让
`files.length` 非零，把『该出文件却没出』的校验整条顶掉」。暂存进工作目录的附件本来能
误打误撞掩盖这个误报，而它被**刻意堵住了** —— 1.3 从「有时被掩盖」变成「稳定触发」。

**另一半：自由输入路径根本没有闸门。** 在 HEAD 上实测 `engine.ts:39-47` 的 `wantsFile`：
「帮我写周报」/「帮我写个周报」/「帮我写这周的周报」/「帮我做个周报」/「帮我写份周报文档」
**全部返回 false**。两道关都过不了：`FILE_ACTION_RE`（`engine.ts:36`）刻意不含裸「写」（只有「写成」），
而「周报」也不在 `FILE_TARGET_RE`（`engine.ts:35`，只认 pptx/xlsx/docx/pdf/csv/excel/word +
幻灯片/演示文稿/电子表格/文件/文档）里 —— 所以连含「做」的说法也栽在载体这一关。只有
「请帮我生成周报文档」才 true。**不点技能卡、直接打「帮我写周报」时 `expectsFile=false`，
AI 只回文字也判 done** —— 而这恰是最自然的说法。

两头都错：**选了技能/挂了附件就过度严格，自由输入则完全没有校验。**

---

## 1.4 首帧前点停止 → 幽灵任务，而且会劫持下一轮

**外部审计报的（WA-005），验证成立，但后果比它写的宽三层。**

窗口是 `engine.ts:970 → 1028`（`listDir`(1009) / `snapshot_workspace`(1011) / `thread/start`(1021)
/ `turn/start`(1024) 这段 await 链）：`running` 在 `970` 就置了 true 而 `currentTurnId` 仍为 null，
「■ 停止」**可点**，而 `stop()`（`1037`）走的是 `1046` 那条 `else if (get().running)
finishTurn('stopped')` 的**纯本地**分支 —— 不发 interrupt、不设任何 abort/generation 标志。
链上 `1005→1028` 没有一处检查 `running` 或代次（第一处 `get().running` 在 `1029`，已经在
`turn/start` **之后**），`thread/start` 和 `turn/start` **照样发出去**，引擎带着
`approvalPolicy:'never'` + `sandbox:'workspace-write'`（`1016-1017`）真正开跑、真正写文件。

> 澄清一点：真正的 preflight 段（`relay_status`/`ensure_launcher`/`validate_tier_model`，
> `876-943`）`running` 仍是 false，`Composer.tsx:246` 此时给的是「发送 ↑」不是「■ 停止」，
> 那一段用户点不到。**可点 stop 的窗口只是 970 行之后那段。**
>
> `db96b78` 的 `stage_attachments` 也没有拉长这个窗口：它的 await 在 `Composer.tsx:93-107`，
> 在 `await sendTurn(...)`（`115`）**之前**，此时 `running` 还是 false。

**审计说「后续事件因为 running=false 不会展示」—— 实际是更糟的「跨轮劫持」：**

1. **幽灵 threadId 被写进 store**（`1022` 行 `set({ threadId })`）。下一轮 `sendTurn` 在 `1014`
   看到 threadId 非空就**跳过 `thread/start`**，把新一轮塞进幽灵那个仍在执行的 codex 会话；
   同时 `952/956` 的 `!s.threadId` 判据失效，**前文重放与身份声明一并跳过**。
   `finishTurn`（`452-499`）自己不碰 `threadId` —— 它只清 `running`/`currentTurnId`/
   `activeLocalTurnId`（`468-470`），所以幽灵 threadId 一定会留在 store 里。
2. **事件劫持**：一旦用户开新一轮，`running` 恢复 true，幽灵迟到的 `turn/started` 会通过
   `532-539` 的**全部**校验（threadId 相同、`currentTurnId` 刚被 `965` 清空所以
   `(currentTurnId && …)` 为假），于是 `540` 行把**幽灵的 turn id 认作当前轮** ——
   之后幽灵的 delta/item 全部渲染进新一轮的气泡，幽灵的 `turn/completed` 直接终结新一轮，
   而新一轮自己真正的 `turn/started` 反被 `538` 拒掉。**这是跨轮内容错配。**
   F5 那道 `stopping` 拦截（`531`）在这里是空的：`stopping = true` 只在 `stop()` 的
   `currentTurnId` 非空分支里置位（`1042`），首帧前点停止走的是 `1046` 的 else 分支，
   **从来没进过拦截窗口**。
3. **副作用打到后续轮次**：`1012` 行 `patchLastTurn({ undoToken })` 用的是「最后一个 turn」
   （`patchLastTurn` 在 `336-345`，按 `ts.length - 1` 取，跟 turn id 无关），若用户 stop 后
   立刻重发，幽灵链残留的 `undoToken` 会盖到新一轮头上（**撤回会还原错的快照**）；
   幽灵链 `turn/start` 的 180s 超时（`318`）到点 reject 后落进 `1031` 的 catch，
   `finishTurn('failed', …)` 打的是当时的 `activeLocalTurnId`（`453`）——
   **把正在流式的新一轮标成 failed。**
4. **幽灵写出的文件不进任何轮的 `aiWrites`**，却会被下一轮的 `detectDeliverables` 当成自己的产出认领。

**测试现状**：全套 stop 测试（`engine.test.ts:2448`、`2472`）都是先 emit `turn/started` 再 stop，
**「首帧前点停止」这条路径零覆盖。**

---

## 1.5 运行中改产出目录 → 整个 App 变砖 + 撤回会删用户文件

**外部审计报的（WA-011），验证成立，且比它写的严重。**

任务运行中设置页可达，`setCwd`（`engine.ts:1110`）无守卫地 `set({ cwd: d, threadId: null })`。
此后 `onEvent` 里**每一个** handler 都因 `isCurrentCanonicalEvent` / `isCurrentTurnEnvelope`
（`engine.ts:501-517`，两者都要求 `!!get().threadId`）失败而 early-return ——
A 轮所有后续事件（含 `turn/completed`）被丢弃，`finishTurn` 永不执行，`running` 永久为 true。
**没有任何兜底会终结这一轮**：首帧看门狗只在 `!receivedTurnFrame` 时才 arm（`engine.ts:1029`），
收到首帧就被 `acceptFrame` 撤销（`engine.ts:526`），`elapsedTimer` 只管计秒。

**审计说「UI 可能永久保持 running」措辞偏轻 —— 是整个 App 对该 session 失能**：
发送被 `Composer.tsx:84` 挡死、编辑/重试/删除被 `TurnView.tsx:47` `guard()` 挡死、
当前对话不可删（`app.ts:238-240`）、历史对话只剩只读浏览、无法真正切回引擎
（`app.ts:222-225` 强制走 `browsing` 旁路，`loadTurns` 被 `engine.ts:1119` 的 running 守卫挡死），
**只能重启进程**。「点停止自救」也不成立：`stop()`（`engine.ts:1037-1049`）因 `currentTurnId` 非空
走 interrupt 分支，`threadId` 已 null 导致 `turn/interrupt` 无效且被 `1045` 行的 `.catch(() => {})`
静默吞掉，该分支不调 `finishTurn`（同 1.1b）。

**触发面比「改目录」大得多**：`SettingsView.tsx:41` 的 `saveDir` **没有 dirty 检查** ——
用户在设置页原样点一下「保存」（很常见的确认动作）就足以清掉 threadId。

**数据损失（审计漏了）**：`detectDeliverables` 只在 `finishTurn` 里调用（`engine.ts:472-473`，
全文唯一调用点），永不执行 → 即使 agent 已经把 PPT 写到旧目录，**产出卡片永不出现**；同时 `App.tsx:95`
在 `running` 恒 true、轮数不变时不再落盘，**那一轮的 reply 永久停在空串**。用户的活白干了。

**撤回会真删用户数据**：`undoTurn`（`engine.ts:1051-1057`）把 `get().cwd`（**实时值**）当 `path`
交给 `undo_workspace`，而不是快照时的 cwd —— `turn.requestEnvelope.cwd` 就在手边没被用。于是
`main.rs:2907-2919` 按旧目录的 manifest 把整份旧快照 copy **进新目录**（污染；`db96b78` 之后连
`上传文件/` 里替用户暂存的附件原件也一起灌进去，`snapshot_workspace`（`main.rs:2841`）备份的是整个目录），
`main.rs:2920-2931` 再 `remove_file` **新目录里与旧轮 AI 产出同名的用户文件** ——
R1 白名单只保证「名字对得上才删」，跨目录时这层保护恰好失效。
这条不依赖变砖场景：A 轮正常跑完 → 改目录 → 点「↩ 撤回本次修改」（`TurnView.tsx:59`，无 running 守卫）即触发。
对照 `activeRequestCwd()`（`engine.ts:445-450`）那种正确捕获 `requestEnvelope.cwd` 的写法 ——
**`undoTurn` 是漏改。**

---

## 1.6 `stopped` / `failed` 落盘被抹成 `done`，重开后主动断言成功

**外部审计报了两条（WA-021 头部徽标 / WA-018 历史重开），根因是同一个，合并在此。**

**A. 头部徽标缺分支**（`App.tsx:104-108`）：三分支映射 running → 「正在干活」、
`failed` → 「未完成」、**其余一律落到 else 的「已完成」**。而 `'stopped'` 是真实存在的状态
（`engine.ts:66`），**3 个调用点会设它**：`engine.ts:608`（`turn/completed` 带
`status: 'interrupted'`）、`engine.ts:635`（`turn/cancelled`）、`engine.ts:1047`（`stop()`）
—— 用户点「■ 停止」后头部立刻显示成功态配色的「已完成」。
!54（`96667c1`）只给 running 那支加了 `&& viewingLive`，没碰这条。

> 修的位置是徽标映射，**不是 `finishTurn`** —— `engine.ts:461-467` 把 `status` 原样写进 store，
> 内存里始终忠实地是 `'stopped'`，撒谎发生在三元表达式的 fallthrough。

**B. 落盘层把状态整体抹平**，且不止 stopped、**连 failed 也丢**：
`Thread.turns` 是 `Pick<Turn, 'userText'|'reply'|'files'|'attachments'|'skill'>`（`app.ts:18`），
**`status` 根本不落盘**；恢复时 `threadTurns` 硬编码 `status: 'done' as const`（`app.ts:132`）。

后果不是「看起来成功」，而是**主动断言成功**：

- 失败轮若写出过半成品文件，`files` 是落盘的，配上伪造的 done 会命中 `App.tsx:139`，
  界面**明写**「✓ 已完成。成果已保存，可在本对话或产出文件夹里找到」——
  **从中性徽标升级成肯定句谎言**，还正好把用户引去翻一个不完整的产物。
- **自助修复入口一起丢**：`failure` 没落盘（`persistTurns` 的 payload 见 `app.ts:274-280`）
  ⇒ `failureAction` 恒为 `'none'`（`TurnView.tsx:69`），重启引擎 / 重新绑定 Relay /
  刷新模型目录 / 切换档位（label 在 `TurnView.tsx:115-124`）全部消失，连失败块本身
  （`TurnView.tsx:265`，门是 `turn.status === 'failed'`）都不再渲染；而普通「↻ 重试」要求
  `turn.reply` 非空（`TurnView.tsx:215` 的 `{turn.reply && (`，按钮在 `TurnView.tsx:222`），
  **失败轮 reply 为空时整轮零可点操作**。
  对 relay 坏掉这类可一键自愈的故障，**重开一次会话等于永久失去修复入口。**
- 最坏组合：preflight 失败 / `missing_output` 那种 reply 为空的失败轮，重开后界面是
  「用户提问 + 空白 + 绿色已完成」。
- 影响面比 `openThread` 大：`threadTurns` **同时喂运行中的只读浏览快照**（`app.ts:224`），
  任务跑着时切过去看旧对话，历史失败同样被粉饰。

**修法**：补 `status` + `failure.safeMessage` 即可闭合。**不要**顺手落 `steps` ——
会撞上 `app.test.ts:228`、`app.test.ts:232` 钉住的「localStorage 里不得出现绝对路径」不变量。

**注意现有测试把错误行为钉死了，一共 5 条，修时必须一并改，否则会被判成回归**：

- `app.test.ts:234-238`、`1303-1309`、`1325-1329` —— 三条 `expect(persistedThreads()[0].turns[0]).toEqual({…})`
  精确形状断言，而 `makeTurn`（`app.test.ts:69`）造的轮次带 `status: 'done'`，payload 多一个字段就红。
- `app.test.ts:256`（`openThread` 恢复）、`app.test.ts:1518`（浏览快照）—— 直接把恢复出来的
  `status` 断言成 `'done'`。

---

## 1.7 撤回（Undo）：领头论断是过期的，真问题在别处

**外部审计把这条列为 P0（WA-002，七条子论断）。逐条验完：领头那条是过期的，但确实另有真问题。**

> **已过期，别再照它改**：子论断 2「Undo 把 `ai_files` 中不在 manifest 的文件当新文件删除」——
> 这个「黑名单删所有新增」的引擎**早在 `d2c41c3` 就反转成了 AI 写入白名单**，而 `d2c41c3`
> **是审计自己基线 `6fbf544` 的祖先**（`git merge-base --is-ancestor` 实测成立）。
> `main.rs:2902-2904` 注释写明「判据从黑名单反转成白名单，保证零误删」，并有两条单测
> （`main.rs:4015`/`4066`）。删除 AI 建的文件是 Undo 的**预期语义**。
>
> **子论断 6（token 无 containment 校验）代码描述准确但链路不闭合**：token 是 Rust 侧生成的
> 纳秒时间戳（`main.rs:2846-2852`，纯数字），**模型永远看不到也提供不了**；历史轮恢复时
> `undoToken: ''`（`app.ts:133`）。不构成可达路径。
>
> **子论断 3 范围比说的窄，但撞车后果比说的重**：只有 cwd **根目录**下恰好名为
> `__manifest.json` 的文件会撞车，嵌套的映射到 `dest/<subdir>/__manifest.json`，不受影响。
> 撞上时不是「备份丢了」而是**用户原文件被写坏**：`main.rs:2875-2878` 在拷完文件后才写
> manifest，覆盖掉刚备份的同名文件；而 manifest 数组里又含 `"__manifest.json"` 这一项，
> 还原循环 `main.rs:2907-2919` 会把那个 JSON 数组原样拷回 cwd 根目录。
>
> **子论断 4 只在同会话内成立**：历史与浏览恢复的轮次 `undoToken` 为空（`app.ts:133`），
> `undoTurn` 在 `engine.ts:1053` 就 early-return。不是跨会话隐患。

**真正的问题（审计没写或写反了）：**

1. **还原循环无条件覆盖用户的并发编辑**（`main.rs:2907-2919`）。用户在 turn 期间或之后对
   一个已存在文件做的任何修改，会被静默回退，**没有回收站、没有 diff、没有版本/冲突检查**。
   白名单反转保住了「删除」这一侧，**把这一侧完全敞开着**。这是真正不可恢复的那条路径。
   **而且还原的目标目录可以压根不是快照来的那个**：`undoTurn` 传的是 `get().cwd`
   （`engine.ts:1054`），不是本轮 `requestEnvelope.cwd`（`engine.ts:1007` 才是快照来源）；
   而 `setCwd`（`engine.ts:1109-1115`）换目录时**不清 `turns`**。于是「在 A 目录跑一轮 →
   换产出目录到 B → 撤回那一轮」会把 A 的快照**倒进 B**，同时按 aiset 在 B 里删文件。
2. **子论断 1 和 2 复合出一条审计从未陈述的路径**：一个因 >50MiB（`main.rs:2864-2866` 静默
   `continue`）**或复制失败**（`main.rs:2871-2873` 只在 `copy(..).is_ok()` 时才 push 进
   manifest，失败无声 —— Word/Excel 持有 Windows 共享锁，对这个用户群是**日常**）而被跳过
   manifest 的既存文件，在 AI 写过它之后就同时「不在 mset」且「在 aiset」，于是
   `main.rs:2928` **把用户的原件直接删掉**，而不是还原它。
   **删除代替还原，比还原失败严格更糟。**
   眼下这条在 Windows 上被下面第 3 条的空集掩盖（aiset 恒空 ⇒ 删除分支不触发），
   **修好 3 的当天它就变成可达路径 —— 两条必须一起改。**
3. **Windows 上撤回按钮其实是空操作**（审计跑在 macOS，完全没看到）：
   `engine.ts:407-419` 的 `toRel` 用 `cwd + '/'` 拼 base（`engine.ts:413`），而 `cwd` 来自
   `default_out_dir_path` 的 `PathBuf`（`main.rs:2181-2189`）经 `to_string_lossy`
   返回（`main.rs:2220`），Windows 上是 `C:\Users\<u>\Documents\工作管家` 这种**反斜杠**形式，
   base 于是成了 `C:\Users\<u>\Documents\工作管家/`。逐字复制 `toRel` 实测：

   | 传入 | 返回 |
   |---|---|
   | `C:\...\工作管家\out.docx` | `null` |
   | `C:/.../工作管家/out.docx` | `null` |
   | `C:\...\工作管家\templates\a.md` | `null` |

   **两种一致的绝对形式都返回 null**（只有 `C:\...\工作管家/out.docx` 这种混合分隔符的畸形
   路径才碰巧能穿过）。相对路径能穿过，但嵌套的对不上 Rust：aiset 里是 `templates/a.md`，
   而 `main.rs:2924-2927` 的 `strip_prefix(..).to_string_lossy()` 在 Windows 上给的是
   `templates\a.md`，`aiset.contains(&rel)` 恒 false。
   **净效果：`aiWrittenFiles` 在 Windows 上至多只能装进模型以相对路径报出的根目录裸文件名**，
   删除分支几乎不触发，撤回留下一堆孤儿 AI 产出却仍报成功。`d2c41c3` 宣称的「数学上零误删」
   在 Windows 上是**空集平凡成立**，不是白名单机制在起作用。
4. **前端谎报「已撤回」**：`engine.ts:1055` 无条件写 `undone:true` + `files:[]`，
   **不看 `undo_workspace` 返回的 `restored`/`deleted` 计数** —— `engine.ts:1054` 明明把它
   接进了 `r`，下一行 `return void r` 直接扔掉。`TurnView.tsx:56-66` 随即弹
   「已撤回本次修改」toast。而 .pptx 还在盘上、**产出卡片（用户在应用内唯一的定位入口）被抹掉**
   —— 文件变成用户在 App 里找不到的孤儿。而且 `undoToken` 只要 snapshot 成功就非空
   （`engine.ts:1011-1012`；`snapshot_workspace` 只要 cwd 是目录就必发 token），
   所以撤回条在**每个**有产出的轮次都出现（`TurnView.tsx:250`，嵌在 `TurnView.tsx:230` 的
   `turn.files.length > 0` 块里），误导面是 100% 覆盖。
   **附带一条：`TurnView.tsx:258-260` 那条「已撤回本次修改」常驻条是死代码** —— 它和撤回按钮
   同在 `files.length > 0` 块内，而撤回刚把 `files` 清空，父块整体消失。用户看到的是产出卡片
   连着撤回条一起凭空不见，界面上不留任何痕迹说明刚发生了什么。
5. **撤回缓存永不清理**：`undo_cache_root` 只在 `main.rs:2790/2853/2893` 出现，无启动清扫；
   全仓唯一的删除点是 `main.rs:2932`，**只在用户真去点撤回时才跑** —— 也就是说
   **每一轮没被撤回的任务都永久漏一个产出目录完整副本**。本机现有 **15** 个陈旧快照目录
   （最早 7-27，最新 7-30），单快照上限是 5000 文件（`main.rs:2803-2804`）× 每文件 50MiB
   （`main.rs:2864`），**无上限增长**。
   两处放大：`undo_cache_root` 在 Windows 上照抄 macOS 的 `Library/Caches` 布局，缓存落在
   `%USERPROFILE%\Library\Caches\com.xiapai.workingagent\undo\` —— 不是 `%LOCALAPPDATA%`，
   没有任何系统清理工具会认领它；而 `db96b78` 之后附件在 `Composer.tsx:92-106` 就被复制进
   `<产出目录>/上传文件/`，**发生在 `snapshot_workspace`（`engine.ts:1011`）之前**，
   于是每份上传文件都会再被复制进快照一份。

---

# 改过的四块 · 改前必读的不变量（!54 = `96667c1`，已在 main）

三个用户报障（F1/F2/F3）的根因同一类：状态只描述「发给模型的字符串」和「引擎在跑什么」，
不描述「用户选了什么」和「用户在看什么」。**已修、已 merge、也已经在桌面那个 `6edbe43` 的包里。**
下面这张表留着不是记功，是**动这四块之前必须先读的不变量清单** —— !55/!56 没有推翻其中任何一条。

| 报障 | 改动落点 | 关键不变量（改这块前先读） |
|---|---|---|
| 附件发送后从对话流消失 | `Turn.attachments` 贯穿渲染/落盘/重试（`engine.ts:833-837`） | `requestEnvelope` 一律不落盘（含内部拼装提示词与绝对路径），`app.ts:269-278` + `app.test.ts:226`/`app.test.ts:1347` 钉住。**db96b78 之后 `Turn.attachments` 存的是 staged 路径**（`Composer.tsx:92-104` 先 invoke `stage_attachments`，落到 `<产出目录>/上传文件/`），不是用户选的原路径；`engine.ts:397-398` + `engine.ts:430-443` 的 `detectDeliverables` 必须继续排除 `上传文件/`，否则用户的输入会被算成本轮交付物、把「该出文件却没出」的校验整条顶掉 |
| 选技能后同样消失 | `Turn.skill`，同上链路 | `regenerate` **不再** `useAttach.clear()`；但 `deleteFrom` 仍然清 —— 删除是丢弃，重试是复用，这个不对称是有意的 |
| 两处技能文案不一致 | `ActiveSkill.id` + `activeSkillFromScene`/`activeSkillFromSkill` 成为唯一构造入口 | 名字与图标一律取自 `SKILL_LIB`；`Scene.skillId` 由可选收紧为必填，**4 张没有对应技能的场景卡已删除**（14 → 10 张），再加一张没技能的卡会直接编译失败 |
| 任务进行中无法切换/新建对话 | `useApp.browsing` 只读旁路 | **运行中绝不调 `loadTurns`** —— 换掉 `engine.turns` 会让正在跑的那轮 delta 灌进被浏览的历史对话，且 `persistTurns` 会把结果写进错误的记录，那是**数据损坏**不是显示错误。`App.tsx` 因此拆成 `engineTurns`（落盘唯一来源）与 `viewTurns`（界面显示） |

**重试历史对话的消息时不带意图**（`replayable` 门禁）：`skillPrefix` 从不落盘，恢复出来的轮次
没有 `requestEnvelope`，附件读取前缀重建不出来。若仍标上附件 chip，界面会显示「带着这两个文件
重跑」而实际 prompt 里一个路径都没有。**宁可不显示，不能显示假的。**

**仍然没写测试的两处快照路径**（!55/!56 都没碰）：
`browsing.turns` 是点击那刻建的快照（`app.ts:224`），浏览期间原线程被改写后它就陈旧 ——
判断是不可达（`app.ts:402-406`：`running` 一落地就收回旁路），但**没测试钉死**；
引擎断连时的收回也没覆盖：`__waEngineClosed`（`engine.ts:762-779`）只靠第 775 行的
`finishTurn('failed')` 间接翻 `running`，而 `app.test.ts` 里一次都没触发过它。

**`expectsFile` 判据当时有意没动 —— 那条已升级为确认的 bug，见 1.3。**
**真正的多对话并行**（turns 按 thread 分区 + 引擎侧多 codex 会话）仍是独立重构，别混进 bug 修复。

## 本轮的验证方式（下次照做）

工作流跑对抗审查**两次都因 402 全挂**（`Budget pool quota has been exhausted`），却返回
`"nothing survived refutation"` —— **假绿灯，一个审查者都没起来**，`attacked: 0` 是唯一破绽。

> **子 agent 的预算池与主会话额度不是同一个。** 看到 `agents_error > 0` 时，工作流的结论一律作废。
> 但工具本身有效：!56（`5019d1c`）就是对抗审查跑通后改出来的，它在第一版附件读法里抓出 4 个真问题，
> 包括我自己那次「中文读取正常」的假阳性自测。**是额度事故，不是这套审查没用。**

替代做法（有效，已固化）：**逐条回退实现，确认测试真的失败**。本轮八处回退全部被抓住，过程中
抓到自己三个问题：`App.tsx` 此前零覆盖；「命门」测试因 `thRUN` 不在 `threads` 里而形同虚设
（`persistTurns` 走 `map` 分支匹配不到、什么都不写，拿哪份 turns 落盘都一样）；旁路自动收回只用
`setState` 验过，已补走完整 `sendTurn → turn/completed → finishTurn` 的测试（`app.test.ts:1568`）。
还有一处测试本身没用：「同路径附件」回退后照样通过 —— React 重复 key 在静态首渲染只告警不丢项，
改成断言「不出现重复 key 告警」才吃得住（`TurnView.test.tsx:446`）。

**jsdom 坑**：`Element.scrollTo` 未实现，测 `App.tsx` 必须打桩（`App.test.tsx:74-77`）。
这也说明**滚动行为在单测里是假的**，手感只能真机验。

---

# 第二层 · 可观测性，改动极小但决定以后能不能查

这一层不修任何 bug，但前几次诊断的时间基本都花在「日志里没有那个字段」上。

## 2.1 `dbg_log` 不记 pid（一行）

`main.rs:51-61`：`writeln!(f, "{} {msg}", utc_timestamp())` —— 所有实例写同一个固定路径的文件，
**谁写的分不出来**。这直接导致「对话被批量删除」的真因至今无法定性：日志能证明「有第二次页面加载
且第一个没退出」，但证不出是两个进程还是一次 webview 重载。

加上 `pid={}` 之后同样的现象一眼可判。

## ~~2.2 relay 不记 tools~~ —— 已作废，改记 model

`protocol_proxy.rs:520` 那处 `upstream_request` 诊断日志**一个 body 相关字段都没有**
（`body_bytes` 属于另一个事件 `helper.request`，在 `launcher.rs:921`，原先这里把两处日志混为一谈）。

**原计划「加转换前后的 tools count/names/types」已作废** —— 4.1 已排除转换层，这条遥测跑完只会
得到「两边都正常」。

**真正缺的字段是 `model`**：payload 里有 `relayId`/`endpoint`/`wireApi`，**没有实际发给上游的
model id** —— 整份 dbg log 里 `"model":` 出现 **0** 次。而「档位与实际模型不符」和
「opus-4-8 上游超时」两条都卡在这个字段上，3.1 那簇 502 至今只能靠「当时挂在优质档」间接归因。加它。

## 2.3 relay 不记 finish_reason / 输出 token 数

P0-D 的定案花了半天，就因为这两个字段没记。而真凶恰恰是 `finish_reason` 恒为 null ——
relay 只要记一笔「上游未提供 finish_reason」，第一眼就能指向它。

**而且现在的日志是主动错的，不只是缺**：`launcher.rs:3197-3233` 在流中途上游出错时会设
`stream_failed=true`，然后**无条件**发出 `helper.protocol_proxy_stream_ok`，status 写 `"200 OK"`。
**一次被截断/中断的生成，在磁盘上被记成成功的 200。** 任何基于这份日志的事后分析都会得出
「请求成功了」的结论。这条比「少记一个字段」严重，优先级应等同 2.1。

## 2.4 没有单实例保护

`working-agent` **完全没有单实例保护**。

> **纠正机制描述**：不是「三个二进制里唯一没引这个插件的」—— 全仓库**没有任何二进制**依赖
> `tauri-plugin-single-instance`（`rg tauri-plugin` 扫所有 `Cargo.toml` 只有 `dialog` 和
> `clipboard-manager`）。manager 与 launcher 各自手搓了一个同名函数 `acquire_single_instance_guard`，
> 底层都是 `codex_plus_core::ports::LoopbackPortGuard`（占一个环回端口当锁，带文件锁 fallback）：
> - manager：`apps/codex-plus-manager/src-tauri/src/lib.rs:236` 抢 `MANAGER_GUARD_PORT`，
>   调用点在 `lib.rs:23`，`AddrInUse` 时记 `manager.already_running` 后直接 return。
> - launcher：`apps/codex-plus-launcher/src/main.rs:61-95` 抢 `debug_port`，还带 stale 实例回收重试。
>
> `working-agent` 侧 `LoopbackPortGuard` **零引用** —— 既没有插件，也没有自建守卫。
> **2.4 不是要从零发明单实例，是要把已有的 guard 接到工作管家上。**

> **纠正两条后果**：原先写「争同一份 config.toml 和同一个 launcher 端口」，**两条都不成立**：
> - **config.toml 有跨进程锁**：`working_agent_relay_tx.rs:428` 取 fs2 `lock_exclusive()`
>   （Windows 上是 LockFileEx），三个启动期写入者（`ensure_provider` / `ensure_windows_sandbox` /
>   `ensure_office_runtime_env`）加 `RelayProcessLease` 全部走它，并发实例会串行化。
> - **端口不会争**：`engine_runtime.rs:335-338` 见到 57321 上已有健康 helper 就返回
>   `LifecycleReady::External`，实例 2 **收养**而不是去 bind，不产生 AddrInUse。

**真正的后果是另一个，而且更糟**：实例 2 收养了实例 1 的 helper，但只有实例 1 持有 owned child。
**实例 1 退出时会跑 `LifecycleIntent::Stop`（`main.rs:1772`，退出钩子在 `main.rs:3497` 调
`cleanup_spawned_engine`）把共享的 helper 杀掉** —— `reconcile_lifecycle` 在
`engine_runtime.rs:315-327` 先无条件 `shutdown_owned`，**之后**才在 `:331` 为 `Stop` 早退；
收尾是 `main.rs:1540-1547` 的 1500ms 超时 + `force_terminate_tree`。
实例 2 走的是 `External` 分支、`owned` 为 `None`，对此毫不知情：它的引擎无故死亡，
而现场只留下实例 1 的一条 `owned_child_stopped`，**没有任何跨实例归因**。

更糟的是 **launcher 自己的单实例保护在我们走的这条路上不可达**：
`--helper-only` 在 `apps/codex-plus-launcher/src/main.rs:41-46` 就 return 了，
而 `acquire_single_instance_guard` 在 `:48`。**两层保护都没生效。**
配合 2.1（无 pid）两个实例的事件混在同一个 log 里无标签，这个 bug 几乎无法诊断。

另：`dbg_log` **无大小上限、无轮转**，文件会随安装寿命无限增长。

---

# 第三层 · 上游问题，我们只能改「怎么失败」

## 3.1 优质档 `claude-opus-4-8` 上游持续不可用（观测截止 07-29，之后未复测）

```
07-28 10:00:10Z / 10:02:10Z / 10:04:10Z / 10:06:11Z  upstream_request_failed
                ERR=上游请求超过 120 秒未返回响应头  → 502 Bad Gateway
07-29 03:59:03Z  隔天再探, 同上
```

本机 `~/.codex-session-delete/codex-plus.log` 里这类超时一共 6 条：07-27 13:36:48Z 一条、
07-28 那 4 条连成一簇（间隔 120.2 / 120.5 / 120.8 秒）、07-29 03:59:03Z 一条；
对应 5 条 `helper.protocol_proxy_failed`，status 全是 `502 Bad Gateway`。**不是抖动。**

**但这份日志的最后一条记录是 2026-07-30 09:47:21Z，此后零新增 —— 07-30 之后一次都没复测。**
上面的结论只覆盖 07-27~07-29 这三天，到今天已空档 8 天，要引用现状先复测。

**还有一个取证缺口**：`upstream_request` / `upstream_request_failed`
（`protocol_proxy.rs:520-532` / `:550-564`）的 detail **没有 model 字段**。
**把这簇 502 归到 `claude-opus-4-8` 头上靠的是「当时挂在优质档」，日志本身证不了**
—— 要区分「这个模型死了」和「这条路由当时死了」，先做 2.2（把 model 记进去）。

而 `/v1/models` 照样把它列为可用，**目录在骗我们，而我们无条件信它**。

我们的责任三条，**在 HEAD（`6edbe43`）上一条都没动**：

- **档位指向死模型却无存活校验**：`skills.ts:118` 的 `TIER_MODEL` 是硬编码常量，换档只是
  `setTier` → `setModel(TIER_MODEL[t])`（`app.ts:295-305`）。唯一的校验是 `validate_tier_model`
  （`main.rs:3329-3389` → `model_catalog.rs:86-151`），它只判「model 在 `/v1/models` 的 id 集合里」
  （`models.contains(model)`，`:138`/`:148`），**不发任何试探请求 —— 只证明目录里有这个名字，
  不证明它还能出活**。这就是上面「目录在骗我们」的代码位置，且它早在 `9ebe5e2`（07-24）就在了，
  别把它当已修。
- **UI 无限转圈**（=1.1）：看门狗全前端只有一处布防 —— `engine.ts:1029` 的
  `if (get().running && !receivedTurnFrame) armWatchdog(25000)`；而 `acceptFrame()`
  （`engine.ts:523-527`）一收到 `turn/started` 就把它 clear 掉。**首帧之后整轮没有任何时间上限**，
  上游卡 8 分钟期间前端只会一直转。
- **8 分钟的来源**：`protocol_proxy.rs:18` `UPSTREAM_STREAM_HEADER_TIMEOUT = 120s`
  （非流式是 `:17` 的 30s），乘 codex 自己的重试。严格说日志里是 **4 次尝试**（首发 + 3 次重试），
  4 × 120 秒 = 8 分钟；原文「重试 4 次」把首发漏算了。

## 3.2 极速档 `step-3.7-flash` 长任务零正文

**7-29 定案，上游行为。** 走 relay 的原样转发通道直连上游：

```
上游 delta 字段 : role×1404, content×1404, reasoning×1403, reasoning_content×1403
finish_reason  : 整个流一次都没出现
content        : 每块都在, 但恒为空字符串
reasoning_content: 5401 字, 结尾断在句子中间
最后一块        : delta{reasoning_content:"、"} → data: [DONE]
```

109 token 输入、明确要求「直接写正文不要提纲」、不带工具，复现两次：输出 3100~3794 tokens，
**正文 0 字**，`responseStatus: "completed"`、`incomplete_details: null`。

我们的 relay 把 `reasoning_content` 路由成 reasoning 项是**正确处理**；它也无法识别截断，因为
`protocol_proxy.rs:271/1448` 的 `finish_reason == "length"` 分支永远不触发。

**我方缺陷见 1.2。**

## 3.3 上游成簇 503 / 500（同事机器，已恢复）

按小时统计 `upstream_response` 状态码：

```
07-29 04h  总  2  ok=  2   失败率   0%
07-29 06h  总 45  ok=  0  503= 45   失败率 100%
07-29 07h  总 45  ok=  0  503= 45   失败率 100%
07-29 08h  总150  ok=  0  503=150   失败率 100%
07-29 09h  总 37  ok= 37   失败率   0%   ← 恢复, 37 次全部 stream_ok
```

本地时间 **14:08–16:59 上游 100% 挂，17:00 后完全恢复**。07-28 05h 还有一段 60 次连续 500。

**「纯文字 / 读文件 / 用技能」三种同时失败**，因为它们都走同一个模型调用，与任务类型无关。

**我方的部分（两条，都还开着）**：

- **重试放大**：240 个 503 只对应约 **4 次真实操作**（按间隔 >30s 分簇），被 codex 重试 × relay
  重试放大了六十倍。本机日志有同形状的铁证：`codex-plus.log` 在
  **2026-07-29 08:03:16Z–08:03:52Z 的 36 秒内打出 16 条 `helper.protocol_proxy_upstream_error`
  / `503 Service Unavailable`**（全程 200×146、503×17），**一次操作扇出 16 次上游请求**。
- **文案不是「笼统」，是甩锅给用户** —— 原文这句说轻了。codex 0.142.5 把 503 包成
  `httpConnectionFailed: { httpStatusCode: 503 }`（`turnFailure.ts:263-264` 的注释就是为它写的），
  而 `:110` 把 `httpConnectionFailed` 映射成 `network`，用户看到的是
  **「网络连接中断，请检查网络后重试。」**（`:82`）—— **上游宕机被说成用户网络故障**。
  只有 `serverOverloaded` / `internalServerError`（`:107-108`）才落到 `server` →
  「服务暂时不可用，请稍后重试。」（`:83`）。
  更刺眼的是 `codexHttpStatus`（`:268-279`）**已经把 503 解出来并挂在 failure 上**
  （`:208` 取值、`:212` 写入 `httpStatus`），但全前端除 `turnFailure.ts` 自己之外
  **没有任何一处读 `httpStatus`** —— 状态码拿到了，选文案时不用。

**这次留下的方法论**（值得记住）：
> 「所有任务类型同时失败」= 优先怀疑上游。先按小时统计 `upstream_response` 的状态码分布，
> 一眼就能看出是成簇（上游宕机）还是长期（配置/代码问题）。

---

# 第四层 · 取证进行中

## 4.1 标准档 `claude-opus-4-6` 零 function_call —— 已定位到上游模型行为

**症状确定且稳定**：真实轮次**五轮全部 fc=0**。模型甚至直接对用户说过「我没有可用的写文件工具」。

**但用探针复现不出来**。原样抄出来的 base_instructions（21046 字）+ developer（10206 字）+ user
消息，配 codex 风格工具集打到本地 relay，**每次都正常 function_call**。已排除：工具数量/体积
（4→49 个、35KB→108KB 全通）、`type:"custom"` lark 工具、`local_shell`、`web_search`、
`include`、`tool_choice`、模型路由、relay 的 tools 转换本身。

### 「未知 ~31KB 工具集」已核销，原假设已推翻

原先这里写着「真实失败请求 `body_bytes=66253`，忠实复现只有 34771 字节，**缺的 ~31KB 工具集是
什么，猜不出来**」，并给了「约 75% 在 `responses_to_chat_completions` 里被静默丢掉或改坏」的置信度
和一棵判定树。**这三样全部作废：**

- **那 31KB 是常量，不是变量。** 它是 **30464 字节的标准 codex 工具集**，在 11 个会话里横跨两天、
  三个模型、多次重启都稳定到 ±1 字节（说明 MCP/plugin 动态注入量为 0）。
- **决定性反证**：同一份 tools 字节在**成功产生 `fc=19` 的请求里一字不差地同样存在**。
- **转换层与 model 无关**：`responses_tools_to_chat_tools`（`protocol_proxy.rs:2418`）的签名只吃
  `tools` 和 `CodexToolContext`，**根本拿不到 model**。整个 `responses_to_chat_completions` 里唯一
  按模型分支的地方是 `:184` 的 `apply_chat_reasoning_options`，而它对 claude 是空操作 ——
  `infer_chat_reasoning_style`（`:3905-3935`）没有 claude 分支，落 `Default`；
  `supports_reasoning_effort`（`:3972-3981`）只对 o 系列 / `gpt-5+` / DeepSeek / LowHigh 返回 true，
  claude 一律 false。**两个 opus 档位走完转换层，发出去的字节逐字相同。** 受控对照里两个请求
  相差 **1 字节（模型名）**，结果一个 fc=0 一个 fc=1。
- **因此 2.2 遥测按原设计（只记转换前后条数）跑完只会得到「两边都正常」，白烧一轮**；那棵判定树
  的三个分支都预设「工具数会因模型而异」，问不出东西。

**真正的差异变量已收窄到只剩模型本身。** 可以直接跳过遥测去做 tier 重映射。

### 下一条更有希望的线（原先完全没提）

11:57 那轮的 assistant **正文里裸着 `<thinking>` 标签**（content 以 `<thinking>` 开头，内含完整
推理后才是正文）。**这条已经不是推测，代码里能直接读出来**：relay 有一整套 inline think 切分器，
但标签是硬编码的 —— `protocol_proxy.rs:19-20` 只有 `THINK_OPEN_TAG = "<think>"` /
`THINK_CLOSE_TAG = "</think>"`。流式路径的 `leading_think_prefix_decision`（`:1705-1717`）对
`<thinking>` 两个条件都不满足：`trimmed.starts_with("<think>")` 为假（第 7 个字符是 `i` 不是 `>`），
`THINK_OPEN_TAG.starts_with(trimmed)` 也为假，于是直接返回 `Text`（`:1716`），buffer 原样吐进文本
通道（`:1153-1155`）。非流式路径的 `split_leading_think_block`（`:3144-3158`，调用点 `:2894` /
`:2907`）同样只认 `<think>`。**Anthropic 风格的 `<thinking>` 100% 走文本通道，一个字都进不了
reasoning。** 这套切分器自 `8067354` 初始导入起就是这样，基线之后无人动过。
这与 1.2 记的「reasoning 被丢弃」是**方向相反的另一个 bug**，且只在 opus 上出现。
**如果模型的工具调用意图也混在这段没被解析的 thinking 里，那 fc=0 就可能不是模型不想调，
而是调用被当成正文吃掉了。**

### 仍值得做的加固（但别当修 4.1 做）

`protocol_proxy.rs:2449` 的 `_ => {}` 配 190 行的 `if !converted.is_empty()`，意味着将来 codex
升级引入任何新 tool type，会导致 tools + tool_choice + parallel_tool_calls **整体消失，无错误
无日志**，症状与 4.1 一模一样。加「未知 tool type 必须报错」的回归测试之前，**得先改掉
`tests/protocol_proxy.rs:529` 那条把静默丢弃钉成期望行为的测试**，否则两者直接冲突。

`/v1/models` 的 `supported_endpoint_types` 那条**无法用代码验证** —— 全仓库 grep 不到这个字符串，
它是上游响应里的字段，日志只记了 200 OK 没存 body。既然转换层已被排除，验它的动机也消失了。

## 4.2 「删一个同标题的对话，全部消失」真因

**已确证**：删除从来不是按标题的（全前端唯一涉及 title 的比较是 `HistoryPanel.tsx:194` 的
`match.field === 'title'`，比的是搜索匹配的字段名）。id 撞车那条路径已在 !53 修掉，两层双保险：
`newThreadId`（`app.ts:105`）加随机后缀，`deleteThread`（`app.ts:252-255`）改成 `findIndex` +
`splice` 的单数删除；可执行测试在 `app.test.ts:1175-1192`（同 id 只带走一条）和 `:1194`
（同标题不同 id 互不影响）。

**但单实例下走不通** —— 两次独立分析一致：`newChat`（`app.ts:189`）在 `:196` 就 `loadTurns([])`
清空 turns，而 `persistTurns`（`app.ts:272`）开头 `:273` 是 `if (!turns.length) return`，
所以同一毫秒双创建不可达。

**另一条候选未证实也未排除**：多实例共享 localStorage 的全量覆写。`loadThreads()`（`app.ts:66`）
只在 store 创建时（`:172`）跑一次，之后整个数组待在内存里；`saveThreads`（`app.ts:93-100`）一律
`jsonSet(THREADS_KEY, list.slice(0, MAX_THREADS))` 整体写回。全前端没有一处 `storage` 事件监听。

**取证前提是 2.1（pid 日志）+ 2.4（单实例守卫）。**

---

# 第五层 · 已知、影响有限

外部审计报的四条（验证成立但影响有限）：

- **技能首次装配失败后永不重试，且技能库会全变关闭**（审计 WA-016）。与下面 P1 那条
  **共用同一个 marker，但是两个不同的问题**。四条子命题里**三条仍成立**，只有「修不了升级刷新」
  那条被 !55 绕开了。
  **marker 写在 `ensure_skill_marketplace` / `list_skills` 之前**（`app.ts:368` vs `371-372`）——
  这两个 invoke 里任一抛错，外层 catch（`app.ts:394`）吞掉异常，**bulk 从未被尝试过，但 marker
  已烧**；下次启动 `claim_p0_skill_initialization` 返回 false，`enable_all_p0_skills` 永不重跑。
  *原文写「首启时引擎短暂不可用、`App.tsx:60` 与 `connect()` 并发」是错的*：这两条命令走
  `run_codex_plugin`（`main.rs:413-435`）直接 spawn codex CLI，不经过 WS 引擎。真正会让它们抛错的
  只有两处：`resolve_marketplace_path` 返回 None（`main.rs:369-382`，随包 `skills-marketplace`
  在安装目录里解析不到）和 manifest 读/解析失败（`main.rs:564-567`）—— `marketplace add` 自己的
  失败只记日志（`main.rs:544`），`plugin list` 的失败被 `unwrap_or_else` 吞掉（`main.rs:575-577`）。
  而且**不需要任何抛错也能触发**：`main.rs:711-714` 把 8 次 `codex plugin add` 的错误全 `let _ =`
  丢弃，函数照样返回 `Ok(8)`（`main.rs:735`）。Windows 上 Defender 拦子进程 / 文件占用 / 30s 超时
  （`main.rs:366`）导致 add 全失败时，`patch_p0_skill_sections_at`（`main.rs:717`）照样把 8 个
  `enabled = true` 段写进 config.toml，于是状态变成「config 说启用、cache 是空的」。
  **marker 的语义本身是错的，且一个字没改**：`main.rs:641` 记的仍是「有人曾经尝试过」，
  既没有成功位，也没有版本位 —— 所以既修不了重试，也修不了升级刷新。
  *可自愈*：技能库复选框和场景卡按需启用仍可用（`explicitlyDisabledSkills` 仍为空），
  `SKILL_LIB`（`skills.ts:38-45`）8 项每项都带 `pluginName`。
- **技能同步的失败被静默吞掉，而且会把错误事实写进 localStorage**（审计 WA-023）。
  前端 4 个吞点（`app.ts:369` / `378` / `380` / `394`）+ Rust 侧 3 个「把失败伪装成成功」的吞点
  （`main.rs:561` / `575-579` / `617`），共 7 处；`syncSkillsFromEngine`（`app.ts:362-396`）整条链路
  一次 `showToast` 都没有。最恶劣的一环审计没抓到：**`main.rs:575-577` 的
  `unwrap_or_else(|_| "{}")` 把「查询失败」翻译成「确定全部未启用」**，前端据此不抛错，
  `app.ts:392` 把假状态 `jsonSet` 进 localStorage，**覆盖掉用户原本正常的 8 项启用记录**。
  另一条分支（`app.ts:394` 外层 catch）方向相反：`enabledSkills` 留 null 而 `isSkillEnabled`
  在 null 时返回 true（`app.ts:307-310`），技能库 8 张卡全打勾但 codex 侧一个 plugin 没装 ——
  **假阳性比全灰更难发现**。
  *审计说错的*：「no fix affordance」不对，`toggleSkill`（`app.ts:316-360`，失败回滚 + toast 在
  `343-359`）是可用的手动重试路径。缺的是**指引**，不是入口。
- **app-server 主动请求无人应答**（审计 WA-017）。`onRawMessage` 只在 id 命中本地 pending 时当
  响应处理，否则凡有 method 就丢给 `onEvent`；而 `onEvent` 只认固定几个通知名，**全代码库不存在
  构造 `{jsonrpc,id,result}` 的路径**（`sendFrame` 唯一调用方是 `rpc()`，只发请求）。
  审计只列了 `requestUserInput` / MCP elicitation 两族，**实际还有 `currentTime/read` 和
  `item/tool/call` 两族同样无应答**，且这四族**都不受 `approvalPolicy:'never'` 约束**
  （never 只关掉 approval 那一族）。本机 `~/.codex/config.toml` 里确实挂着一个真实 MCP server
  （node_repl）。触发后的 hang 是**永久且无兜底**的 —— 同 1.1。
- **工具过程与输出从未被采集**（审计 WA-020，但它把「层」搞错了）。
  审计说「落盘时丢了 tool output / exit code」—— 实际是**从没采集**：`Turn` 接口
  （`engine.ts:61-86`）没有任何输出字段；`item/completed`（`engine.ts:584-598`）只做三件事 ——
  把 step 标 done（`:590`）、`collectAiWrites` 抓写入路径（`:588`）、把 `reasoning` /
  `agentMessage` 的文本塞进 thinking/reply（`:592-596`，`itemText` 在 `engine.ts:1196`）。
  `aggregatedOutput`（命令的 stdout+stderr）在整个 `apps/working-agent/src` 下**零引用**，
  工具的 `exitCode` 同样 —— `engine.ts:182-187` 那个 exitCode 来自 codex 的 error 载荷，
  不是工具退出码；`McpToolCall` 的 `success`/`error` 一并丢。
  后果：任务真失败时用户和支持人员手上零证据。
  *但记录并未灭失*：`~/.codex/sessions/**/rollout-*.jsonl` 保有完整的 `function_call` /
  `function_call_output` / `reasoning`，**只是没在工作管家里呈现**。
  正确的改法是**失败时才展开** `exitCode` + `aggregatedOutput` 尾部若干行
  （`TurnView.tsx:179` 在 failed 轮本来就会保留现场），**而不是给每个非零退出码染红**（见已证伪）。

原有条目：

- **P1 升级用户拿不到新 SKILL.md —— 主干已修（`ff5016e` + `6edbe43`），四个残口还开着。**
  `codex plugin marketplace add` 至今只更新源指针、不刷新已装 plugin 的 cache 副本；`ff5016e`
  加的 `refresh_cached_skill_docs`（`main.rs:482-534`）绕开它：直接比对
  `~/.codex/plugins/cache/working-agent-p0/<plugin>/local/skills/<skill>/SKILL.md` 与随包原文，
  只在**渲染后**内容不同时覆写，不新建、不删除、不动启用状态。调用点两处 ——
  `ensure_skill_marketplace`（`main.rs:549`，`list_skills` / `set_skill_enabled` /
  前端 `syncSkillsFromEngine` 都会走到）和 `enable_all_p0_skills`（`main.rs:729`，`6edbe43`
  补的：全新机器上 `plugin add` 之后 cache 才存在，前一次刷新必然返回 0）。
  **marker 不再是障碍** —— 刷新完全不经过 `claim_p0_skill_initialization`。测试见
  `main.rs:766` / `791` / `817` / `880` / `909` / `1040`。原文记的「15 份 md5 全是 `80800447…`」
  已不复现（现在是 8 plugin × 8 skill = **64 份**）。
  **还开着的四条**：
  1. `resolve_python_runtime_exe` 只有 `#[cfg(windows)]` 定义（`main.rs:264`），却在
     `main.rs:549` 和 `main.rs:729` 被**无条件调用** —— **HEAD 在 macOS 上编不过**，
     mac 版既拿不到这个修复，也出不了包。
  2. 只刷「cache 里已经有」的 skill（`main.rs:492` 起的 `read_dir(&cache_root)`）。marketplace
     新增第 9 个 skill 时升级用户照样拿不到 —— 唯一会 `plugin add` 的 `enable_all_p0_skills`
     被 marker 挡着（见上面 WA-016）。
  3. 解释器解析不出来时 `render_skill_doc` 返回 None，`main.rs:513-520` 直接 `continue` ——
     那台机器上带占位符的 6 份文档**永远**停在旧版，连与 Python 无关的段落也刷不到。
  4. `set_skill_enabled`（`main.rs:611-626`）在 `plugin add`（`:619`）**之前**才调
     `ensure_skill_marketplace`（`:617`），add 之后不再刷新。用户在技能库/场景卡里单个启用某技能，
     **本次会话读到的是刚拷进来的未渲染原文**（`{{WA_PYTHON_ABS}}` 字面量当路径执行），
     要等下次启动才被刷好。
  本机现状：cache 里 `report-ppt` 那份 md5 `28dff295…`、随包原文 `afbd243c…` —— 因为这台机器自
  `feea75a` 起没再跑过 app，下次启动会被刷上。
- **elevated 沙箱下附件读法拿不到解释器绝对路径**（`\\?\` 那条已证伪，见「已证伪」）。
  SKILL.md 走 `{{WA_PYTHON_ABS}}` 占位符兜住了「`$env:WA_PYTHON` 在 elevated 沙箱里为空」，
  但**附件读法前缀是前端运行时拼的**（`attachments.ts:112`、`:123`），只有 `$env:WA_PYTHON`
  一条路、**没有绝对路径通道** —— elevated 机器上带 Office 附件的任务会直接落到
  「如实告诉用户没能读出文件内容」。修法：把渲染好的绝对路径也交给前端（或让 Rust 侧拼这段前缀）。
- **`jsonSet` / `jsonGet` 静默失败**（`lib/storage.ts`）—— 写失败无感知（`catch {}` 注释写着
  「quota/private mode 忽略」）；存储损坏时 `jsonGet` 返回 fallback，等于**全部历史静默清零还伪装成
  「本来就没有」**。
- **删除确认框低报范围** —— 一条对话可能装多个任务（实测最多 3 个），而 thread 只有一个 `title`，
  文案就只报那一个（`HistoryPanel.tsx:235`，`「${delThread?.title ?? ''}」将被永久删除，无法恢复。`），
  而这个标题本身冻结在第一轮（见下条）。
- **对话标题永久冻结在 `turns[0].userText`**（`app.ts:284`，只在 `!currentThreadId` 分支里算一次），
  之后再不更新；配合 `deleteFrom` 能让标题和内容无关。
- **删除动画残留 280ms 窄竞态**（!53 的残留，`HistoryPanel.tsx:251`）：任务恰在确认后 280ms 内
  才开始跑时仍会演完弹回。
- **每次启动必现** `[FE] PROMISE REJ: RELAY_PROVISIONING` —— 来自 `auth.rs:702`
  （`state.relay_ready` 为 None）。**我们这台正常工作的机器也有它，别当病因。**
- **codex-plus-manager 会整份覆盖 config.toml** —— **比原先写的严重，而且是它踩我们**：
  `commands.rs:1908` 是裸 `std::fs::write(&config_path, &updated_config)`（**无锁、无 tmp+rename、
  无回读校验**），`commands.rs:2498` 的 `save_relay_file_in_home` 同样把前端整段文本直接盖上去 ——
  **完全绕开工作管家那把共享 relay 锁**（见 2.4）。更狠的是
  `crates/codex-plus-core/src/relay_config.rs:2246` 的 `retain_only_provider_table`：
  `providers.clear(); providers.insert(…)` 会把 `[model_providers]` 下**其他所有 provider 表整张删掉**。
  而两个 app **抢的是同一个 provider id**（WA 的 `RELAY_PROVIDER_NAME = "custom"` vs manager 的
  `RELAY_PROVIDER = "custom"`），写进 `[model_providers.custom]` 的 `base_url` 语义还不同
  （WA 固定 `http://127.0.0.1:57321/v1`，manager 写用户供应商地址）。
  **同机同时装两个 app 时，manager 能把 WA 的 relay 路由整段抹掉，WA 下次启动又抢回来。**
  只影响同时装了「Codex 增强版」并主动点应用配置的人。
- **注销/换账号不清理任务与本地数据**（审计 WA-012）。机制属实：`logout`
  （`AuthGate.tsx:94-105`）只改 auth 状态 —— 不清 store、不停引擎、localStorage 键全局无命名空间。
  但审计押注的「账号 B 继承 A 的数据」这条链**前提不成立** —— 两个 relay 账号要共用同一个
  Windows 登录，而那时 `~/.codex/config.toml` 里 A 的 relay Key 早就共享了；而且 B 挂载时
  `setModel` 会清掉 threadId（`App.tsx:57` + `engine.ts:1106-1107`），B **不会接着 A 的 codex 会话讲**。
  **真正值得修的是审计只捎带提了一句的那半边，且只需一个用户一个账号就能撞上**：
  任务运行中退出登录 → `stop()` 走的是 `rpc('turn/interrupt')` → `sendFrame` →
  `invoke('engine_send')`（`engine.ts:304-305`），而 `engine_send` 不在 `command_is_public`
  白名单里（`auth.rs:29-42`），`command_access_decision` 直接判 `AuthRequired`（`auth.rs:100`）——
  **停止按钮彻底失效，且 `engine.ts:1046` 的 `.catch(() => {})` 把它吞得一声不响**，
  而 agent 仍在 workspace-write 沙箱里往产出目录写文件；`list_dir` 同样被拒，
  `listDir` 的 `catch { return [] }`（`engine.ts:382-386`）把空产出交给 `engine.ts:484`
  判成 `missing_deliverable` —— **一个真的做完了 PPT 的任务在历史里显示「任务未完成」**，
  用户会重跑一遍。修的正确顺序是：logout 先 `stop()` 并 await 本轮终结，**再**清 store。
  **另一半更要紧（2026-08-07 新增）：`logout` 根本不回收本地凭据。**
  `auth.rs:1345-1400` 只清钥匙串里的 session cookie 并打一发远端 `/api/user/logout`，
  **没有一行**去删 `~/.codex/config.toml` 的 `experimental_bearer_token`
  （`relay_config_tx.rs:964` 写进去的）或 `~/.codex-session-delete/settings.json` 的 `relayApiKey`。
  配合 helper 零鉴权 + `ACAO: *`（0.1 第 2 条），**「退出登录」在安全语义上什么都没退** ——
  那把能花钱的 `sk-…` 留在明文文件里，任意本机进程和任意网页照样能用。
  「登录过一次」= 那个口子永久开着。修法：logout 一并撤销本地 provider 段与 settings 里的 key
  （最好也向服务端吊销该 relay token）。
- **纯离线打不开，且 shrimpai.cc 是单点**（2026-08-07 新增）。冷启动 `authorized = None`
  （`auth.rs:353-359`），只有一次成功的 `GET shrimpai.cc/api/user/self`（`auth.rs:1963`）能翻态；
  断网走 Transient 分支只是「不清凭据」，**不 set authorized**（`auth.rs:1966-1974`），
  于是第 0 秒起所有非公开命令判 `AUTH_REQUIRED`，**没有任何离线宽限**（HTTP 超时 20s，
  `auth.rs:405-410`）。而登录态、模型目录（`GET shrimpai.cc/v1/models`，`model_catalog.rs:109-113`）、
  上游推理三件事全押在同一个域名上，**该域名挂了产品完全不可用，无降级**。
  另有一条门内死角：relay 未就绪时卡在 RelayRepairGate，只有「重新绑定 / 重新检查 / 退出账号」
  三个按钮，**没有进入工作台的出口**（`RelayRepairGate.tsx:53-79`）—— 登录成功也可能进不去。
- **两条被高估的防线（2026-08-07 核出，别当已有保护）**：
  ① 「Tauri invoke 总闸拦住一切非公开命令」不准确 —— tauri 2.11.5 里 `plugin:` 前缀命令走
  `manager.extend_api`，**不进 `run_invoke_handler`**，`main.rs:3485` 那道闸管不到它们；
  `capabilities/default.json` 授予的 `core:default` / `dialog:allow-open` 未登录可调
  （跑不了 agent、读不到 key，但话不能说成「全部被拒」）。
  ② 工作管家**不检测也不清理**残留的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` ——
  `env_conflicts.rs` 只有 manager 在用（`codex-plus-manager/src-tauri/src/commands.rs:1597/1608`），
  而 working-agent 的 `Cargo.toml` 没依赖 `codex-plus-core`（只 `#[path]` 内联了单个
  `working_agent_relay_tx.rs`）。而且 helper spawn `codex app-server` 时**继承父进程环境、
  不做白名单过滤**（`launcher.rs:2097-2114`），机器上残留的 `OPENAI_*` 会被原样交给 codex。
- **manager 的 darwin sidecar 内嵌版本是 1.2.18**（`package.json` 已 1.2.19），库里那份是旧的；
  且 `tauri.conf.json:31` 的 `externalBin` 意味着 **Windows 版 manager 无法从干净 clone 构建**。
- **Windows 上系统敏感路径保护 100% 不存在**（审计 WA-015 报的是「symlink 绕过」，方向偏了）。
  两层原因叠加：`main.rs:2231` 的 `Component::Prefix(_) => return false` 让
  `is_sensitive_system_path` 在 Windows 上**永远返回 false**；即使去掉早退，`main.rs:2244` 的敏感根清单
  `["/etc","/System","/usr","/private","/Library"]` **全是 macOS 路径**，
  `C:\Windows`、`C:\Program Files`、`C:\ProgramData` 一个都不在。
  所以用户在设置页敲 `C:\Windows\System32`，会经 `resolve_out_dir_path`（`main.rs:2265-2287`）
  直接走到 `main.rs:2219` 的 `create_dir_all`。
  **实际危害很小**（非提权进程在那儿建目录会失败并返回 `OUTPUT_DIRECTORY_CREATE_FAILED`），
  但审计假定存在的那道防线在发行平台上是空的。**测试也照不出来**：
  `main.rs:2296-2594` 的 `r4_sensitive_system_path_tests` 共 14 个 test，6 个挂着
  `#[cfg(unix)]` / `#[cfg(target_os = "macos")]`；剩下 8 个在 Windows 上确实跑，但断言的全是
  `/etc`、`/etcetera`、`/etc/../tmp/…` 这类 POSIX 路径 —— 它们在 Windows 上 `is_absolute()`
  就是 false，函数在 `main.rs:2224` 第一行就返回，**测试空过**。
  **没有任何一条断言过 `C:\Windows` 应该被拒**，CI 照样全绿。
  另：`main.rs:2212` 的 `sens.iter().any(|s| s == p)` 精确相等在大小写不敏感的 NTFS 上会漏判 ——
  用户敲小写 `c:\users\x\desktop` 时「工作管家」子目录重定向静默失效，文件直接落桌面。
  （盘符那一段不受影响：Windows 下 `Prefix::Disk` 存的是大写化后的字节，`c:` 与 `C:` 相等。
  原先写的「或带尾部反斜杠时」是错的 —— `Path::components` 会把尾部分隔符归一化掉，`==` 仍然命中。）

---

# 沙箱状态 —— unelevated 下可用；elevated 是另一套机制，且已经咬过两次

| | 状态 | 证据 |
|---|---|---|
| 配置正确 | ✅ | `[windows] sandbox` 已写入（`relay_config_tx.rs:295` `ensure_windows_sandbox`，默认值常量在 `relay_config_tx.rs:22`，调用点 `main.rs:1561`）。缺这个键 workspace-write 会**静默降级只读**；codex.exe 里还有**第二条**静默降级路径 —— `derived permission profile cannot be represented as a legacy sandbox policy; falling back to read-only` |
| 沙箱内执行命令 | ✅ | `function_call_output`：`Exit code: 0, Wall time: 1.4 seconds`（**本机 unelevated 下测的**，未在 elevated 下复测） |
| 沙箱内读文件 | ⚠️ **按档位分岔，原文写「✅」是错的** | `unelevated`：`CreateRestrictedToken` 只加 `WRITE_RESTRICTED`、`SidsToDisable` 为空，仍以 `CreateProcessAsUserW` 保留用户身份 → **读不受限**。`elevated`：`CreateProcessWithLogonW` 以独立本地账户 `CodexSandboxOffline`/`CodexSandboxOnline` 跑（只属 `CodexSandboxUsers` + `Users`）→ **读取由 ACL 决定**。实测 `icacls` 命中 `CodexSandboxUsers` 的条数：产出目录/桌面/文档/下载/图片/OneDrive 有 `(OI)(CI)(RX)`；`C:\Users` 根、用户 profile 根、`C:\tmp`、`C:\Program Files`、**别的盘根（D:/F:）**、**被搬迁过的已知文件夹**（`D:\Yilia\下载`，owner-only ACL 且断继承）全是 **0** |
| **沙箱内写文件** | ✅ | session `11-28-26` 里 agent 自己建了 `templates/` 并写入两个 .md，磁盘 mtime 对得上（同样是 unelevated；elevated 下产出目录的写权限来自 setup helper 的 `write_roots` 显式授权，不是同一套机制） |
| 随包 Python 可用 | ✅ 但**不能靠环境变量送进去** | 解释器本身没问题。但 `[shell_environment_policy.set]` 的 `WA_PYTHON`（`relay_config_tx.rs:345`）**不进 elevated 沙箱** —— `CreateProcessWithLogonW` 不传注入的 env，同事日志里 `Get-ChildItem Env:` 匹配 `PYTHON` 输出为空。通配符搜索也不行：elevated 下沙箱账户对 `C:\Users` 与用户 profile 根 `icacls` 命中 0，**不能枚举、但能按绝对路径直达**。`feea75a` 改走文档内占位符：`main.rs:462` `{{WA_PYTHON_ABS}}` → `main.rs:466` `render_skill_doc` 渲染绝对路径 → `main.rs:482` `refresh_cached_skill_docs` 刷缓存，`6edbe43` 补了首装那一次（`main.rs:729`）。**未在同事机器上回归验过。** |
| 附件在沙箱里读得到 | ✅ 绕开而非解决 | `db96b78`：发送前由**应用**（以用户身份，唯一读得到源文件的身份）把附件复制进 `<产出目录>/上传文件/`，再把复制后的路径给模型。Rust `stage_attachments`（`main.rs:1953`，常量 `main.rs:1923` `ATTACHMENT_STAGE_DIR`）：cwd 内不复制、同名加 ` (2)` 且保留扩展名、复制失败**报错**而非静默交出原路径、目录不复制。配套 `engine.ts:398`/`:438` 让 `detectDeliverables` 排除该目录 |
| 网络确实被拦 | ⚠️ | **仅有 `turn_context` 的声明，未实测** |
| 端到端产出 office 文件 | ❌ | **「卡在模型层」是当时的误判。** 同事机器上真正的两个故障：① SKILL.md（无 BOM 的 UTF-8）被 PowerShell 5.1（ACP=gb2312）读成乱码、**退出码 0**，我们写的禁令根本没送到模型（她日志里是 `description: "姹囨姤 PPT 鍒朵綔…"`）；② `WA_PYTHON` 不在 elevated 沙箱环境里。第二条**就是沙箱**。两条都在 `feea75a` 修了，**但没在她机器上回归验过**。3.1 / 3.2 / 4.1 是独立的上游问题，不是这一格的原因 |

**同事机器上 `[windows] sandbox = "elevated"`，不是我们写的 `"unelevated"`。**
我们的 `ensure_windows_sandbox`（`relay_config_tx.rs:295`）只在键**缺失**时才写默认值 ——
`apply_windows_sandbox_default` 在 `relay_config_tx.rs:1069`（`[windows]` 子表）和 `:1075`（inline table）
各有一次 `contains_key → Ok(false)`。这个设计不改：config.toml 与官方 Codex 共享。那台机器同时装着
**官方 Codex**（`[marketplaces.openai-primary-runtime]` + `documents`/`pdf`/`spreadsheets`/
`presentations`/`template-creator` 五个 plugin + `[desktop]` + `[tui.model_availability_nux]`），
`elevated` 很可能是它写的。

**原文末句「是否影响沙箱行为我没有实测过」现在有答案：影响极大，而且已经咬过两次。**
两个档位是**两套完全不同的实现**（codex.exe 里 `WindowsSandboxModeToml` 的两个取值）：

- `unelevated` —— `CreateRestrictedToken` 只加 `WRITE_RESTRICTED`、`SidsToDisable` 为空，
  `CreateProcessAsUserW` **保留用户身份**（二进制里 `windows-sandbox-rs\src\token.rs`）。
  → 读不受限、注入的 env 进得去。
- `elevated` —— `CreateProcessWithLogonW` 以独立本地账户 `CodexSandboxOffline` / `CodexSandboxOnline`
  登录后跑（只属 `CodexSandboxUsers` + `Users`）。
  → **读取由 ACL 决定**、**注入的 env 一个都不传**。可读/可写范围由 `codex-windows-sandbox-setup.exe`
  逐个根目录授权：它的 Payload 有 `read_roots` / `write_roots` / `deny_read_paths` / `deny_write_paths` /
  `proxy_ports` / `allow_local_binding`，模式含 `full` / `provision-only` / `read-acls-only`，
  日志串 `Granting sandbox read access to …` / `Sandbox read access granted for …` / `apply deny-read ACLs`。

由此炸出的两个真故障 —— `db96b78`（附件读不了）和 `feea75a`（`WA_PYTHON` 不存在）—— **都只在 elevated 下复现**。
**以后任何「沙箱里能不能 X」的结论必须标档位，unelevated 的结论一律不许外推。**

## `codex sandbox` 子命令（诊断工具，值得弄通）

它是**唯一能脱离 GUI 和模型、单独验证「沙箱里能不能干某件事」**的手段。前两次想用都用不了，
只能退回「连 WS 驱动整轮真实对话」——几十秒、烧 token、还得指望模型配合。

**用不了的原因已经查清。** `-P/--permissions-profile <NAME>` 从配置栈按名字取 profile
（帮助文本：`Named permissions profile to apply from the active configuration stack`），
配置里写成 **`[permissions.<NAME>]`**，值就是 `PermissionProfileToml`；还必须配顶层
`default_permissions` 才生效（codex.exe 里的诊断串：
``config defines `[permissions]` profiles but does not set `default_permissions`:``）。
我们这台机器的 config 里 `[permissions]` 命中 0。**它是可注入的**：用 `-c 'permissions.dbg....'`
时报错是「值类型不对」而非「键不存在」。

而且 profile 与我们现在走的 `sandbox` 那条路**天生互斥**，二进制里三条硬错误：
``permissions` cannot be combined with `sandbox``、
``sandbox_mode` and `permission_profile` overrides cannot both be set``、
app-server 侧 ``permissionProfile` cannot be combined with `sandboxPolicy``。
我们前端发的正是 `sandbox: 'workspace-write'`（`engine.ts:1016`），
**所以 profile 只能拿来单独做诊断，不能顺手接进产品。**

schema 已从 codex.exe 的 serde 字符串里挖全：

- `PermissionProfileToml` **5 个字段**（`struct PermissionProfileToml with 5 elements`）：`description` /
  `extends` / `workspace_roots` / **`filesystem`**（TOML 侧是一个词；协议 JSON 侧才叫 `file_system`，
  别搞混）/ `network`
- **`filesystem` 是 untagged enum `FilesystemPermissionToml`**（报错串 `data did not match any variant of
  untagged enum FilesystemPermissionToml`），两种形状：① 直接给一个 `FileSystemAccessMode` 字符串 ——
  TOML 侧取 `read` / `write` / `deny`（协议侧还多一个 `restricted`）；② 给一张表
  `{ entries = [...], glob_scan_max_depth = N }`。`entries` 元素的键：`path` / `type` / `glob_pattern` /
  `pattern` / `special` / `value` / `access`；`special` 走 `FileSystemSpecialPath`，`kind` 取
  `root` / `minimal` / `project_roots` / `subpath` / `tmpdir` / `slash_tmp` / `unrestricted` / `external-sandbox`
- **`network` 是 `NetworkToml`，13 个字段里只有 7 个是代理配置** —— 原文写「13 个字段全是代理配置」是错的。
  策略层 6 个：`enabled` / `mode` / `domains`（元素是 `NetworkDomainPermissionToml`，`allow` / `deny`）/
  `unix_sockets`（`NetworkUnixSocketPermissionToml`）/ `allow_local_binding` / `mitm`；
  代理层 7 个：`proxy_url` / `enable_socks5` / `socks_url` / `enable_socks5_udp` / `allow_upstream_proxy` /
  `dangerously_allow_non_loopback_proxy` / `dangerously_allow_all_unix_sockets`。
  旁边那个 `struct NetworkProxyConfigToml with 12 elements` 就是同字段去掉 `enabled` 的版本，可交叉印证切分。

**两层嵌套形状已经不缺了，下一步是真跑一次 `-P` 拿退出码，别再读字符串。**
跑之前必须先排掉一条静默陷阱：profile 推导不出等价的 legacy sandbox policy 时 codex 会**降级只读**
（`derived permission profile cannot be represented as a legacy sandbox policy; falling back to read-only`），
落进这条分支时测出来的「读不到 / 写不了」全是自己造的。

---

# 设计决定（不是 bug，别当问题修）

- **pandas / matplotlib 没有随包**（各十几 MB）。`excel-analysis/SKILL.md` 已改成让模型用标准库
  `csv` / `collections` / `statistics`；openpyxl 本身能出图表和公式。
- **50MB / 830 个文件不进 git**。用 `pinned-archive-tree` source type，9 个上游制品各自钉
  `url + sha256 + size`，只允许 `www.python.org/ftp/python/` 和 `files.pythonhosted.org/packages/`。
- **Tauri 资源映射必须用纯目录键**，绝不能用 glob：`tauri-utils` 的
  `resource_paths_iter_map_allow_walk` 测试证明 glob 会拍平子目录。
- **`PYTHONDONTWRITEBYTECODE=1` 必须设**。不设首次 import 就写出 48 个 `__pycache__`，
  而沙箱里根目录只有 read 权限。
- **`multi-platform` / `xiaohongshu` 只补 description 的读取提示、正文一个字不动是对的** ——
  `feea75a` 给这两份各改 1 行，只在 description 尾部追加那句 ASCII `Get-Content -Raw -Encoding utf8`
  提示；没有 `## READ ME FIRST` ASCII 段、没有 `{{WA_PYTHON_ABS}}`：它们产出纯 Markdown，不生成
  office 文件，不需要解释器。门禁把这条差别钉死了 —— `skill-docs.test.mjs:85` 的 description
  检查覆盖全部 8 份，`:98` 的 ASCII 硬规则段检查只对 `FILE_PRODUCING`（`:99-106`）那 6 份生效。
- **删除运行中的对话选择「拒绝」而不是「自动停止」** —— `stop()` 是异步的（发 `turn/interrupt`
  等 `turn/cancelled`，1.2s 兜底），而确认框从没告诉用户会中止任务。
- **launcher 复用（`ready` 无 `spawned`）是设计允许的** —— 同事机器上一个 launcher 活了 29 小时，
  每次开关软件都复用它。判据是 major 版本 + protocol + transport 相等
  （`engine_runtime.rs:173-199`，minor/patch 写的是 `Some(_)` 任意值）。
  **副作用**：1.2.18 的旧 launcher 会被 1.2.19 的新 UI 接受，「新界面 + 旧引擎」是允许的组合。
- **SKILL.md 绝不加 BOM** —— codex 0.142.5 要求首行 trim 掉空白后等于 `---`，而 BOM 不是空白，
  加了该 skill 会被**静默地整条丢出** `<skills_instructions>`，同时 `plugin add` 仍 exit=0、
  `plugin list` 仍报 enabled、`doctor` 一个字不提。抗乱码只能走 codex 自己解码、不经过 shell
  的两条通道：description 尾部的 ASCII 读取提示 + 正文顶部的纯 ASCII 硬规则段（`feea75a`）。
- **解释器路径靠 `{{WA_PYTHON_ABS}}` 占位符，不靠环境变量、更不靠通配符搜索** —— elevated 档位用
  `CreateProcessWithLogonW` 以独立本地账户跑命令，注入的 `WA_PYTHON` 传不过去；而该账户对
  `C:\Users` 没有枚举权，glob 找不到、按绝对路径直达可用。渲染在 `render_skill_doc`
  （`main.rs:466-480`）做，顺手剥掉 `\\?\` 前缀；解释器解析不出来时**跳过写入**
  （`main.rs:513-520`），绝不用空值盖掉一份本来好用的缓存；比较的是**渲染后**内容而非随包原文
  （`main.rs:521`），否则每次启动都无条件重写、`count` 不再代表「真的升级过」。
- **附件由应用侧复制进 `<产出目录>/上传文件/`，不靠提示词教模型换读法** —— 能不能读取决于用户机器的
  磁盘布局与 ACL（elevated 沙箱账户只在被显式授权的目录里有 RX），提示词里判断不了，换个读法也
  绕不过去（PowerShell 和 Python 是同一个身份被拒）。应用进程是唯一读得到源文件的身份，所以由它搬：
  `stage_attachments`（`main.rs:1953`），目录名常量 `ATTACHMENT_STAGE_DIR`（`main.rs:1923`）
  必须与 `engine.ts:398` 一致。
- **目录附件不复制、原样交出** —— 可能极大且语义不同；读不到时让模型如实报告（`db96b78`）。

---

# 已经验过的，别重复验

- 安装目录 `python-runtime` 830 文件完整，目录哈希与 manifest 一致（`1d16ccb6…`）
- 中文安装路径下解释器正常，`import pptx/docx/openpyxl` 全过
- 从**建好的安装包里解出来**的解释器能生成合法 OOXML 的 .pptx/.xlsx/.docx
- 应用启动自动写入 `WA_PYTHON` + `PYTHONDONTWRITEBYTECODE`，官方 Codex 在
  `[shell_environment_policy.set]` 里的键一个没动（同事机器上两个键都在）
- 8 个 SKILL.md **0 处依赖探测**（没有 `python --version` / `Test-Path` / `Get-Command` 探活，
  也不跟用户报「未装」）。注意：`feea75a` 之后直接 grep `pip install` 会命中 6 次 —— 那是 6 份
  产出型技能 ASCII 段里的**禁令**（`Never \`pip install\` anything`），不是探测，别当回归。
- 真实 `turn_context`：`sandbox_policy` = workspace-write / `network_access: false`，
  `file_system` = 根目录 read、产出目录 write、`/tmp` write。**这是本机 `unelevated` 的声明，不能外推**：
  `elevated` 下命令以独立本地账户跑，真正的读取边界由 `CodexSandboxUsers` 在各目录上的 ACL 决定，
  `turn_context` 里看不出来。
- **agent 真的在沙箱里写出过文件**（`templates/` 下两个 .md）
- `claude-opus-4-6` 经我们的 relay **能正常返回 function_call**（探针）
- 对抗性审查四轮：Python 运行时（6 视角）必须修 0；retry（7 视角 + 每条 2 驳斥者）must-fix 0
  且抓到我引入的双击竞态已修；thread（6 视角）must-fix 0 且抓到我引入的动画闪回已修；
  附件提示词（!56）抓出四个真问题并全修 —— 断言「你看不到图片」是能力倒退（codex 自带
  `view_image`）、`Get-Content -Raw` 不带 `-Encoding` 会复现同一类静默乱码、目录/未识别扩展名
  会绕过整个修复、让脚本写进产出目录会污染 `detectDeliverables`。
- SKILL.md 的四条致命写法**逐条实测过**（同 home、同命令、只差 3 字节的 A/B 翻转 4 次，双向确定）：
  BOM / 开头空行（LF 与 CRLF）/ GBK 编码 / 首行不是 `---`，任一命中该 skill 就被静默丢出
  `<skills_instructions>`，唯一能看出来的诊断是 `codex debug prompt-input`。门禁
  `apps/working-agent/scripts/skill-docs.test.mjs`（8 项，挂在 `npm test` 的 `test:bundle` 上）
  把这四条钉死，并做过变异测试（BOM 挂 2 条，空行 / GBK 各挂 1 条）。
- 一次性 CODEX_HOME 里端到端验过：8 个技能**全部**出现在 `<skills_instructions>`，中文完好
  （U+6C47 在、U+FFFD=0、U+FEFF=0），ASCII 段在模拟 ANSI 误读后仍可读。
- 占位符渲染：`{{WA_PYTHON_ABS}}` 在**全新安装**上曾原样送到模型眼里（`refresh_cached_skill_docs`
  跑在 `plugin add` 之前，cache 还不存在直接 `return 0`），已用一次性 CODEX_HOME 复现
  （`RAW PLACEHOLDER STILL PRESENT: True`）并修于 `6edbe43`，测试
  `refresh_renders_freshly_installed_cache_copies_too`（`main.rs:1040`）钉住这个时序。
- 附件暂存：Rust 5 项（`main.rs:933-1038`：复制 / 已在 cwd 内不重复 / 同名不覆盖且保留扩展名 /
  工作目录不可用要报错 / 去重命名）+ 前端 2 项（`engine.test.ts:2662-2710`：暂存文件不算产出、
  且不掩盖 `missing_deliverable`），后两项做过变异测试，去掉 `上传文件/` 过滤即双双变红。
- 全量门禁基线：480 vitest + 40 node + 217 cargo 全过，tsc 干净（`6edbe43`）。

---

# 已证伪，别再查

- ~~「relay 对不同模型的 tool-calling 支持不一致」~~ —— `upstream_request_parts` 不看 model。
- ~~「claude-opus-4-6 只支持 anthropic endpoint 所以工具被丢」~~ —— 探针证明它能正常调工具。
- ~~「工具数量/体积/特殊 tool type 触发上游丢 tools」~~ —— 4→49 个工具、35KB→108KB 全通。
- ~~「缺的 ~31KB 工具集是未知量」~~ —— 已核销为 30464 字节的标准 codex 工具集，
  且在 `fc=19` 成功的请求里一字不差地同样存在。见 4.1。
- ~~「stale 的 SKILL.md 缓存导致模型不调工具」~~ —— 缓存陈旧与 tool-calling 无关。那是独立问题，
  且主干已由 `ff5016e` 的 `refresh_cached_skill_docs` 闭合（`main.rs:482-534`）。
- ~~「删除是按标题匹配的」~~ —— 全前端零处按标题比较，可执行测试证实按 id。
- ~~「~1700 output tokens 是硬性输出上限」~~ —— 探针跑到 3794，`incomplete_details: null`。
- ~~「`codex sandbox` 要求 `[permissions]` 表」~~ —— 实际要求 `-P <具名档案>` + `default_permissions`。
- ~~「同事机器上两个安装并发争抢」~~ —— 4 个 launcher pid **时间零重叠**，D 盘已无安装，
  任务管理器只有一个 exe。那条 `D:\Software` 日志是历史痕迹。
- ~~「`RELAY_PROVISIONING` 是故障原因」~~ —— 我们这台正常工作的机器每次启动也有它。

## 本轮新增的证伪（2026-07-31 / 08-07）

- ~~「工作管家需要 OpenAI / ChatGPT 账号（或 OpenAI API key）」~~ —— **一个都不需要，全链路不碰。**
  登录的是 **ShrimpAI 自家账号**（`auth.rs:8` `AUTH_BASE_URL = "https://shrimpai.cc"`，
  登录 `:1041`、2FA `:1251`、续期 `:1963`，钥匙串 service 名 `com.xiapai.workingagent.shrimpai-auth`），
  服务端自动下发 relay token（`auth.rs:1509`），写成 `experimental_bearer_token`
  （`relay_config_tx.rs:964`）—— 它在 codex 里是与 OAuth **并列的一等鉴权态**
  （codex.exe 里 `ModelProviderInfo with 17 elements` 的字段表把它和 `requires_openai_auth` 并列，
  另有枚举字面量 `Not logged in | Bearer token | OAuth`），所以不走 ChatGPT OAuth。
  `authContents` 恒为空串（`relay_config_tx.rs:518`），本机 `~/.codex/auth.json` 不存在也照常跑。
  codex 里真正强制 ChatGPT 凭据的功能（cloud tasks / remote control / connectors /
  exec-server registration）工作管家一条都不碰 —— 它对 codex 的调用面只有 `codex plugin …`
  （`main.rs:424`）和 helper spawn 的 `codex app-server`。
  **`OpenAI Official` 预设（`codex-plus-manager/src/presets.ts:45-52`）和 README:262
  「切回官方 ChatGPT 登录模式」是 manager 的语义，别串到工作管家头上。**
  *顺带*：全仓 README/CHANGELOG **没有一句**说明工作管家需要注册 ShrimpAI 账号，
  README:192 里的 provider 名 `CodexPlusPlus` 也已过期（现为 `custom`）。
- ~~「工作管家会把残留的 `OPENAI_API_KEY` 判成环境冲突并清掉」~~ —— **不会，方向恰好相反**，
  见第五层「两条被高估的防线」②。

- ~~「`WA_PYTHON` 带 `\\?\` 前缀导致 PowerShell 调不起来」~~ —— **07-30 那条「纠正」自己踩了同一个坑，
  两次独立复测都推翻它。**
  > 2026-08-07 本机实测（PowerShell **5.1.26100.8875**，路径直接从 `~/.codex/config.toml`
  > 读出后调用，全程不经 bash 转义）：`Test-Path` = **True**，
  > `& $p -c "import pptx,sys"` 输出 `1.0.2 / 3.13.12`、`$LASTEXITCODE = 0`。
  > 对照组 `\\?\C:\Windows\System32\cmd.exe` 同样 `Test-Path` True、`&` 调用成功。
  >
  > 07-30 记的「`Test-Path` False + CommandNotFoundException」是**bash 把 `\\?\` 吞成 `\?\`**
  > 的产物 —— 用 `\?\` 在同一台机器上精确复现出了那个 False + CommandNotFound。
  > verbatim 前缀在 PS 5.1 的 command discovery 里**是认的**，最早那句「功能实测无碍」才是对的。
  >
  > `feea75a` 在 `render_skill_doc`（`main.rs:475`）剥前缀本身无害，但 `main.rs:472-473`
  > 的注释理由「`\\?\` 在 PowerShell 的 command discovery 里不认」是错的，别照它推广。
  > `config.toml` 里的 `WA_PYTHON` 至今仍带前缀（`relay_config_tx.rs:1039-1041` 原样写入），
  > **不需要改。**
- ~~「给 SKILL.md 加 UTF-8 BOM 能修 PowerShell 5.1 读出的乱码」~~ —— **反向致命**。
  同 home、同命令、只差 3 字节的 A/B 实测：BOM 会让 codex 0.142.5 把该 skill 从
  `<skills_instructions>` 里**静默整条丢掉**，而 `plugin add` 仍 exit=0、`plugin list` 仍报
  installed/enabled、`doctor` 一个字不提，唯一看得出来的是 `codex debug prompt-input`。
  同类静默丢弃另有三条：开头空行、GBK/GB18030 编码、首行不是 `---`。一次踩中就是 8 个技能同时消失。
  现在是硬门禁：`scripts/skill-docs.test.mjs:42-78`（`feea75a`）。
- ~~「`[shell_environment_policy.set]` 里的 `WA_PYTHON` 一定能进到沙箱子进程」~~ ——
  2026-07-31 同事机器实测：config.toml 里有 `WA_PYTHON`，沙箱内
  `Get-ChildItem Env: | ? Name -match 'PYTHON|WA_PYTHON'` **输出为空**；elevated 后端用
  `CreateProcessWithLogonW` 起进程，注入的 env 没传过去。让模型自己 glob 也不行 ——
  沙箱账户对 `C:\Users` 和 profile 根目录 icacls 命中数 0，**不能枚举但能按绝对路径直达**。
- ~~「Windows 沙箱只限制写、读不受限」~~ —— **只在 `unelevated` 档位成立**。
  `[windows] sandbox = "elevated"` 下 codex 以独立本地账户 `CodexSandboxOffline/Online` 跑命令
  （实测只属 `CodexSandboxUsers` + `Users` 两组），读得到什么完全取决于 ACL 是否显式授权。
  完整取证见「沙箱状态」那张表。**07-30 写下那条硬证据时的机器是 unelevated，不该外推。**

## 外部审计（`Working-Agent-Bug.md`）里不成立的，别照着改

那份报告 23 条里，以下几条**验证不成立**。它跑在 **macOS**（路径 `/Users/luc1ferx`、
`/private/tmp`、「python3 externally managed environment」），而我们发行的是 **Windows** ——
这是它多数误判的来源。

- ~~**WA-001「P0：默认不隔离官方 Codex 配置」**~~ —— **误读了一个 dev-only 调试旁路。**
  `WORKING_AGENT_DEV_CODEX_HOME` 这个常量和读它的函数**整个挂在 `#[cfg(debug_assertions)]` 上**
  （`main.rs:2936-2967`），release 编译时 `active_dev_codex_paths()` 是硬编码 `None`
  （`main.rs:2969-2972`），**env 变量连读都不读**。所以「未设 env 就回退真实 HOME」这个语义在发布版里
  不存在。共用真实 `~/.codex` 是**产品设计**（工作管家是 Codex 的打包发行版，靠这个目录才能用上
  codex CLI 的 marketplace/plugin/provider）。且**对 config.toml 的**写入全部是 `toml_edit` 逐键
  surgical + 回读校验 + 共享文件锁 + 解析失败即拒写：`marketplace add` / `plugin add` 是幂等
  additive（`added==0` 就不写文件），`apply_windows_sandbox_default` 在键已存在时 `return Ok(false)`
  （`relay_config_tx.rs:1050-1071`，注释写明「那是别人的显式配置」），`ensure_office_runtime_env`
  只碰 `WA_PYTHON` / `PYTHONDONTWRITEBYTECODE` 两个键（`relay_config_tx.rs:1038-1047`）。
  **WA 侧没有任何一处全量覆盖 config.toml** —— 真正做裸覆盖的是 **manager**，见第五层。
  唯一的全量写目标是 skill 缓存文档本身：`refresh_cached_skill_docs`（`main.rs:482-534`）整文件覆写
  `~/.codex/plugins/cache/working-agent-p0/<plugin>/local/skills/<skill>/SKILL.md`，但只在两边都存在
  且渲染后内容不同时写，不新建 plugin、不删文件、不动启用状态。
- ~~**WA-019「附件校验接受目录，可致巨大上下文/长扫描」**~~ —— **两条后果都不存在。**
  应用**从不把附件内容送进上下文**：给模型的只有路径字符串加一段按扩展名生成的读法
  （`buildAttachmentPrefix`，`attachments.ts:95-119`；`Composer.tsx:112` → `engine.ts:1027`），
  校验本身只取 `size`/`is_file`（`attachments.ts:145-166`）。既没有递归扫描，也没有内容入上下文 ——
  目录路径的成本就是一个字符串。而且「接受目录」是**刻意**的，`attachments.test.ts:91` 有专门
  测试钉住；选择器还传了 `directory: false`（`Composer.tsx:65`），只能靠拖入。
  **`db96b78` 之后有两句要改**：① 「应用一个字节都不读附件」不再准确 —— 发送前 `stage_attachments`
  会 `std::fs::copy` 把每个**文件**附件整份复制进 `<产出目录>/上传文件/`（`main.rs:1953-2010`，
  拷贝失败直接报错、绝不静默把原路径交出去）；**目录仍然不复制**（`!source.is_file()` 原样交给模型，
  `main.rs:1977-1984`），所以「目录 → 长扫描」这条依然为假。② `MAX_ATTACHMENT_BYTES = 200MB`
  （`attachments.ts:3`）**不再是表演性的** —— 它现在是那次同步复制的唯一上界：199MB 的 PDF 会在
  点「发送」到 turn 真正开始之间被整份拷一遍（`Composer.tsx:94-103` 是 await 的），且没有任何进度反馈。
- ~~**WA-022「成功任务默认隐藏工具步骤」**~~ —— 是刻意取舍（源码注释写明），且用户仍能通过
  「💭 思考过程」和产出文件卡片间接看到。措辞也不准：不是「默认折叠」，是**条件渲染直接不进 DOM**
  （`TurnView.tsx:179` 的 `streaming || failed`），比它描述的更彻底、没有可点的披露件。
  但反过来这也降低了危害：**steps 本来就不落盘**（`app.ts:18` 的 `Pick` 不含它，`:133` 恒 `[]`），
  暴露它属于**新功能**而非修回归。
- **WA-006「失败工具被显示为完成」从 P1 降到 P3，且它给的药方是错的。**
  代码事实成立（`engine.ts:584-590` 的 `item/completed` 无条件把 step 打成 `done`，
  全程不读 `status`/`exitCode`/`error`），但后果被严重夸大：steps 只在 `streaming` 或 `failed`
  时渲染（`TurnView.tsx:179`）、落盘时被清空，**唯一读 `st.state === 'done'` 的地方是
  `TurnView.tsx:191` 的标签文字**（「完成」/「出错」/「进行中」）—— 它不参与任何「任务是否成功」
  的判定（`finishTurn` 的 status 完全来自 `turn/completed` 的 `p.turn.status`、error 事件与
  reply/deliverable 校验）。
  **而「非零 exitCode 就标红」这个修法是反向的**：`rg` 退出 1 = 没匹配，是正常探测；
  探测型 `pip` 失败后恢复也是正常 agent 行为。照它改会在成功任务里刷出红行，制造恐慌。
  真正该做的是**失败时展开诊断材料**，见第五层 WA-020 那条。
- **WA-014「不完整协议响应被误判成功」四条子论断里没有一条构成真 bug。**
  ① 流末 buffer 残留**按构造必然是不完整 SSE 帧**，丢弃是规范行为（WHATWG EventSource 要求），
  且它也过不了 `protocol_proxy.rs:432` 的 JSON 解析；② `finish_reason=None → "completed"`
  （`protocol_proxy.rs:3408-3413` 的 `response_status`，只有 `Some("length")` 映射到 `incomplete`）
  在只有四个状态可选、上游正常发完 `[DONE]` 时是**唯一合法选择**（3.2 已把它定性为上游行为，
  重新包装成我方 bug 是倒退）；③ `status===undefined → done`（`engine.ts:603`）确实是同一函数内的
  不一致，但需要 app-server 发出「id 对得上却没 status」的畸形事件，无可达证据；④ **明确为假** ——
  `engine.ts:475` 的 `if (files.length)` 让 AI 写出的文件短路整个失败判定，工具产出**是已经被计入的**。
  *真正值得改的是它没说的那条*：relay 手里有「既没见 `[DONE]` 也没见任何 finish_reason」这个信号，
  finalize 时完全不用。
- **WA-010 的「mtime 截断到秒导致漏检」不可达。** mtime 确实被截到整秒，但比较是**严格 `>`**
  且新文件走 `!(k in snapshot)` 分支必被抓 —— 要漏必须「文件在基线里已存在」**且**「AI 在基线
  `listDir` 的同一秒内覆写它」，等于模型往返 < 1s，现实中关不上。
  （WA-010 的另两条子论断成立，已分别并入 1.3 和 1.7。）
- **WA-008「模型读了 SKILL.md 却不遵守」的证据不成立于 Windows。**
  随包 CPython 与 `WA_PYTHON` 全部 `#[cfg(windows)]` 门控（`prepare_office_runtime` 的 windows 版在
  `main.rs:1575`，non-windows 是空函数 `:1591-1592`；`WA_PYTHON_KEY` 与 `ensure_office_runtime_env`
  见 `relay_config_tx.rs:29-30`、`:344-345`），**macOS 上 `WA_PYTHON` 恒为空**，而 8 份 SKILL.md
  **明文授权**此时退回 Markdown —— ASCII 硬规则段（`weekly-report/SKILL.md:44`、
  `excel-analysis/SKILL.md:46`、`report-ppt/SKILL.md:48`）与中文段（`weekly-report/SKILL.md:137`、
  `meeting-notes/SKILL.md:152`、`report-ppt/SKILL.md:152`）各写了一遍。所以「120s 没出 DOCX」
  在 macOS 上是**合规行为**，不是违规证据。（`feea75a` 重排过这 8 份文档，原来引的「第 95 行」已不存在。）
  而且它把问题泛化成「LLM 不听话」，反而**错过了真正可修的那条**：交付物闸门只查文件存在性不查类型，见 1.2。
  另外「没有交付物合规强制」这半句为假 —— `missing_deliverable` 判定早在 `df01923`（2026-07-14，
  审计基线的祖先）就上线了，现在在 `engine.ts:476-490`。

---

# 环境坑（都真踩过）

- **跑 .ps1 必须用 `pwsh`**（本机 7.6.4），不能用 `powershell`。5.1 按 ANSI 读 UTF-8 脚本，
  中文路径全被读坏，表现为 `Test-Path` 返回 False / 找不到 `codex.exe`。
- **`$script` 是 PowerShell 作用域保留字**，别当变量名用（会静默变 null）。
- **写脚本别用 bash heredoc**。Git Bash 里 heredoc 会吞掉 `\\`，正则 `[^"\\]` 变成非法的
  `[^"\]` 直接语法错误。用 Write 工具落文件。**这条已经害我误判过一次根因**（`\\?\` 前缀，见已证伪）：
  heredoc 把 `'\\?\'` 落成 3 字符的 `'\?\'`，被测对象在写入磁盘时就已经被篡改了。
- **Git Bash 里 `/tmp` 不是 `C:\tmp`**，是 `usertemp` 挂载到 `%LOCALAPPDATA%\Temp`（`mount` 输出
  `C:/Users/Administrator/AppData/Local/Temp on /tmp type ntfs (... usertemp)`）。`C:\tmp` 也存在，
  但是**另一个目录**、内容完全不同 —— 两边都不是仓库里的 tmp。
- **`python3` 在 PATH 里「存在」，是 WindowsApps 的执行别名 stub**
  （`AppData/Local/Microsoft/WindowsApps/python3`）：它打印
  `Python was not found; run without arguments to install from the Microsoft Store`，
  **退出码 0**。所以 `which python3` 成功、`command -v` 成功、`$?` 也是 0 —— 比根本没有更坏，
  任何按退出码判成功的探测都会被它骗过。脚本一律用 `node`；要真解释器用随包那份。
- **判断子进程成败看 `$?` / `$LASTEXITCODE`**，不要看 `cmd | head` 的管道退出码。
- **前端测试命令是 `npm test`**（= `vitest run --exclude "scripts/*.test.mjs" && npm run test:bundle`）。
  直接跑 `npx vitest run` 会把 `scripts/*.test.mjs` 那些 node:test 文件吸进来，报假失败。
- **`cargo test` 第一次常挂 `os error 32`** —— build.rs 撞上 `python-runtime\libcrypto-3.dll`
  被占用（dev 构建跑过就会锁着），重跑即过。
- **打包前确认 vite 真的死了**（查 1420 端口）。`tauri dev` 退出后 vite 子进程可能存活，
  锁着 `rollup.win32-x64-msvc.node` / `esbuild.exe`，`npm ci` 报 EPERM -4048。
- **打包 gate 要求 `git status --porcelain` 完全为空**（`build-windows-bundle.ps1:66-72`，
  未追踪也算，非空直接 `throw "The Windows bundle gate requires a clean checkout."`）。
  当前挡路的是 5 个未追踪：`7_30.md` / `dump_at.py` / `scan_sandbox.py` / `scan_win.py` /
  `apps/codex-plus-manager/src-tauri/binaries/codex-plus-plus-x86_64-pc-windows-msvc.exe` ——
  出包前挪到 `%TEMP%/wa-hold/`，出完挪回。另外还有两份 `M`（`src-tauri/gen/schemas/*.json`，
  见下条），那两份是**换行符污染，挪目录治不了，得 `git checkout --` 掉**。
- **每次 commit 后 dev/test 构建都会挂**（`repository-built bundle components do not match the
  current commit`）。恢复配方（来自 `build-windows-bundle.ps1:79-118`，别凭记忆）：

  ```bash
  # 1) 重建 launcher（仓库根目录）
  CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS="-C target-feature=+crt-static" \
  CARGO_TARGET_DIR="<repo>/target" \
  cargo build --locked --release --target x86_64-pc-windows-msvc \
    --package codex-plus-launcher --bin codex-plus-plus
  # 2) 重新暂存（apps/working-agent 下；用 --codex 复用缓存, 别用 --download-codex）
  node scripts/prepare-bundle-resources.mjs \
    --helper "<repo>/target/x86_64-pc-windows-msvc/release/codex-plus-plus.exe" \
    --codex  "<repo>/apps/working-agent/src-tauri/target/bundle-inputs/codex-x86_64-pc-windows-msvc.exe"
  # 3) 验证（exit 0 才行）
  node scripts/prepare-bundle-resources.mjs --validate-staged
  ```

  launcher release 构建约 60 秒；缓存都在 `bundle-inputs/` 时重新暂存很快。
- **`tauri dev` / `tauri build` 会用 CRLF 重写 `src-tauri/gen/schemas/*.json`** ——
  `desktop-schema.json` 和 `windows-schema.json`，必现，现在仓库里就挂着这两条 `M`。
  纯换行符差异：`git diff --ignore-all-space --numstat` 对它们**输出为空**。
  提交/出包前 `git checkout --` 掉。`src-tauri/Cargo.toml` 只在 tauri 真动依赖时才一起被重写。
  （别顺手 checkout `.ps1`：`.gitattributes` 里 `*.ps1 text eol=crlf` 是故意的。）
- **解 zip 用系统自带 bsdtar**（`%SystemRoot%\System32\tar.exe`）。Git Bash 的 `tar` 是 GNU tar，
  读不了 zip —— 代码里已加 `--version` 校验，别绕过它。
- **PyPI 会把这台机器限速到 3KB/s**。缓存命中也重算哈希，把已验证的制品拷进 `bundle-inputs/`
  是安全的加速手段。
- **跑对抗审查 workflow 时子 agent 会真的动仓库**。有一次把 `relay_config_tx.rs` 的删除暂存进了
  index。审查期间别同时跑 gate。
- **`SKILL.md` 的字节是硬约束，违反任何一条 codex 就把整个 skill 从 `<skills_instructions>`
  里静默丢掉**：UTF-8/UTF-16 BOM、开头空行（LF 或 CRLF）、GBK/GB18030 编码、首行 trim 后不等于 `---`。
  踩中时 `codex plugin add` 仍 exit=0 打印 "Added plugin"、`codex plugin list` 仍报
  "installed, enabled"、`codex doctor` 一个字不提 —— **唯一能看出来的探针是
  `codex debug prompt-input`**。一次踩中就是 8 个技能同时消失，表现为「技能装着但模型从不用它」。
  反过来 `marketplace.json` 加 BOM 则直接让 `marketplace add` exit=1。
  四种写法已被门禁钉死在 `scripts/skill-docs.test.mjs:42-78`，动 SKILL.md 前先读它，
  别用带 BOM 的编辑器。
- **PowerShell 5.1 的 `Get-Content` 不带 `-Encoding` 时按系统 ACP 解码**（gb2312 机器上就是 GBK），
  无 BOM 的 UTF-8 中文全变乱码而**退出码是 0**，静默到底。而 BOM 又不能加（见上条）——
  所以任何要给模型看的文本只有两条通道：走 codex 自己解码的 frontmatter `description`，
  或者写成纯 ASCII。我们自己在 5.1 上读 UTF-8 文件同理，一律显式写 `-Encoding utf8`。
  反过来加了 `-Encoding utf8` 又会把真正的 GB18030 旧文件读成 U+FFFD ——
  **没有哪个开关两边都对**，所以附件读法改成让随包 Python 按 utf-8-sig→utf-8→gb18030 顺序试。

---

# 如果只挑三件事做

0. **0.1（helper 鉴权 + 去掉 `ACAO:*` + WS 校验 Origin）** —— 若这条复核成立，它是唯一一条
   「不修就不该出包」的。加随机 token 是一天的活。
   > **HEAD 上零改动，而「出包前」这个前提已经作废两次**：剥 Origin 在 `launcher.rs:1969`、
   > `ACAO: *` 在 `launcher.rs:1061/1065`（**另有 3369/3384/3395/3407 四处同样带 `*`**，
   > 原先只点了前两处）、`/shutdown` 的 loopback 检查在 `launcher.rs:988`、
   > `allow_external` 的短路 `||` 现在在 `main.rs:1612`。
   > 桌面上那个 `6edbe43` 的包已经发到同事机器上了，这三条原样跟着走。
   > **2026-08-07 重核后这条更该往上提**，新增三个事实：① `/v1` 代理**不看调用方的
   > `Authorization`**，key 从本地取（`protocol_proxy.rs:686-717`），网页连假 token 都不用带；
   > ② `logout` 不回收本地 key（`auth.rs:1345-1400`），退出登录后口子照开；
   > ③ 未登录的机器上 WS 那条路会去借用户**自己的官方 codex 凭据**（0.1 第 4 条）。
   > 加随机 token 时**必须同时**处理 logout 撤销，否则修一半。
1. **1.3 + 1.1 + 1.1b + 1.2**（`expectsFile` 解耦 / 看门狗重新布防 / stop 本地兜底 / 交付物判据）
   —— 这四条是同一个「任务到底成没成」的闭环，分开改会互相打架。HEAD 上四条**全部未动**：
   `acceptFrame`（`engine.ts:523-527`）仍只清不布防，`error` 分支仍在 `548` 的 `willRetry` 门
   **之前**于 `546` 撤掉看门狗；`stop()`（`engine.ts:1037-1049`）仍只发一发 interrupt，`1044` 的
   1.2s 定时器只复位 `stopping`；`engine.ts:475` 的 `if (files.length) return { files }` 仍短路掉
   `484` 的两条校验；`isSystemNoise`（`engine.ts:389-395`）仍不滤 `.py`，六份 SKILL.md 仍明写
   「把脚本写进产出目录再执行」。
   > **1.3 必须排到最前，而且它比 7-30 那天更容易触发。** `d8fc6a4` / `7e50fb9` / `db96b78`
   > 把「拖个文件进来问它」做成了一等流程（按格式给读法、应用替用户把附件复制进
   > `<产出目录>/上传文件/`），但 `engine.ts:984` 的
   > `expectsFile: !!skillPrefix.trim() || wantsFile(t)` 一个字没改，`Composer.tsx:112`
   > 的 `buildAttachmentPrefix` 对任何附件都返回非空串。
   > **更糟的是这轮顺手堵掉了原来那条误打误撞的逃生口**：`db96b78` 让 `detectDeliverables`
   > 排除 `上传文件/`（`engine.ts:401-403` + `438`），附件前缀又明令模型把脚本写进
   > `$env:TEMP`（`attachments.ts:112`）。于是纯问答轮 `files.length` 必为 0、
   > `expectsFile` 必为 true，**必然翻成 `missing_deliverable`**。
   > 附带一条新矛盾：附件前缀说「不要写进产出目录」，六份 SKILL.md 说「把脚本写进产出目录」
   > —— 两个指令通道现在互相打脸，改 1.2 时得一起统一。
   > **1.1 的第 3 条理由已被纠正**：要治的是 relay **body idle**，不是 header 超时 ——
   > 所以**不要**照 3.1 原来写的去降 header 超时，那打不到靶。
2. **1.6（`status` 落盘）+ 1.5（运行中禁改 cwd）** —— 都是几十行，但一条修掉「重开历史后
   主动断言成功 + 永久失去修复入口」，另一条修掉「整个 App 变砖 + 撤回删用户文件」。
   1.6 要一并改 5 条把错误行为钉死的测试（`app.test.ts:234-238` / `1303-1309` / `1325-1329` /
   `256` / `1518`）。
3. **2.1 + 2.4**（`dbg_log` 加 pid + 把已有的 `LoopbackPortGuard` 接到工作管家上）。
   > **2.4 的理由要换**：不是「争 config.toml / 争端口」（两者都有保护），而是
   > 「实例 1 退出会杀掉实例 2 收养的 helper，且无任何日志归因」。
   > 也不是「缺个 tauri 插件」——manager 和 launcher 都是自建守卫，照抄即可。

~~**2.2**（relay 记 tools）~~ 已作废（4.1 的 31KB 已核销、转换层已排除），改做
**relay 记 model**（2.2 重写版，3.1 的归因就卡在这个字段）+ **2.3**（`finish_reason` /
输出 token / 别再把中断记成 200）。

**出包状态（2026-08-07 重核，替掉原先「!54 不在 fix8 里」那句）**：`main` 已是 `5019d1c`
（= !56），!54（`943392f`）早就在里面，F1/F2/F3 不需要再为出包等。fix8 那个包也已被取代 ——
桌面上现在是 `工作管家_0.1.0_x64-setup_6edbe43-PPT与D盘附件修复.exe`（95.0 MB，2026-07-31），
基于 `6edbe43`，含 !51~!56 **加上三个还没进 main 的 commit**。

**现在的错位方向反了**：`feea75a` / `db96b78` / `6edbe43` 只在分支
`fix/skill-doc-encoding-and-python-channel` 上，**从 `main` 出的包会比同事手里那个更旧**
（缺 PS 5.1 乱码修复和附件暂存）。先把这个分支合掉，再出下一个包。
「每次 commit 后 provenance 校验必挂、要重新暂存 bundle 资源」那条恢复配方仍然有效。

---

# 验证方法论（值得固化）

## 第一轮（2026-07-30，外部审计 23 条）

把外部审计（ChatGPT，23 条）当**假设集**而不是结论集，逐条打到 HEAD 的真实代码上，
每条再派三个不同视角的 refuter（可达性 / 误读 / 定级与设计意图）去驳。**结果分布**：

| 判定 | 条数 | 说明 |
|---|---|---|
| 成立 | 8 | 已并入第零/一/五层 |
| 部分成立（核心真、子论断有错） | 11 | 多数是**领头论断过期**或**归因错层** |
| 不成立 / 设计如此 | 4 | 已进「已证伪」 |

**三条可复用的教训**：

1. **先对齐 baseline，再读结论。** 那份审计基于 `6fbf544`，而当时 HEAD 已是 `943392f`。
   它有多条（如「编程作业 → `task:'excel'`」、`ActiveSkill` 无 id）在当时是对的、现在已修。
   **不核 baseline 就会去修已经修好的东西。**
2. **平台不匹配会制造整类假报障。** 审计跑 macOS，我们发 Windows。随包 Python、office runtime、
   敏感路径校验、`toRel` 的路径分隔符 —— 四处都因平台门控产生了方向错误的结论。
   有的是假报障，有的**反而漏报了更严重的 Windows 专属问题**（如 1.7 第 3 条：
   撤回在 Windows 上其实是空操作）。
3. **「过期的领头论断」最危险。** WA-002 把七条子论断挂在一条早已被 `d2c41c3`（**审计自己基线的
   祖先**，已用 `git merge-base --is-ancestor` 复核）反转掉的行为上，并据此定级 P0。照它改会去
   加固一个已经反转过的判据，而**真正敞开的那条（还原循环无条件覆盖用户编辑）会被漏掉**。
   > 定级也要自己重算：审计给的 3 个 P0 里，1 个（WA-001）是误读、1 个（WA-002）领头论断过期、
   > 1 个（WA-003）方向错 —— 但**它漏掉的那个方向反而更该是 P0**。

## 第二轮（2026-07-31 同事机器 PPT 复现 + 08-07 全文重核）追加四条

4. **同一份代码在不同档位、不同 shell 下是不同的系统。** 「沙箱读写无限制」的硬证据只在
   unelevated 下成立：`elevated` 后端用 `CreateProcessWithLogonW` 以独立本地账户跑命令，
   注入的 env **传不过去**（她机器上 `Get-ChildItem Env:` 里没有 `WA_PYTHON`，而 `config.toml`
   里明明有那个键），沙箱账户对 `C:\Users` 和 profile 根 `icacls` 命中 0。同理 pwsh 7 与
   powershell 5.1 的 ACP 不同（utf-8 vs gb2312），同一份 SKILL.md 本机读出「汇报 PPT 制作」、
   她机器读出「姹囨姤 PPT」。
   **本机 A/B 通过不等于功能可用 —— 每条实测结论都要标注档位与 shell 版本。**
5. **静默失败的加载器必须用探针实测，不能推断。** codex 0.142.5 对 SKILL.md 有四条硬约束
   （BOM / 开头空行 / 非 UTF-8 编码 / 首行不是 `---`，任一命中就把整个 skill **整条丢掉**），
   而 `plugin add` 仍 exit=0、`plugin list` 仍报 enabled、`doctor` 一个字不提。第一版方案
   「给 SKILL.md 加 BOM」**方向完全相反**，是在一次性 `CODEX_HOME` 里端到端跑出来才否掉的。
   门禁已落在 `scripts/skill-docs.test.mjs`，四种致命写法都用变异测试验过真能挂。
6. **复现脚本自己会制造假根因。** 「`\\?\` 前缀导致 PPT 失败」那条是我误报两次 —— bash heredoc
   吞掉了反斜杠，我看到的路径不是代码里的路径。08-07 用 `[char]92` 在 PowerShell 内部拼字符串
   复测才定案。**下结论前先确认自己的观测通道没有污染输入。**
7. **「代码改好了」不等于「用户机器上是新的」。** !55 的根因：`codex plugin marketplace add`
   只更新源指针，批量启用又被一次性 marker `.wa-initialized`（`main.rs:641`）挡着 ——
   一台 marker 早于 `5ab77aa` 一天的机器上，缓存里的 SKILL.md 全是旧的。
   凡是改随包资源的 commit，必须同时回答「已安装的用户怎么拿到它」。
