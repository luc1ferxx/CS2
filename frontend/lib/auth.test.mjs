import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadTypeScriptModule(relativePath, overrides = {}) {
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
    process,
    URLSearchParams,
    ...overrides,
    require(specifier) {
      if (specifier.startsWith("@/types/")) {
        return {};
      }
      throw new Error(`Unexpected runtime import: ${specifier}`);
    }
  };
  vm.runInNewContext(outputText, context, { filename });
  return module.exports;
}

{
  const requests = [];
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async (url, init) => {
      requests.push({ url, init });
      return new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    },
    Response
  });

  await api.listDemos();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.credentials, "include");
}

{
  const previousProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER;
  try {
    process.env.NEXT_PUBLIC_AUTH_PROVIDER = "steam";
    const api = loadTypeScriptModule("./api.ts", { URLSearchParams });
    assert.equal(
      api.getAuthLoginUrl("/demos/demo-1"),
      "http://localhost:8000/auth/steam/login?return_to=%2Fdemos%2Fdemo-1"
    );
    assert.equal(api.getConfiguredAuthProvider(), "steam");

    process.env.NEXT_PUBLIC_AUTH_PROVIDER = "oidc";
    const oidcApi = loadTypeScriptModule("./api.ts", { URLSearchParams });
    assert.equal(
      oidcApi.getAuthLoginUrl("/dashboard"),
      "http://localhost:8000/auth/login?return_to=%2Fdashboard"
    );
    assert.equal(oidcApi.getConfiguredAuthProvider(), "oidc");
  } finally {
    if (previousProvider === undefined) {
      delete process.env.NEXT_PUBLIC_AUTH_PROVIDER;
    } else {
      process.env.NEXT_PUBLIC_AUTH_PROVIDER = previousProvider;
    }
  }
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.deepEqual(
    { ...auth.reduceAuthState({ status: "authenticated" }, { type: "signedOut" }) },
    { status: "anonymous" }
  );
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.deepEqual(
    {
      ...auth.reduceAuthState(
        { status: "checking" },
        { type: "sessionFailed", message: "API unavailable" }
      )
    },
    { status: "error", message: "API unavailable" }
  );
}

{
  const requests = [];
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async (url, init) => {
      requests.push({ url, init });
      return new Response(null, { status: 204 });
    },
    Response
  });

  await api.logoutAuthSession();

  assert.equal(requests[0].url, "http://localhost:8000/auth/logout");
  assert.equal(requests[0].init.method, "POST");
  assert.equal(requests[0].init.credentials, "include");
}

{
  const requests = [];
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async (url, init) => {
      requests.push({ url, init });
      return new Response(JSON.stringify({ id: "demo-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    },
    File,
    FormData,
    Response
  });

  await api.createDemoUpload(new File(["demo"], "sample.dem"));

  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.credentials, "include");
  assert.equal(requests[0].init.headers, undefined);
}

{
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async () =>
      new Response(JSON.stringify({ detail: "Session expired" }), {
        status: 401,
        headers: { "Content-Type": "application/json" }
      }),
    Response
  });

  await assert.rejects(
    api.listDemos(),
    (error) =>
      api.isApiError(error) &&
      error.status === 401 &&
      error.code === "unauthenticated" &&
      error.message === "Session expired"
  );
}

{
  const auth = loadTypeScriptModule("./auth.ts");
  const state = auth.reduceAuthState(
    { status: "checking" },
    {
      type: "sessionAuthenticated",
      account: {
        displayName: "Reviewer",
        avatarUrl: null,
        provider: "steam"
      }
    }
  );

  // An API without capabilities offers neither dev tools nor render clips.
  assert.deepEqual(
    { ...state, capabilities: { ...state.capabilities } },
    {
      status: "authenticated",
      account: {
        displayName: "Reviewer",
        avatarUrl: null,
        provider: "steam"
      },
      capabilities: { devTools: false, renderClips: false }
    }
  );
}

{
  const auth = loadTypeScriptModule("./auth.ts");
  const account = { displayName: "Local development", avatarUrl: null, provider: "development" };
  const capabilitiesAfter = (capabilities) => ({
    ...auth.reduceAuthState({ status: "checking" }, { type: "sessionAuthenticated", account, capabilities })
      .capabilities
  });

  assert.deepEqual(capabilitiesAfter({ devTools: true, renderClips: true }), {
    devTools: true,
    renderClips: true
  });
  assert.deepEqual(capabilitiesAfter({ devTools: false, renderClips: true }), {
    devTools: false,
    renderClips: true
  });
  for (const unknown of [null, "all", [], {}, { devTools: "true", renderClips: 1 }]) {
    assert.deepEqual(capabilitiesAfter(unknown), { devTools: false, renderClips: false });
  }
  // The shared default is never handed out for mutation.
  assert.notEqual(auth.authCapabilities(undefined), auth.NO_CAPABILITIES);
  assert.deepEqual({ ...auth.NO_CAPABILITIES }, { devTools: false, renderClips: false });
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.deepEqual(
    { ...auth.reduceAuthState({ status: "checking" }, { type: "unauthorized" }) },
    { status: "anonymous" }
  );
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.deepEqual(
    {
      ...auth.reduceAuthState(
        { status: "error", message: "API unavailable" },
        { type: "unauthorized" }
      )
    },
    { status: "anonymous" }
  );
}

{
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async () => new Response("", { status: 401 }),
    Response
  });
  let unauthorizedCount = 0;
  const unsubscribe = api.onUnauthorized(() => {
    unauthorizedCount += 1;
  });

  await assert.rejects(api.listDemos());
  unsubscribe();

  assert.equal(unauthorizedCount, 1);
}

{
  const requests = [];
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async (url, init) => {
      requests.push({ url, init });
      return new Response(JSON.stringify({
        authenticated: true,
        account: {
          displayName: "Reviewer",
          avatarUrl: null,
          provider: "steam"
        },
        capabilities: { devTools: false, renderClips: true }
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    },
    Response
  });

  const session = await api.getAuthMe();

  assert.equal(session.authenticated, true);
  assert.equal(session.account.displayName, "Reviewer");
  assert.deepEqual({ ...session.capabilities }, { devTools: false, renderClips: true });
  assert.equal(requests[0].url, "http://localhost:8000/auth/me");
  assert.equal(requests[0].init.credentials, "include");
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.equal(
    auth.sanitizeReturnTo("/demos/demo-1?round=4"),
    "/demos/demo-1?round=4"
  );
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.equal(auth.sanitizeReturnTo("https://evil.example/steal"), "/dashboard");
  assert.equal(auth.sanitizeReturnTo("//evil.example/steal"), "/dashboard");
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.equal(auth.sanitizeReturnTo("/\\\\evil.example/steal"), "/dashboard");
}

{
  const sources = [
    readFileSync(resolve(__dirname, "./api.ts"), "utf8"),
    readFileSync(resolve(__dirname, "./auth.ts"), "utf8"),
    readFileSync(resolve(__dirname, "../components/auth/AuthProvider.tsx"), "utf8"),
    readFileSync(resolve(__dirname, "../app/auth/callback/page.tsx"), "utf8")
  ].join("\n");

  assert.doesNotMatch(sources, /localStorage|sessionStorage/);
  assert.doesNotMatch(sources, /Authorization|Bearer|X-Dev-User-Id/);
  assert.doesNotMatch(sources, /NEXT_PUBLIC_.*(?:TOKEN|SECRET|SESSION_COOKIE|API_KEY)/i);
  assert.match(sources, /NEXT_PUBLIC_AUTH_PROVIDER/);
}

{
  const auth = loadTypeScriptModule("./auth.ts");

  assert.deepEqual(
    { ...auth.reduceAuthState({ status: "authenticated" }, { type: "unauthorized" }) },
    { status: "expired" }
  );
}

{
  const api = loadTypeScriptModule("./api.ts", {
    fetch: async () => new Response("", { status: 401 }),
    File,
    FormData,
    Response
  });
  let unauthorizedCount = 0;
  api.onUnauthorized(() => {
    unauthorizedCount += 1;
  });

  await assert.rejects(
    api.createDemoUpload(new File(["demo"], "sample.dem")),
    (error) => api.isApiError(error) && error.status === 401
  );

  assert.equal(unauthorizedCount, 1);
}

function errorResponseApi(body, init) {
  return loadTypeScriptModule("./api.ts", {
    fetch: async () =>
      new Response(typeof body === "string" ? body : JSON.stringify(body), {
        headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
        status: init.status
      }),
    File,
    FormData,
    Response
  });
}

async function rejectedError(api, request) {
  let caught = null;
  try {
    await request(api);
  } catch (error) {
    caught = error;
  }
  assert.ok(api.isApiError(caught), "expected an ApiError");
  return caught;
}

{
  // The structured quota body carries its own wait and wins over the header.
  const api = errorResponseApi(
    {
      detail: {
        code: "upload_daily_limit",
        message: "Daily upload limit reached.",
        retryAfterSeconds: 5400
      }
    },
    { status: 429, headers: { "Retry-After": "30" } }
  );
  const error = await rejectedError(api, (client) =>
    client.createDemoUpload(new File(["demo"], "sample.dem"))
  );

  assert.equal(error.status, 429);
  assert.equal(error.code, "request_failed");
  assert.equal(error.detailCode, "upload_daily_limit");
  assert.equal(error.message, "Daily upload limit reached.");
  assert.equal(error.retryAfterSeconds, 5400);
}

{
  // Without a usable body value the Retry-After header is the fallback.
  for (const retryAfterSeconds of [undefined, 0, -10, "60", null]) {
    const api = errorResponseApi(
      { detail: { code: "parse_queue_full", message: "Parse queue is full.", retryAfterSeconds } },
      { status: 503, headers: { "Retry-After": "60" } }
    );
    const error = await rejectedError(api, (client) => client.retryDemoParse("demo-1"));

    assert.equal(error.detailCode, "parse_queue_full");
    assert.equal(error.retryAfterSeconds, 60);
  }
}

{
  // A string detail still picks up the header; a fractional body value rounds up.
  const stringDetail = await rejectedError(
    errorResponseApi({ detail: "Too many requests" }, { status: 429, headers: { "Retry-After": " 45 " } }),
    (client) => client.listDemos()
  );
  assert.equal(stringDetail.detailCode, null);
  assert.equal(stringDetail.retryAfterSeconds, 45);

  const fractional = await rejectedError(
    errorResponseApi(
      { detail: { code: "active_parse_limit", message: "Busy.", retryAfterSeconds: 59.2 } },
      { status: 429 }
    ),
    (client) => client.listDemos()
  );
  assert.equal(fractional.retryAfterSeconds, 60);
}

{
  // Only whole positive delay-seconds count; anything else leaves the wait unknown.
  for (const header of ["0", "-5", "1.5", "soon", "Wed, 21 Oct 2026 07:28:00 GMT", ""]) {
    const error = await rejectedError(
      errorResponseApi(
        { detail: { code: "active_parse_limit", message: "Busy." } },
        { status: 429, headers: { "Retry-After": header } }
      ),
      (client) => client.listDemos()
    );
    assert.equal(error.retryAfterSeconds, null, `Retry-After ${JSON.stringify(header)}`);
  }

  const noHeader = await rejectedError(
    errorResponseApi({ detail: "Upload too large" }, { status: 413 }),
    (client) => client.listDemos()
  );
  assert.equal(noHeader.retryAfterSeconds, null);
  assert.equal(noHeader.message, "Upload too large");

  const api = loadTypeScriptModule("./api.ts");
  assert.equal(new api.ApiError(429, "Busy").retryAfterSeconds, null);
}
