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
  steamRequestErrorMessage,
  steamSyncResultMessage
} = loadTypeScriptModule("./steam-matches.ts");

const statuses = {
  discovered: "Discovered",
  demo_pending: "Demo pending",
  downloading: "Downloading",
  parsing: "Parsing",
  ready: "Ready",
  unavailable: "Unavailable"
};

for (const [status, label] of Object.entries(statuses)) {
  assert.equal(steamMatchStatusLabel(status), label);
}

assert.equal(
  steamConnectionStatusLabel(connection({ connected: false, status: "disconnected" })),
  "Not connected"
);
assert.equal(
  steamConnectionStatusLabel(connection({ credentials_configured: false })),
  "Setup required"
);
assert.equal(steamConnectionStatusLabel(connection({ status: "syncing" })), "Syncing");
assert.equal(steamConnectionStatusLabel(connection({ status: "caught_up" })), "Up to date");
assert.equal(
  steamConnectionStatusLabel(connection({ status: "retry_wait", next_retry_at: "2026-07-19T13:00:00Z" })),
  "Waiting to retry"
);
assert.equal(
  steamConnectionStatusLabel(connection({ status: "authorization_required" })),
  "Needs attention"
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
  assert.equal(
    display.errorMessage,
    "Steam rejected the saved authorization. Replace both codes before syncing again."
  );
}

assert.equal(
  buildSteamConnectionDisplay(
    connection({ last_error_code: "steam_cursor_invalid", status: "authorization_required" })
  ).errorMessage,
  "Use a recent match sharing code from this Steam account before syncing again."
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

  assert.equal(display.sourceLabel, "Steam match history");
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

  assert.equal(display.statusLabel, "Ready");
  assert.equal(display.demoHref, "/demos/demo-2");
  assert.equal(display.mapName, "de_mirage");
  assert.equal(display.durationLabel, "31m 15s");
  assert.equal(display.sideRoundsLabel, "CT 13 · T 10");
  assert.equal(display.playersLabel, "Player One, Player Two");
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

  assert.equal(display.playersLabel, "One, Two, Three +2");
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
  assert.equal(display.errorMessage, "No licensed Demo source is configured.");
  assert.equal(display.manualUploadSupported, true);
}

assert.equal(
  JSON.stringify(steamMatchImportAction("discovered", null, true)),
  JSON.stringify({ enabled: true, label: "Import Demo" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("unavailable", null, true)),
  JSON.stringify({ enabled: true, label: "Retry import" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, true)),
  JSON.stringify({ enabled: false, label: "Downloading" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, true, null, false, true)),
  JSON.stringify({ enabled: true, label: "Retry import" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("demo_pending", null, true, null, false, true)),
  JSON.stringify({ enabled: true, label: "Retry import" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("downloading", null, false, null, false, true)),
  JSON.stringify({ enabled: false, label: "Manual upload" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("parsing", "demo-2", true)),
  JSON.stringify({ enabled: false, label: "Parsing" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("parsing", "demo-2", true, null, true)),
  JSON.stringify({ enabled: true, label: "Retry parser" })
);
assert.equal(
  JSON.stringify(
    steamMatchImportAction("unavailable", "demo-2", false, "parser_dispatch_unavailable")
  ),
  JSON.stringify({ enabled: true, label: "Retry parser" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("unavailable", "demo-2", true, "parser_failed")),
  JSON.stringify({ enabled: false, label: "Unavailable" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("ready", "demo-2", true)),
  JSON.stringify({ enabled: false, label: "Ready" })
);
assert.equal(
  JSON.stringify(steamMatchImportAction("discovered", null, false)),
  JSON.stringify({ enabled: false, label: "Manual upload" })
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
  assert.equal(
    display.demoImportMessage,
    "No licensed automatic Demo provider is configured. Use manual .dem upload."
  );
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
  "No new matches. Steam match history is up to date."
);
assert.equal(
  steamSyncResultMessage({
    discovered_count: 20,
    caught_up: false,
    limit_reached: true,
    status: "connected"
  }),
  "Discovered 20 new matches. The sync limit was reached; sync again to continue."
);

assert.equal(
  steamRequestErrorMessage(409, "sync", "steam_cursor_invalid"),
  "Use a recent match sharing code from this Steam account before syncing again."
);
assert.equal(
  steamRequestErrorMessage(409, "sync", "steam_authorization_invalid"),
  "Steam rejected the saved authorization. Replace both codes before syncing again."
);
assert.equal(
  steamRequestErrorMessage(403, "sync"),
  "This request origin was rejected. Refresh the configured app origin and try again."
);
assert.equal(
  steamRequestErrorMessage(503, "sync"),
  "Steam match history is temporarily rate-limited or unavailable. Retry later."
);
assert.equal(
  steamRequestErrorMessage(409, "import", "demo_source_unavailable"),
  "No licensed automatic Demo provider is configured. Use manual .dem upload."
);
assert.equal(
  steamRequestErrorMessage(409, "import", "demo_import_in_progress"),
  "This Demo import is already running. Refresh the match status."
);
assert.equal(
  steamRequestErrorMessage(502, "import", "demo_download_too_large"),
  "The Demo provider response failed secure download checks. Use manual .dem upload or retry later."
);

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
