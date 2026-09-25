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
    AbortController,
    Date,
    Promise,
    console,
    exports: module.exports,
    module,
    require(specifier) {
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

const {
  cancelDemoUpload,
  formatFileSize,
  formatMegabytes,
  getDemoUploadSnapshot,
  startDemoUpload,
  subscribeDemoUpload,
  takeDemoUploadOutcome,
  uploadEtaLabel,
  uploadPercent,
  uploadProgressLabel
} = loadTypeScriptModule("./demo-upload.ts");

const MB = 1024 * 1024;

function deferredUploader() {
  const calls = [];
  const uploader = (file, options) =>
    new Promise((resolvePromise, rejectPromise) => {
      calls.push({ file, options, resolve: resolvePromise, reject: rejectPromise });
      options.signal.addEventListener("abort", () => {
        const error = new Error("Upload cancelled");
        error.name = "AbortError";
        rejectPromise(error);
      });
    });
  return { calls, uploader };
}

{
  assert.equal(formatMegabytes(0), "0");
  assert.equal(formatMegabytes(12.34 * MB), "12.3");
  assert.equal(formatMegabytes(286.4 * MB), "286");
  assert.equal(formatFileSize(1024 * MB), "1 GB");
  assert.equal(formatFileSize(512 * MB), "512 MB");
  assert.equal(uploadPercent({ loaded: 0, total: 0 }), 0);
  assert.equal(uploadPercent({ loaded: 42.9, total: 100 }), 42);
  assert.equal(uploadPercent({ loaded: 120, total: 100 }), 100);
}

{
  // No estimate until a few seconds of throughput exist.
  assert.equal(uploadEtaLabel({ loaded: 10 * MB, total: 100 * MB, startedAt: 0 }, 1000), null);
  assert.equal(uploadEtaLabel({ loaded: 50 * MB, total: 100 * MB, startedAt: 0 }, 120_000), "约 2 分钟");
  assert.equal(uploadEtaLabel({ loaded: 80 * MB, total: 100 * MB, startedAt: 0 }, 40_000), "约 10 秒");
  assert.equal(
    uploadProgressLabel({ fileName: "a.dem", loaded: 120 * MB, total: 286 * MB, startedAt: 0, phase: "sending" }, 60_000),
    "上传中 41%，120 / 286 MB，约 2 分钟"
  );
  assert.equal(
    uploadProgressLabel({ fileName: "a.dem", loaded: 286 * MB, total: 286 * MB, startedAt: 0, phase: "verifying" }, 60_000),
    "上传完成，服务器校验中…"
  );
}

{
  const { calls, uploader } = deferredUploader();
  let notified = 0;
  const unsubscribe = subscribeDemoUpload(() => {
    notified += 1;
  });

  const file = { name: "match.dem", size: 200 };
  const done = startDemoUpload(file, uploader, () => 1000);
  assert.equal(getDemoUploadSnapshot().fileName, "match.dem");
  assert.equal(getDemoUploadSnapshot().phase, "sending");
  assert.equal(notified, 1);

  // A second file waits for the first.
  await assert.rejects(startDemoUpload({ name: "other.dem", size: 1 }, uploader), /already running/);
  assert.equal(calls.length, 1);

  // Progress inside one whole percent is not re-published.
  calls[0].options.onProgress({ loaded: 100, total: 200 });
  calls[0].options.onProgress({ loaded: 101, total: 200 });
  assert.equal(notified, 2);
  assert.equal(getDemoUploadSnapshot().loaded, 100);

  calls[0].options.onProgress({ loaded: 200, total: 200 });
  assert.equal(getDemoUploadSnapshot().phase, "verifying");

  calls[0].resolve({ id: "demo-1" });
  assert.equal((await done).id, "demo-1");
  assert.equal(getDemoUploadSnapshot(), null);
  unsubscribe();
}

{
  const { calls, uploader } = deferredUploader();
  const done = startDemoUpload({ name: "slow.dem", size: 100 }, uploader);

  assert.equal(cancelDemoUpload(), true);
  await assert.rejects(done, (error) => error.name === "AbortError");
  assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(getDemoUploadSnapshot(), null);
  assert.equal(cancelDemoUpload(), false);
}

{
  // A failed upload frees the slot for the next file.
  const { calls, uploader } = deferredUploader();
  const failed = startDemoUpload({ name: "bad.dem", size: 10 }, uploader);
  calls[0].reject(new Error("413"));
  await assert.rejects(failed, /413/);
  assert.equal(getDemoUploadSnapshot(), null);
  const next = startDemoUpload({ name: "good.dem", size: 10 }, uploader);
  calls[1].resolve({ id: "demo-2" });
  assert.equal((await next).id, "demo-2");
}

{
  // The outcome waits for whichever page takes it, once.
  const { calls, uploader } = deferredUploader();
  const rejected = startDemoUpload({ name: "busy.dem", size: 10 }, uploader);
  assert.equal(takeDemoUploadOutcome(), null);
  const refusal = new Error("INTAKE_BUSY");
  calls[0].reject(refusal);
  await assert.rejects(rejected, /INTAKE_BUSY/);
  const failed = takeDemoUploadOutcome();
  assert.equal(failed.kind, "failed");
  assert.equal(failed.fileName, "busy.dem");
  assert.equal(failed.error, refusal);
  assert.equal(takeDemoUploadOutcome(), null);

  const landed = startDemoUpload({ name: "ok.dem", size: 10 }, uploader);
  calls[1].resolve({ id: "demo-3" });
  await landed;
  const outcome = takeDemoUploadOutcome();
  assert.equal(outcome.kind, "landed");
  assert.equal(outcome.demo.id, "demo-3");

  // A new upload drops an outcome nobody took.
  const stale = startDemoUpload({ name: "a.dem", size: 10 }, uploader);
  calls[2].reject(new Error("503"));
  await assert.rejects(stale, /503/);
  const fresh = startDemoUpload({ name: "b.dem", size: 10 }, uploader);
  assert.equal(takeDemoUploadOutcome(), null);
  calls[3].resolve({ id: "demo-4" });
  await fresh;
  assert.equal(takeDemoUploadOutcome().demo.id, "demo-4");
}

console.log("demo upload helpers passed");
