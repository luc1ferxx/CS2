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
  UPLOAD_RESUME_KEY,
  cancelDemoUpload,
  clearUploadResumeRecord,
  formatFileSize,
  formatMegabytes,
  getDemoUploadSnapshot,
  readUploadResumeRecord,
  startDemoUpload,
  subscribeDemoUpload,
  takeDemoUploadOutcome,
  uploadBusyLabel,
  uploadEtaLabel,
  uploadHoldLabel,
  uploadPercent,
  uploadProgressLabel,
  uploadStateLabel,
  writeUploadResumeRecord
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
  // Nothing is sent yet: the file is being checked and the session opened.
  assert.equal(getDemoUploadSnapshot().phase, "preparing");
  assert.equal(notified, 1);

  // A second file waits for the first.
  await assert.rejects(startDemoUpload({ name: "other.dem", size: 1 }, uploader), /already running/);
  assert.equal(calls.length, 1);

  // Progress inside one whole percent is not re-published; bytes alone read as sending.
  calls[0].options.onProgress({ loaded: 100, total: 200 });
  calls[0].options.onProgress({ loaded: 101, total: 200 });
  assert.equal(notified, 2);
  assert.equal(getDemoUploadSnapshot().loaded, 100);
  assert.equal(getDemoUploadSnapshot().phase, "sending");

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

{
  // An uploader that reports its status owns the phase, and the pause and wait reasons ride along.
  const { calls, uploader } = deferredUploader();
  let clock = 1000;
  const done = startDemoUpload({ name: "big.dem", size: 1000 }, uploader, () => clock, { resumeSessionId: "a".repeat(32) });
  const options = calls[0].options;
  assert.equal(options.resumeSessionId, "a".repeat(32));
  assert.equal(options.replace, undefined);

  options.onStatus({ phase: "preparing" });
  // A resumed upload reports the bytes already on the server before it sends.
  options.onProgress({ loaded: 600, total: 1000 });
  assert.equal(getDemoUploadSnapshot().phase, "preparing");
  assert.equal(getDemoUploadSnapshot().loaded, 600);

  clock = 5000;
  options.onStatus({ phase: "sending" });
  const sending = getDemoUploadSnapshot();
  assert.equal(sending.phase, "sending");
  // The estimate's window starts here, at the resumed bytes, not at zero.
  assert.equal(sending.startedAt, 5000);
  assert.equal(sending.startedLoaded, 600);
  assert.equal(uploadEtaLabel({ ...sending, loaded: 800 }, 25_000), "约 20 秒");

  options.onStatus({ phase: "paused", reason: "offline" });
  assert.equal(getDemoUploadSnapshot().phase, "paused");
  assert.equal(getDemoUploadSnapshot().pauseReason, "offline");
  // Bytes alone never move a status-reporting upload out of its phase.
  options.onProgress({ loaded: 999, total: 1000 });
  options.onProgress({ loaded: 1000, total: 1000 });
  assert.equal(getDemoUploadSnapshot().phase, "paused");

  clock = 60_000;
  options.onStatus({ phase: "sending" });
  assert.equal(getDemoUploadSnapshot().startedAt, 60_000);
  assert.equal(getDemoUploadSnapshot().pauseReason, null);

  options.onStatus({ phase: "waiting", reason: "quota", code: "active_parse_limit", retryAfterSeconds: 60 });
  assert.equal(getDemoUploadSnapshot().phase, "waiting");
  assert.equal(getDemoUploadSnapshot().waitCode, "active_parse_limit");

  options.onStatus({ phase: "verifying" });
  assert.equal(getDemoUploadSnapshot().phase, "verifying");
  calls[0].resolve({ id: "demo-9" });
  await done;
  takeDemoUploadOutcome();
}

{
  // A failed outcome carries what was uploaded and how, for a relaunch with replace.
  const { calls, uploader } = deferredUploader();
  const file = { name: "other.dem", size: 10 };
  const failed = startDemoUpload(file, uploader, Date.now, { replace: true });
  assert.equal(calls[0].options.replace, true);
  calls[0].reject(new Error("409"));
  await assert.rejects(failed, /409/);
  const settled = takeDemoUploadOutcome();
  assert.equal(settled.kind, "failed");
  assert.equal(settled.source, file);
  assert.equal(settled.request.replace, true);
}

{
  const snapshot = (overrides) => ({
    fileName: "a.dem",
    loaded: 62 * MB,
    total: 100 * MB,
    startedAt: 0,
    phase: "sending",
    ...overrides
  });
  assert.equal(uploadBusyLabel(snapshot({ phase: "preparing" })), "准备中…");
  assert.equal(uploadBusyLabel(snapshot()), "上传中 62%");
  assert.equal(uploadBusyLabel(snapshot({ phase: "paused", pauseReason: "offline" })), "已暂停 62%");
  assert.equal(uploadBusyLabel(snapshot({ phase: "verifying" })), "校验中…");
  assert.equal(uploadBusyLabel(snapshot({ phase: "waiting" })), "排队中…");

  assert.equal(uploadStateLabel(snapshot({ phase: "paused", pauseReason: "reauth" })), "已暂停");
  assert.equal(uploadStateLabel(snapshot({ phase: "paused", pauseReason: "retrying" })), "重试中");
  assert.equal(uploadStateLabel(snapshot({ phase: "waiting" })), "排队中");

  assert.equal(uploadHoldLabel(snapshot()), null);
  assert.equal(uploadHoldLabel(snapshot({ phase: "paused", pauseReason: "offline" })), "网络已断开，恢复连接后会自动继续");
  assert.match(uploadHoldLabel(snapshot({ phase: "paused", pauseReason: "reauth" })), /登录已过期.*重新登录/);
  assert.match(uploadHoldLabel(snapshot({ phase: "paused", pauseReason: "retrying" })), /自动重试/);
  assert.match(uploadHoldLabel(snapshot({ phase: "waiting", waitCode: "active_parse_limit" })), /等当前比赛处理完/);
  assert.match(uploadHoldLabel(snapshot({ phase: "waiting", waitCode: "parse_queue_full" })), /处理队列已满/);

  assert.equal(
    uploadProgressLabel(snapshot({ phase: "paused", pauseReason: "offline" }), 60_000),
    "网络已断开，恢复连接后会自动继续，已传 62%，62.0 / 100 MB"
  );
  assert.equal(uploadProgressLabel(snapshot({ phase: "preparing" }), 0), "正在准备上传…");
}

{
  // The resume record: one key, shape-checked, and storage that throws is never fatal.
  const store = new Map();
  const storage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key)
  };
  const record = { sessionId: "0123456789abcdef0123456789abcdef", name: "match.dem", size: 1234, lastModified: 99 };
  assert.equal(readUploadResumeRecord(() => storage), null);
  writeUploadResumeRecord(() => storage, { ...record, extra: "dropped" });
  assert.deepEqual(JSON.parse(store.get(UPLOAD_RESUME_KEY)), record);
  assert.deepEqual({ ...readUploadResumeRecord(() => storage) }, record);

  store.set(UPLOAD_RESUME_KEY, JSON.stringify({ ...record, sessionId: "../../etc" }));
  assert.equal(readUploadResumeRecord(() => storage), null);
  store.set(UPLOAD_RESUME_KEY, "{not json");
  assert.equal(readUploadResumeRecord(() => storage), null);

  clearUploadResumeRecord(() => storage);
  assert.equal(store.has(UPLOAD_RESUME_KEY), false);

  const throwing = () => {
    throw new Error("SecurityError");
  };
  assert.equal(readUploadResumeRecord(throwing), null);
  writeUploadResumeRecord(throwing, record);
  clearUploadResumeRecord(throwing);
  assert.equal(readUploadResumeRecord(() => null), null);
}

console.log("demo upload helpers passed");
