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
    demo_id: "demo-2"
  });

  assert.equal(display.statusLabel, "Ready");
  assert.equal(display.demoHref, "/demos/demo-2");
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
    ...overrides
  };
}
