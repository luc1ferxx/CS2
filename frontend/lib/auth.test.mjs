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
  const api = loadTypeScriptModule("./api.ts", { URLSearchParams });

  assert.equal(
    api.getAuthLoginUrl("/demos/demo-1"),
    "http://localhost:8000/auth/login?return_to=%2Fdemos%2Fdemo-1"
  );
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

  assert.deepEqual(
    {
      ...auth.reduceAuthState(
        { status: "checking" },
        { type: "sessionAuthenticated" }
      )
    },
    { status: "authenticated" }
  );
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
      return new Response(JSON.stringify({ authenticated: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    },
    Response
  });

  const session = await api.getAuthSession();

  assert.equal(session.authenticated, true);
  assert.equal(requests[0].url, "http://localhost:8000/auth/session");
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
  assert.doesNotMatch(sources, /NEXT_PUBLIC_.*(?:TOKEN|SECRET|SESSION|AUTH)/i);
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
