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
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

const { uploadLimitMessage } = loadTypeScriptModule("./upload-limits.ts");

{
  // The daily wait rounds up to whole minutes and drops a zero hour or minute part.
  const daily = (seconds) => uploadLimitMessage(429, "upload_daily_limit", seconds);
  assert.equal(daily(1), "今天的上传次数已用完，约 1 分钟 后可以继续上传。");
  assert.equal(daily(60), "今天的上传次数已用完，约 1 分钟 后可以继续上传。");
  assert.equal(daily(61), "今天的上传次数已用完，约 2 分钟 后可以继续上传。");
  assert.equal(daily(3540), "今天的上传次数已用完，约 59 分钟 后可以继续上传。");
  assert.equal(daily(3541), "今天的上传次数已用完，约 1 小时 后可以继续上传。");
  assert.equal(daily(3600), "今天的上传次数已用完，约 1 小时 后可以继续上传。");
  assert.equal(daily(3601), "今天的上传次数已用完，约 1 小时 1 分钟 后可以继续上传。");
  assert.equal(daily(5 * 3600 + 30 * 60), "今天的上传次数已用完，约 5 小时 30 分钟 后可以继续上传。");
  assert.equal(daily(86400), "今天的上传次数已用完，约 24 小时 后可以继续上传。");
}

{
  // Without a usable wait the copy still names the limit instead of a file problem.
  for (const seconds of [null, undefined, 0, -5, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(
      uploadLimitMessage(429, "upload_daily_limit", seconds),
      "今天的上传次数已用完，请稍后再继续上传。"
    );
  }
}

{
  assert.equal(
    uploadLimitMessage(429, "active_parse_limit", 60),
    "已有比赛正在处理，请等当前比赛处理完成后再上传。"
  );
  assert.equal(
    uploadLimitMessage(503, "parse_queue_full", 60),
    "服务繁忙，处理队列已满，请稍后再试。"
  );
  assert.equal(
    uploadLimitMessage(503, "upload_quota_unavailable", 30),
    "暂时无法检查上传额度，请稍后再试。"
  );
}

{
  // Anything else falls back to the caller's own copy.
  assert.equal(uploadLimitMessage(429, null, 60), null);
  assert.equal(uploadLimitMessage(503, null, null), null);
  assert.equal(uploadLimitMessage(413, null, null), null);
  assert.equal(uploadLimitMessage(409, "active_parse_limit", 60), null);
  assert.equal(uploadLimitMessage(503, "upload_daily_limit", 60), null);
  assert.equal(uploadLimitMessage(429, "parse_queue_full", 60), null);
  assert.equal(uploadLimitMessage(429, "steam_rate_limited", 60), null);
}
