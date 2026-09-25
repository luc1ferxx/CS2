import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadTypeScriptModule(relativePath, imports = {}) {
  const filename = resolve(__dirname, relativePath);
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, {
    module,
    exports: module.exports,
    require(specifier) {
      if (imports[specifier]) return imports[specifier];
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  }, { filename });
  return module.exports;
}

const {
  NETWORK_ERROR_MESSAGE,
  NOT_FOUND_ERROR_MESSAGE,
  SERVER_ERROR_MESSAGE,
  renderFailureMessage,
  requestFailureKind,
  userFacingError
} = loadTypeScriptModule("./user-errors.ts", {
  "@/lib/upload-limits": loadTypeScriptModule("./upload-limits.ts")
});

// Shaped like ApiError without importing the API client.
function apiError(status, message = "", detailCode = null, retryAfterSeconds = null) {
  return Object.assign(new Error(message || `Request failed with ${status}`), { status, detailCode, retryAfterSeconds });
}

{
  // Browsers word a dropped connection differently; all of them read as offline.
  for (const message of ["Failed to fetch", "NetworkError when attempting to fetch resource.", "Load failed"]) {
    assert.equal(requestFailureKind(new TypeError(message)), "network", message);
    assert.equal(userFacingError(new TypeError(message), "fallback"), NETWORK_ERROR_MESSAGE);
  }
  assert.doesNotMatch(NETWORK_ERROR_MESSAGE, /应用已启动|diagnostics|Redis/,
    "hosted players are never told to start an app or check operator tooling");
}

{
  assert.equal(userFacingError(apiError(500), "fallback"), SERVER_ERROR_MESSAGE);
  assert.equal(userFacingError(apiError(502, "Bad gateway"), "fallback"), SERVER_ERROR_MESSAGE);
  assert.equal(userFacingError(apiError(404, "Demo not found"), "fallback"), NOT_FOUND_ERROR_MESSAGE);
  assert.equal(requestFailureKind(apiError(404)), "not_found");
}

{
  // Backend English detail never reaches the player; the action's own copy does.
  const conflict = apiError(409, "Demo parse must complete before rendering");
  assert.equal(userFacingError(conflict, "生成视频失败，请重试。"), "生成视频失败，请重试。");
  assert.equal(
    userFacingError(conflict, "生成视频失败，请重试。", { conflict: "比赛处理完成后才能生成视频。" }),
    "比赛处理完成后才能生成视频。"
  );
  assert.equal(userFacingError(apiError(400, "Coaching event not found"), "保存评价失败，请重试。"), "保存评价失败，请重试。");
  assert.equal(userFacingError("not an error", "fallback"), "fallback");
}

{
  // Quota rejections keep their exact wording, ahead of every other mapping.
  assert.equal(
    userFacingError(apiError(429, "Daily upload limit reached.", "upload_daily_limit", 5400), "fallback"),
    "今天的上传次数已用完，约 1 小时 30 分钟 后可以继续上传。"
  );
  assert.equal(
    userFacingError(apiError(503, "Parse queue is full.", "parse_queue_full", 60), "fallback"),
    "服务繁忙，处理队列已满，请稍后再试。"
  );
  assert.equal(userFacingError(apiError(413, "Upload too large"), "fallback"), "文件超过上传大小限制，请选择较小的 .dem 文件。");
  assert.equal(userFacingError(apiError(400, "Only .dem files are supported"), "fallback"), "请选择有效的 .dem 比赛文件后重试。");
}

{
  assert.equal(renderFailureMessage("RENDER_WORKER_UNAVAILABLE"), "视频生成服务暂时不可用，请稍后重试。");
  assert.equal(renderFailureMessage("RENDER_TIMED_OUT"), "生成视频超时，请重试。");
  assert.equal(renderFailureMessage("RENDER_QUEUE_TIMED_OUT"), "等待生成的时间过长，请稍后重试。");
  assert.equal(renderFailureMessage("SOMETHING_NEW"), "视频生成失败，请重试。");
  assert.equal(renderFailureMessage(null), "视频生成失败，请重试。");
}

console.log("user-errors tests passed");
