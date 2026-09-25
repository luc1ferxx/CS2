import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import vm from "node:vm";
import ts from "typescript";

import { parserEventReplay } from "./test-fixtures/replay-quality.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const nodeRequire = createRequire(import.meta.url);

function loadTypeScriptModule(relativePath, runtimeImports = {}) {
  const filename = resolve(__dirname, relativePath);
  const source = readFileSync(filename, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      jsx: ts.JsxEmit.ReactJSX,
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
    process,
    require(specifier) {
      if (specifier in runtimeImports) {
        return runtimeImports[specifier];
      }
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

{
  const media = loadTypeScriptModule("./media-url.ts");

  assert.equal(media.resolveMediaUrl("/media/videos/demo-1/clip.mp4"), null);
}

{
  const media = loadTypeScriptModule("./media-url.ts");

  assert.equal(media.resolveMediaUrl("https://cdn.example/clip.mp4"), null);
}

{
  const media = loadTypeScriptModule("./media-url.ts");

  assert.equal(media.resolveMediaUrl("blob:https://app.example/secret"), null);
  assert.equal(media.resolveMediaUrl("data:video/mp4;base64,AAAA"), null);
}

{
  const media = loadTypeScriptModule("./media-url.ts");

  assert.equal(media.resolveMediaUrl("/demos/../media/video"), null);
}

{
  const media = loadTypeScriptModule("./media-url.ts");

  assert.deepEqual(
    { ...media.resolvePrivateMediaSource("/demos/demo-1/media/video") },
    {
      src: "http://localhost:8000/demos/demo-1/media/video",
      crossOrigin: "use-credentials"
    }
  );
  assert.deepEqual(
    { ...media.resolvePrivateMediaSource("/demos/demo-1/render/jobs/job-1/media/video") },
    { src: "http://localhost:8000/demos/demo-1/render/jobs/job-1/media/video", crossOrigin: "use-credentials" }
  );
  for (const invalid of [
    "/demos/demo-1/render/jobs/../media/video",
    "/demos/demo-1/render/jobs/job-1/media/video?url=https://outside.example",
    "/demos/demo-1/render/jobs/%2e%2e/media/video",
    "/demos/demo-1/render/jobs/job-1/manifest"
  ]) assert.equal(media.resolvePrivateMediaSource(invalid), null);
}

{
  const icons = new Proxy(
    {},
    {
      get() {
        return () => null;
      }
    }
  );
  const commonImports = {
    "react/jsx-runtime": nodeRequire("react/jsx-runtime"),
    react: nodeRequire("react"),
    "lucide-react": icons,
    "@/lib/api": {},
    "@/lib/demo-library": { friendlyErrorMessage: (value) => value },
    "@/lib/render-worker": loadTypeScriptModule("./render-worker.ts"),
    "@/types/replay": {}
  };
  const { RenderOperatorPanel } = loadTypeScriptModule(
    "../components/replay/RenderOperatorPanel.tsx",
    commonImports
  );
  const { VideoSetupPanel } = loadTypeScriptModule(
    "../components/replay/VideoSetupPanel.tsx",
    commonImports
  );
  const video = {
    status: "ready",
    url: "/demos/demo-1/media/video?private-reference",
    durationSeconds: 10,
    tickStart: 0,
    tickEnd: 640,
    tickRate: 64,
    source: "rendered",
    timeOriginSeconds: 0
  };
  const operatorMarkup = renderToStaticMarkup(
    React.createElement(RenderOperatorPanel, {
      video,
      latestJob: null,
      jobCount: 0,
      refreshing: false,
      onRefresh() {}
    })
  );
  const setupMarkup = renderToStaticMarkup(
    React.createElement(VideoSetupPanel, {
      currentVideoTime: 0,
      detectedDurationSeconds: null,
      video,
      async onSaveCalibration() {},
      async onUploadVideo() {}
    })
  );

  assert.doesNotMatch(operatorMarkup, /private-reference/);
  assert.doesNotMatch(setupMarkup, /private-reference/);
}

{
  const publicContracts = [
    readFileSync(resolve(__dirname, "../types/demo.ts"), "utf8"),
    readFileSync(resolve(__dirname, "../types/replay.ts"), "utf8"),
    readFileSync(resolve(__dirname, "./api.ts"), "utf8")
  ].join("\n");

  assert.doesNotMatch(publicContracts, /\bowner_id\s*:/);
  assert.doesNotMatch(publicContracts, /\bstorageKey\s*[?:]/);
  assert.doesNotMatch(publicContracts, /\bvideo_url\s*[?:]/);
}

{
  const playerSource = readFileSync(
    resolve(__dirname, "../components/replay/FirstPersonReplay.tsx"),
    "utf8"
  );

  assert.match(playerSource, /crossOrigin=/);
  assert.match(playerSource, /refreshSession/);
  assert.doesNotMatch(playerSource, /createObjectURL|revokeObjectURL|fetch\s*\(/);
}

{
  const { FirstPersonReplay } = loadTypeScriptModule(
    "../components/replay/FirstPersonReplay.tsx",
    {
      "react/jsx-runtime": nodeRequire("react/jsx-runtime"),
      react: nodeRequire("react"),
      "lucide-react": nodeRequire("lucide-react"),
      "@/components/auth/AuthProvider": { useAuth: () => ({ refreshSession: async () => true }) },
      "@/lib/demo-library": loadTypeScriptModule("./demo-library.ts"),
      "@/lib/media-url": loadTypeScriptModule("./media-url.ts"),
      "@/lib/render-worker": loadTypeScriptModule("./render-worker.ts"),
      "@/lib/replay-time": loadTypeScriptModule("./replay-time.ts"),
      "@/lib/user-errors": loadTypeScriptModule("./user-errors.ts", {
        "@/lib/upload-limits": loadTypeScriptModule("./upload-limits.ts")
      })
    }
  );

  const { videoPlaybackState } = loadTypeScriptModule("./replay-time.ts");
  function playerMarkup(videoOverrides = {}, replayOverrides = {}, currentTick = 100, selectedPlayerId = null, playerProps = {}) {
    const fixture = parserEventReplay();
    const replay = {
      ...fixture,
      video: { ...fixture.video, status: "ready", ...videoOverrides },
      ...replayOverrides
    };
    return renderToStaticMarkup(React.createElement(FirstPersonReplay, {
      replay,
      currentTick,
      playing: false,
      speed: 1,
      playbackState: videoPlaybackState(replay.video, currentTick, selectedPlayerId),
      mediaUnavailable: false,
      renderRequesting: false,
      renderClipRequesting: false,
      latestRenderClipJob: null,
      onRequestMockRender() {},
      onRequestRenderClip() {},
      onVideoTickChange() {},
      onVideoUnavailable() {},
      onViewVideoClip() {},
      ...playerProps
    }));
  }

  const replayOnlyMarkup = playerMarkup();
  assert.match(replayOnlyMarkup, /战术回放已就绪/);
  assert.match(replayOnlyMarkup, /尚未生成第一人称视频/);
  assert.doesNotMatch(replayOnlyMarkup, /视频文件尚未就绪/);
  assert.match(replayOnlyMarkup, /生成这一刻的视频/);
  assert.doesNotMatch(replayOnlyMarkup, /模拟视频任务/);
  assert.match(playerMarkup({}, {}, 100, null, { showDevActions: true }), /模拟视频任务/);
  // Without a clip handler (render clips off) the player offers no clip button at all.
  const noClipMarkup = playerMarkup({}, {}, 100, null, { onRequestRenderClip: undefined });
  assert.doesNotMatch(noClipMarkup, /生成这一刻的视频|观看这一刻/);
  assert.match(noClipMarkup, /战术回放已就绪/);

  // A queued clip with nothing to claim it used to sit on "等待生成" forever.
  const queuedClipJob = { job_id: "queued-job", status: "queued" };
  const offlineWorker = {
    mode: "external", required: true, connected: false, status: "offline",
    last_seen_at: "2026-09-16T12:00:00+00:00", age_seconds: 600, busy_rendering: false
  };
  const offlineClipMarkup = playerMarkup({}, {}, 100, null, {
    currentTickClipJob: queuedClipJob, renderWorker: offlineWorker
  });
  // Player copy: no renderer or polling jargon, just that the service is offline and the job waits.
  assert.match(offlineClipMarkup, /视频服务暂时离线<\/button>/);
  assert.match(offlineClipMarkup, /已排队，服务恢复后会自动开始生成。/);
  assert.doesNotMatch(offlineClipMarkup, /渲染器|轮询/);
  assert.doesNotMatch(offlineClipMarkup, /等待生成<\/button>/);
  const connectedClipMarkup = playerMarkup({}, {}, 100, null, {
    currentTickClipJob: queuedClipJob, renderWorker: { ...offlineWorker, connected: true, status: "connected" }
  });
  assert.match(connectedClipMarkup, /等待生成<\/button>/);
  assert.doesNotMatch(connectedClipMarkup, /视频服务暂时离线/);
  assert.doesNotMatch(
    playerMarkup({}, {}, 100, null, { currentTickClipJob: queuedClipJob }),
    /视频服务暂时离线/
  );

  const noFramesMarkup = playerMarkup({}, { frames: [] });
  assert.match(noFramesMarkup, /暂无回放视频/);
  assert.match(noFramesMarkup, /该比赛暂未提供可用的位置数据/);
  assert.doesNotMatch(noFramesMarkup, /战术回放已就绪/);

  for (const source of ["rendered", "manual_upload"]) {
    const missingMediaMarkup = playerMarkup({ source });
    assert.match(missingMediaMarkup, /视频文件尚未就绪/);
    assert.match(missingMediaMarkup, /任务已结束/);
    assert.doesNotMatch(missingMediaMarkup, /尚未生成第一人称视频/);
  }

  const queuedMarkup = playerMarkup({ status: "queued" });
  assert.match(queuedMarkup, /视频等待生成/);
  assert.doesNotMatch(queuedMarkup, /战术回放已就绪/);

  const playableMarkup = playerMarkup({
    source: "rendered", url: "/demos/demo-1/media/video",
    tickStart: 0, tickEnd: 640, durationSeconds: 10
  });
  assert.match(playableMarkup, /<video/);
  assert.doesNotMatch(playableMarkup, /render-status-overlay/);
  assert.match(playableMarkup, /玩家视角未确认/);

  const xelexId = "76561198998266210";
  const clip = {
    source: "rendered", url: "/demos/demo-1/media/video", povSteamId: xelexId,
    tickStart: 6363, tickEnd: 7643, tickRate: 64, durationSeconds: 20
  };
  const players = [{ id: xelexId, name: "xelex", side: "T", color: "#fff" }];
  const xelexClipMarkup = playerMarkup(clip, { players }, 6683, xelexId);
  assert.match(xelexClipMarkup, /<video/);
  assert.match(xelexClipMarkup, /xelex 的视角/);
  assert.match(xelexClipMarkup, /观看 xelex 视频/);
  assert.match(xelexClipMarkup, /0:05/);
  const compactMarkup = playerMarkup(clip, { players }, 6683, xelexId, { compact: true });
  assert.match(compactMarkup, /<video/);
  assert.doesNotMatch(compactMarkup, /first-person-header|模拟视频任务|生成这一刻的视频/);
  assert.doesNotMatch(compactMarkup, /视角未确认/);
  const unverifiedCompactMarkup = playerMarkup({ ...clip, source: "manual_upload", povSteamId: null }, { players }, 6683, xelexId, { compact: true });
  assert.match(unverifiedCompactMarkup, /<video/);
  assert.match(unverifiedCompactMarkup, /class="hud-chip">视角未确认/);
  assert.doesNotMatch(unverifiedCompactMarkup, /first-person-header/);
  for (const tick of [6362, 7643, 20000]) {
    const outsideClipMarkup = playerMarkup(clip, { players }, tick, xelexId);
    assert.doesNotMatch(outsideClipMarkup, /<video/);
    assert.match(outsideClipMarkup, /当前时刻不在视频范围内/);
    assert.match(outsideClipMarkup, /战术回放/);
    assert.match(outsideClipMarkup, /观看 xelex 视频/);
  }

  const invalidMediaMarkup = playerMarkup({ source: "rendered", url: "https://cdn.example/clip.mp4" });
  assert.match(invalidMediaMarkup, /未能加载这段视频/);
  assert.doesNotMatch(invalidMediaMarkup, /战术回放已就绪/);
}
