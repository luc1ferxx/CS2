import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import vm from "node:vm";
import ts from "typescript";

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
