import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadTypeScriptModule(relativePath) {
  const filename = resolve(__dirname, relativePath);
  const source = readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: filename
  });
  const module = { exports: {} };
  const context = {
    console,
    exports: module.exports,
    module,
    require(specifier) {
      if (specifier === "@/types/steam") {
        return {};
      }
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

const {
  buildSteamConnectionDisplay,
  buildSteamMatchDisplay,
  steamConnectionStatusLabel,
  steamMatchImportAction,
  steamMatchStatusLabel,
  steamImportErrorMessage,
  steamRequestErrorMessage,
  steamSyncResultMessage
} = loadTypeScriptModule("./steam-matches.ts");

const statuses = {
  discovered: "已发现",
  demo_pending: "等待获取",
  downloading: "下载中",
  parsing: "读取比赛中",
  ready: "可以复盘",
  unavailable: "无法导入"
};

for (const [status, label] of Object.entries(statuses)) {
  assert.equal(steamMatchStatusLabel(status), label);
}

assert.equal(
  steamConnectionStatusLabel(connection({ connected: false, status: "disconnected" })),
  "未连接"
);
assert.equal(
  steamConnectionStatusLabel(connection({ credentials_configured: false })),
  "需要设置"
);
assert.equal(steamConnectionStatusLabel(connection({ status: "syncing" })), "同步中");
assert.equal(steamConnectionStatusLabel(connection({ status: "caught_up" })), "已是最新");
assert.equal(
  steamConnectionStatusLabel(connection({ status: "retry_wait", next_retry_at: "2026-07-19T13:00:00Z" })),
  "等待重试"
);
assert.equal(
  steamConnectionStatusLabel(connection({ status: "authorization_required" })),
  "需要处理"
);

{
  const secretAuthCode = "AAAA-SECRET-CODE";
  const secretSharingCode = "CSGO-secret-sharing-code";
  const display = buildSteamConnectionDisplay({
    ...connection({
      last_error_code: "steam_credentials_invalid",
      last_error_message: `Rejected ${secretAuthCode} ${secretSharingCode}`
    }),
    game_auth_code: secretAuthCode,
    initial_match_sharing_code: secretSharingCode
  });
  const serialized = JSON.stringify(display);

  assert.equal(serialized.includes(secretAuthCode), false);
  assert.equal(serialized.includes(secretSharingCode), false);
  assert.equal(serialized.includes("last_error_message"), false);
  assert.equal(display.errorMessage, "Steam 拒绝了已保存的授权，请更换两个授权码后再同步。");
}

assert.equal(
  buildSteamConnectionDisplay(
    connection({ last_error_code: "steam_cursor_invalid", status: "authorization_required" })
  ).errorMessage,
  "请使用这个 Steam 账号最近一场比赛的分享代码，然后再同步。"
);

{
  const secretSharingCode = "CSGO-secret-sharing-code";
  const display = buildSteamMatchDisplay({
    id: "match-1",
    status: "discovered",
    source: "steam_match_history",
    discovered_at: "2026-07-19T12:00:00Z",
    updated_at: "2026-07-19T12:00:00Z",
    demo_id: null,
    match_sharing_code: secretSharingCode,
    map_name: "de_fake",
    score: "13-0"
  });
  const serialized = JSON.stringify(display);

  assert.equal(display.sourceLabel, "Steam 比赛记录");
  assert.equal(display.demoHref, null);
  assert.equal(serialized.includes(secretSharingCode), false);
  assert.equal(serialized.includes("de_fake"), false);
  assert.equal(serialized.includes("13-0"), false);
}

{
  const display = buildSteamMatchDisplay({
    id: "match-2",
    status: "ready",
    source: "steam_match_history",
    discovered_at: "2026-07-19T12:00:00Z",
    updated_at: "2026-07-19T12:30:00Z",
    demo_id: "demo-2",
    provider_id: "licensed-partner",
    map_name: "de_mirage",
    duration_seconds: 1875,
    ct_round_wins: 13,
    t_round_wins: 10,
    players: ["Player One", "Player Two"],
    import_error_code: null,
    import_error_message: null,
    import_retryable: false,
    parser_dispatch_pending: false,
    manual_upload_supported: true
  });

  assert.equal(display.statusLabel, "可以复盘");
  assert.equal(display.demoHref, "/demos/demo-2");
  assert.equal(display.mapName, "de_mirage");
  assert.equal(display.durationLabel, "31 分 15 秒");
  assert.equal(display.sideRoundsLabel, "CT 13 比 T 10");
  assert.equal(display.playersLabel, "Player One、Player Two");
  assert.equal(display.errorMessage, null);
}

{
  const display = buildSteamMatchDisplay({
    id: "match-players",
    status: "ready",
    source: "steam_match_history",
    discovered_at: "2026-07-19T12:00:00Z",
    updated_at: "2026-07-19T12:30:00Z",
    demo_id: "demo-players",
    provider_id: "licensed-partner",
    map_name: "de_nuke",
    duration_seconds: null,
    ct_round_wins: null,
    t_round_wins: null,
    players: ["One", "Two", "Three", "Four", "Five"],
    import_error_code: null,
    import_error_message: null,
    import_retryable: false,
    parser_dispatch_pending: false,
    manual_upload_supported: true
  });

  assert.equal(display.playersLabel, "One、Two、Three 等 5 人");
}

{
  const display = buildSteamMatchDisplay({
    id: "match-3",
    status: "unavailable",
    source: "steam_match_history",
    discovered_at: "2026-07-19T12:00:00Z",
    updated_at: "2026-07-19T12:30:00Z",
    demo_id: null,
    provider_id: null,
    map_name: null,
    duration_seconds: null,
    ct_round_wins: null,
    t_round_wins: null,
    players: null,
    import_error_code: "demo_source_unavailable",
    import_error_message: "No licensed Demo source is configured.",
    import_retryable: false,
    parser_dispatch_pending: false,
    manual_upload_supported: true
  });

  assert.equal(display.mapName, null);
  assert.equal(display.sideRoundsLabel, null);
  // The backend's English operator text never reaches the player; the code picks the copy.
  assert.equal(display.errorMessage, "当前版本还不能从 Steam 自动下载比赛录像。同步只会列出你的比赛记录；要复盘，请手动上传这场比赛的 .dem 文件。");
  assert.equal(display.manualUploadSupported, true);
}

assert.equal(
  JSON.stringify(steamMatchImportAction("discovered", null, true)),
  JSON.stringify({ enabled: true, label: "导入比赛" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("unavailable", null, true)),
  JSON.stringify({ enabled: true, label: "重新导入" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, true)),
  JSON.stringify({ enabled: false, label: "下载中" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, true, null, false, true)),
  JSON.stringify({ enabled: true, label: "重新导入" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("demo_pending", null, true, null, false, true)),
  JSON.stringify({ enabled: true, label: "重新导入" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, false, null, false, true)),
  JSON.stringify({ enabled: false, label: "手动上传 .dem" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("parsing", "demo-2", true)),
  JSON.stringify({ enabled: false, label: "读取比赛中" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("parsing", "demo-2", true, null, true)),
  JSON.stringify({ enabled: true, label: "重新处理" })
);
assert.equal(
  JSON.stringify(
    steamMatchImportAction("unavailable", "demo-2", false, "parser_dispatch_unavailable")
  ),
  JSON.stringify({ enabled: true, label: "重新处理" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("unavailable", "demo-2", true, "parser_failed")),
  JSON.stringify({ enabled: false, label: "无法导入" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("ready", "demo-2", true)),
  JSON.stringify({ enabled: false, label: "可以复盘" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("discovered", null, false)),
  JSON.stringify({ enabled: false, label: "手动上传 .dem" })
);

{
  const display = buildSteamConnectionDisplay(
    connection({
      demo_import_available: false,
      demo_source_provider: "disabled",
      manual_upload_supported: true
    })
  );

  assert.equal(display.demoImportAvailable, false);
  assert.equal(display.demoSourceProvider, "disabled");
  assert.equal(display.manualUploadSupported, true);
  assert.equal(display.demoImportMessage, "当前版本还不能从 Steam 自动下载比赛录像。同步只会列出你的比赛记录；要复盘，请手动上传这场比赛的 .dem 文件。");
}

{
  const display = buildSteamConnectionDisplay(
    connection({
      demo_import_available: true,
      demo_source_provider: "licensed-partner",
      manual_upload_supported: true
    })
  );

  assert.equal(display.demoImportAvailable, true);
  assert.equal(display.demoSourceProvider, "licensed-partner");
  assert.equal(display.demoImportMessage, null);
}

assert.equal(
  steamSyncResultMessage({
    discovered_count: 0,
    caught_up: true,
    limit_reached: false,
    status: "caught_up"
  }),
  "没有新比赛，Steam 比赛记录已是最新。"
);
assert.equal(
  steamSyncResultMessage({
    discovered_count: 20,
    caught_up: false,
    limit_reached: true,
    status: "connected"
  }),
  "发现 20 场新比赛。本次同步已达上限，再同步一次可以继续。"
);

assert.equal(
  steamRequestErrorMessage(409, "sync", "steam_cursor_invalid"),
  "请使用这个 Steam 账号最近一场比赛的分享代码，然后再同步。"
);
assert.equal(
  steamRequestErrorMessage(409, "sync", "steam_authorization_invalid"),
  "Steam 拒绝了已保存的授权，请更换两个授权码后再同步。"
);
assert.equal(
  steamRequestErrorMessage(403, "sync"),
  // No operator advice about request origins.
  "同步 Steam 比赛记录失败，请稍后再试。"
);
assert.equal(
  steamRequestErrorMessage(503, "sync"),
  "Steam 比赛记录暂时无法访问或请求过于频繁，请稍后再试。"
);
assert.equal(
  steamRequestErrorMessage(409, "import", "demo_source_unavailable"),
  "当前版本还不能从 Steam 自动下载比赛录像。同步只会列出你的比赛记录；要复盘，请手动上传这场比赛的 .dem 文件。"
);
assert.equal(
  steamRequestErrorMessage(409, "import", "demo_import_in_progress"),
  "这场比赛正在导入，请刷新查看最新状态。"
);
assert.equal(
  steamRequestErrorMessage(502, "import", "demo_download_too_large"),
  "暂时无法自动导入这场比赛，可以手动上传 .dem 文件。"
);
assert.equal(steamRequestErrorMessage(401, "load"), "登录已过期，请重新登录后继续。");
assert.equal(steamImportErrorMessage(null), null);
assert.equal(steamImportErrorMessage("parser_dispatch_unavailable"), "比赛已下载，但还没能开始处理，可以重新处理。");
assert.equal(steamImportErrorMessage("parser_failed"), "比赛录像无法读取，请手动上传这场比赛的 .dem 文件。");
assert.equal(steamImportErrorMessage("demo_download_failed"), "暂时无法自动导入这场比赛，可以手动上传 .dem 文件。");

{
  // Every string the panel shows from these helpers is Chinese.
  const english = /[A-Za-z]{4,}/;
  const texts = [
    ...Object.keys(statuses).map((status) => steamMatchStatusLabel(status)),
    ...["load", "connect", "sync", "disconnect", "import"].flatMap((action) =>
      [null, 403, 404, 409, 412, 429, 500, 502, 503].map((status) => steamRequestErrorMessage(status, action))
    ),
    ...["steam_credentials_invalid", "steam_cursor_invalid", "rate_limited", "unavailable", "other"].map(
      (code) => buildSteamConnectionDisplay(connection({ last_error_code: code })).errorMessage
    )
  ];
  for (const text of texts) {
    assert.doesNotMatch(text.replace(/Steam|CT|\.dem/g, ""), english, text);
  }
}

console.log("steam match helpers passed");

function connection(overrides = {}) {
  return {
    connected: true,
    status: "connected",
    credentials_configured: true,
    scheduled_sync_enabled: false,
    last_sync_started_at: null,
    last_sync_completed_at: null,
    next_retry_at: null,
    last_error_code: null,
    last_error_message: null,
    demo_import_available: false,
    demo_source_provider: "disabled",
    manual_upload_supported: true,
    ...overrides
  };
}
