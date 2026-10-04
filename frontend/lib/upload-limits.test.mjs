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

const { demoFileProblem, demoUploadErrorMessage, uploadLimitMessage, uploadQuotaSummary } = loadTypeScriptModule("./upload-limits.ts");

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
    "已有比赛正在处理，请等当前比赛处理完成后再试。"
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

{
  const NOT_A_DEMO = "这不是 CS2 的 .dem 比赛文件。压缩包（.zip、.rar、.gz、.bz2 等）请先解压，再上传里面的 .dem 文件。";
  const SERVER = "服务暂时无法接收文件，你的文件没有问题，请稍后重试。";
  const GiB = 1024 * 1024 * 1024;

  // Quota codes keep their own copy ahead of any intake mapping.
  assert.equal(
    demoUploadErrorMessage(429, "active_parse_limit", 60),
    "已有比赛正在处理，请等当前比赛处理完成后再试。"
  );
  assert.equal(demoUploadErrorMessage(503, "upload_quota_unavailable", 30), "暂时无法检查上传额度，请稍后再试。");

  // A size rejection says size, whether it carries the code or only the status.
  const tooLarge = "文件超过上传上限（1 GB），请确认选择的是单场比赛的 .dem 文件。";
  assert.equal(demoUploadErrorMessage(413, "INTAKE_TOO_LARGE", null), tooLarge);
  assert.equal(demoUploadErrorMessage(413, null, null), tooLarge);
  assert.equal(
    demoUploadErrorMessage(413, "INTAKE_TOO_LARGE", null, 512 * 1024 * 1024),
    "文件超过上传上限（512 MB），请确认选择的是单场比赛的 .dem 文件。"
  );

  assert.equal(demoUploadErrorMessage(400, "INTAKE_TYPE_REJECTED", null), NOT_A_DEMO);
  assert.equal(demoUploadErrorMessage(400, "INTAKE_CONTENT_MISMATCH", null), NOT_A_DEMO);
  assert.match(demoUploadErrorMessage(400, "INTAKE_TRUNCATED", null), /不完整/);
  assert.match(demoUploadErrorMessage(400, "INTAKE_EMPTY", null), /空的/);

  // Outages are the server's, not the file's.
  assert.equal(demoUploadErrorMessage(503, "INTAKE_STORAGE_UNAVAILABLE", null), SERVER);
  assert.equal(demoUploadErrorMessage(503, "INTAKE_UNAVAILABLE", null), SERVER);
  assert.equal(demoUploadErrorMessage(502, null, null), SERVER);
  assert.match(demoUploadErrorMessage(503, "INTAKE_BUSY", 5), /服务正忙/);

  // Anything else is left to the generic request copy.
  assert.equal(demoUploadErrorMessage(400, null, null), null);
  assert.equal(demoUploadErrorMessage(409, "something_else", null), null);

  // The upload session codes: resource limits are the server's, a missing part asks for the file again.
  assert.match(demoUploadErrorMessage(503, "upload_capacity_busy", 30), /你的文件没有问题/);
  assert.match(demoUploadErrorMessage(503, "upload_storage_full", 60), /暂存空间不足.*你的文件没有问题/);
  assert.match(demoUploadErrorMessage(409, "upload_session_exists", null), /未完成的上传/);
  assert.match(demoUploadErrorMessage(409, "upload_parts_missing", null), /选择同一个文件继续/);
  assert.match(demoUploadErrorMessage(409, "upload_parts_in_flight", 1), /传送中/);
  assert.match(demoUploadErrorMessage(409, "upload_session_not_open", null), /已经结束/);
  assert.match(demoUploadErrorMessage(400, "upload_part_invalid", null), /重新上传/);
  assert.match(demoUploadErrorMessage(422, "upload_part_digest_mismatch", null), /校验没有通过/);
  assert.match(demoUploadErrorMessage(404, "upload_session_gone", null), /过期或已被放弃/);
  assert.match(demoUploadErrorMessage(401, "account_deleted", null), /账户已删除/);
  // The part 0 signature check reuses the intake's code and copy.
  assert.equal(demoUploadErrorMessage(400, "INTAKE_CONTENT_MISMATCH", null), NOT_A_DEMO);
  // A quota refusal at completion keeps the quota copy.
  assert.equal(
    demoUploadErrorMessage(429, "upload_daily_limit", 3600),
    "今天的上传次数已用完，约 1 小时 后可以继续上传。"
  );

  assert.equal(demoFileProblem({ name: "match.dem", size: 300 * 1024 * 1024 }), null);
  assert.equal(demoFileProblem({ name: "MATCH.DEM", size: 10 }), null);
  assert.equal(demoFileProblem({ name: "match.dem.gz", size: 10 }), NOT_A_DEMO);
  assert.equal(demoFileProblem({ name: "match.zip", size: 10 }), NOT_A_DEMO);
  assert.match(demoFileProblem({ name: "match.dem", size: 0 }), /空的/);
  assert.equal(demoFileProblem({ name: "match.dem", size: GiB + 1 }), tooLarge);
  assert.equal(demoFileProblem({ name: "match.dem", size: GiB }), null);
  assert.equal(
    demoFileProblem({ name: "match.dem", size: 600 * 1024 * 1024 }, 512 * 1024 * 1024),
    "文件超过上传上限（512 MB），请确认选择的是单场比赛的 .dem 文件。"
  );
}

{
  const quota = (overrides = {}) => ({
    dailyLimit: 10,
    dailyUsed: 3,
    dailyResetSeconds: null,
    activeLimit: 2,
    activeCount: 0,
    ...overrides
  });
  const summary = (value) => ({ ...uploadQuotaSummary(value) });
  const HELP = "每个账号 24 小时内最多上传 10 场，处理失败或已归档的上传也计入次数。";

  // Development (no limits) and an API without the endpoint say nothing.
  const silent = { hint: null, blockedReason: null, helpNote: null };
  assert.deepEqual(summary(null), silent);
  assert.deepEqual(summary(quota({ dailyLimit: null, activeLimit: null, activeCount: 5 })), silent);

  assert.deepEqual(summary(quota()), { hint: "今天还可上传 7 场", blockedReason: null, helpNote: HELP });
  assert.deepEqual(summary(quota({ activeCount: 2 })), {
    hint: "等当前比赛处理完再上传",
    blockedReason: null,
    helpNote: HELP
  });
  assert.deepEqual(summary(quota({ dailyUsed: 10, dailyResetSeconds: 5400 })), {
    hint: "今天的次数已用完",
    blockedReason: "今天的上传次数已用完，约 1 小时 30 分钟 后可以继续上传。",
    helpNote: HELP
  });
  // Only the in-flight cap on: no count to show, but still the wait.
  assert.equal(summary(quota({ dailyLimit: null, activeCount: 2 })).hint, "等当前比赛处理完再上传");
}
