import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/dashboard/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import type { AuthCapabilities } from "@/lib/auth";
import { demoSummary, ingestion } from "@/lib/test-fixtures/review";

vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

function mockAuth(capabilities: AuthCapabilities | undefined) {
  vi.mocked(useAuth).mockReturnValue({
    state: {
      status: "authenticated",
      account: { displayName: "Local development", avatarUrl: null, provider: "development" },
      capabilities
    },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {})
  });
}

// The Steam panel has its own API surface and its own tests to come; here it
// would only add unrelated requests to every library scenario.
vi.mock("@/components/steam/RecentSteamMatches", () => ({
  RecentSteamMatches: () => null
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listDemos: vi.fn(),
    createMockUpload: vi.fn(),
    createDemoUpload: vi.fn(),
    uploadDemoFile: vi.fn(),
    getUploadQuota: vi.fn(),
    updateDemo: vi.fn(),
    archiveDemo: vi.fn(),
    retryDemoParse: vi.fn()
  };
});

function parsingDemo() {
  return demoSummary({
    id: "demo-2",
    name: "Ranked Mirage",
    original_filename: "ranked_mirage.dem",
    map_name: "de_mirage",
    status: "parsing",
    completed_at: null,
    ingestion: ingestion({ phase: "parsing", active: true, jobStatus: "processing" })
  });
}

const DAILY_LIMIT = "今天的上传次数已用完，约 1 小时 30 分钟 后可以继续上传。";
const NO_LIMITS: api.UploadQuota = {
  dailyLimit: null,
  dailyUsed: 0,
  dailyResetSeconds: null,
  activeLimit: null,
  activeCount: 0,
  maxUploadBytes: 1024 * 1024 * 1024
};
const ACTIVE_LIMIT = "已有比赛正在处理，请等当前比赛处理完成后再试。";

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function rowFor(name: string) {
  const link = screen.getByRole("link", { name });
  const row = link.closest("article");
  if (!row) throw new Error(`no library row for ${name}`);
  return row;
}

describe("DashboardPage", () => {
  beforeEach(() => {
    mockAuth({ devTools: true, renderClips: true });
    vi.mocked(api.listDemos).mockResolvedValue([]);
    vi.mocked(api.getUploadQuota).mockResolvedValue(NO_LIMITS);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("lists demos with their processing state and the right entry action", async () => {
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary(), parsingDemo()]);

    render(<DashboardPage />);

    expect(await screen.findByText("2 场比赛")).toBeInTheDocument();
    const ready = rowFor("Mock Match demo-1");
    expect(ready).toHaveTextContent("可以复盘");
    expect(within(ready).getByRole("link", { name: "进入复盘：Mock Match demo-1" })).toHaveAttribute(
      "href",
      "/demos/demo-1#player"
    );

    const parsing = rowFor("Ranked Mirage");
    expect(parsing).toHaveTextContent("读取比赛中");
    expect(within(parsing).getByRole("link", { name: "查看处理状态：Ranked Mirage" })).toHaveAttribute(
      "href",
      "/demos/demo-2"
    );
    expect(screen.getByText("1 场正在处理，完成后自动更新")).toBeInTheDocument();
  });

  it("labels the finding count as the whole match's and holds it back until parsing finishes", async () => {
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary({ coaching_event_count: 37 }), parsingDemo()]);

    render(<DashboardPage />);

    await screen.findByText("2 场比赛");
    const count = within(rowFor("Mock Match demo-1")).getByTitle("所有玩家合计；进入比赛后只显示你的玩家的建议");
    expect(count).toHaveTextContent(/^全场\s*37\s*条复盘线索$/);
    expect(rowFor("Ranked Mirage")).not.toHaveTextContent(/复盘线索/);
  });

  it("creates a mock demo from the empty library and shows it", async () => {
    const user = userEvent.setup();
    const created = demoSummary({
      id: "demo-9",
      name: "Mock Match demo-9",
      status: "queued",
      completed_at: null,
      ingestion: ingestion({ phase: "uploaded", active: true, jobStatus: "queued" })
    });
    vi.mocked(api.listDemos).mockResolvedValueOnce([]).mockResolvedValue([created]);
    vi.mocked(api.createMockUpload).mockResolvedValue(created);

    render(<DashboardPage />);

    expect(await screen.findByText("开始你的第一场复盘")).toBeInTheDocument();
    expect(screen.getByText("上传 .dem 比赛文件，即可查看战术回放和复盘建议。也可以先用模拟比赛体验。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "示例比赛" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "示例比赛（模拟数据）" }));

    expect(api.createMockUpload).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText("示例比赛已创建：Mock Match demo-9。这是模拟数据，可用来体验复盘。")
    ).toBeInTheDocument();
    expect(rowFor("Mock Match demo-9")).toHaveTextContent("等待处理");
    expect(screen.queryByText("开始你的第一场复盘")).not.toBeInTheDocument();
  });

  it("shows the unavailable state when the library cannot load and recovers on refresh", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos)
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValue([demoSummary()]);

    render(<DashboardPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("网络连接中断，请检查网络后重试。");
    expect(screen.queryByText(/应用已启动/)).not.toBeInTheDocument();
    const emptyState = screen.getByText("暂时无法加载比赛").closest(".library-empty-state");
    if (!emptyState) throw new Error("library empty state not rendered");

    // The toolbar has its own refresh control; the empty state offers the same
    // action as the recovery path, which is the one under test here.
    await user.click(within(emptyState as HTMLElement).getByRole("button", { name: "刷新比赛列表" }));

    expect(await screen.findByText("1 场比赛")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(rowFor("Mock Match demo-1")).toBeInTheDocument();
  });

  it("says a first load failure once, in the ledger, without an upload that would fail too", async () => {
    vi.mocked(api.listDemos).mockRejectedValueOnce(new api.ApiError(503, "Service Unavailable"));

    render(<DashboardPage />);

    const alert = await screen.findByRole("alert");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(alert).toHaveTextContent("暂时无法加载比赛");
    expect(alert).toHaveTextContent("服务暂时出错，请稍后重试。");
    expect(within(alert).queryByRole("button", { name: "上传比赛 .dem" })).not.toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "刷新比赛列表" })).toBeInTheDocument();
  });

  it("retries a failed refresh straight from the error banner while the rows stay", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos)
      .mockResolvedValueOnce([demoSummary()])
      .mockRejectedValueOnce(new api.ApiError(503, "Service Unavailable"))
      .mockResolvedValue([demoSummary()]);

    render(<DashboardPage />);
    await screen.findByText("1 场比赛");
    await user.click(screen.getByRole("button", { name: "刷新比赛列表" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("服务暂时出错，请稍后重试。");
    expect(rowFor("Mock Match demo-1")).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "重试" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(api.listDemos).toHaveBeenCalledTimes(3);
  });

  it("shows a failed demo's reason in Chinese and points a broken file at a new upload", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([
      demoSummary({
        status: "failed",
        completed_at: null,
        ingestion: ingestion({
          phase: "failed",
          jobStatus: "failed",
          retryable: true,
          failure: {
            errorCode: "INVALID_DEMO",
            message: "Invalid or unreadable demo file.",
            failedAt: "2026-09-18T12:00:00Z",
            updatedAt: "2026-09-18T12:00:00Z",
            retryable: true,
            attemptCount: 1
          }
        })
      })
    ]);
    const pickerClicks = vi.fn();

    render(<DashboardPage />);
    await screen.findByText("1 场比赛");
    const row = rowFor("Mock Match demo-1");
    screen.getByLabelText("选择 .dem 比赛文件").addEventListener("click", pickerClicks);

    expect(row).toHaveTextContent("处理失败");
    expect(within(row).getByText("文件无法读取，可能不是完整的 CS2 .dem 比赛文件。")).toBeVisible();
    expect(row).not.toHaveTextContent(/Invalid or unreadable|INVALID_DEMO/);
    expect(within(row).queryByRole("link", { name: /查看处理状态/ })).not.toBeInTheDocument();

    // Another pass over the same broken file cannot help, so the upload leads
    // and the retry the backend still allows moves into the row menu.
    await user.click(within(row).getByRole("button", { name: "重新上传：Mock Match demo-1" }));
    expect(pickerClicks).toHaveBeenCalledTimes(1);
    expect(within(row).getByRole("button", { name: "重新处理", hidden: true })).not.toBeVisible();

    vi.mocked(api.retryDemoParse).mockResolvedValueOnce(demoSummary({ status: "queued", completed_at: null }));
    await user.click(within(row).getByLabelText("Mock Match demo-1 的更多操作"));
    await user.click(within(row).getByRole("button", { name: "重新处理" }));
    expect(api.retryDemoParse).toHaveBeenCalledWith("demo-1");
  });

  it("offers the retry first for a failure another pass can fix", async () => {
    vi.mocked(api.listDemos).mockResolvedValue([
      demoSummary({
        status: "failed",
        completed_at: null,
        ingestion: ingestion({
          phase: "failed",
          jobStatus: "failed",
          retryable: true,
          failure: {
            errorCode: "PARSE_TIMED_OUT",
            message: "Parsing this demo took too long and was stopped.",
            failedAt: "2026-09-18T12:00:00Z",
            updatedAt: "2026-09-18T12:00:00Z",
            retryable: true,
            attemptCount: 1
          }
        })
      })
    ]);

    render(<DashboardPage />);
    await screen.findByText("1 场比赛");
    const row = rowFor("Mock Match demo-1");

    expect(within(row).getByText("处理时间过长，已停止。")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "重新处理" })).toBeVisible();
    expect(within(row).queryByRole("button", { name: /重新上传/ })).not.toBeInTheDocument();
  });

  it("explains an empty search and clears it on request", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary()]);

    render(<DashboardPage />);
    expect(await screen.findByText("1 场比赛")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "搜索比赛" }), "mirage");
    expect(screen.getByText("没有找到这场比赛")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "进入复盘：Mock Match demo-1" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(rowFor("Mock Match demo-1")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "搜索比赛" })).toHaveValue("");
  });

  it.each([
    ["the API turns dev tools off", { devTools: false, renderClips: true }],
    ["an older API sends no capabilities", undefined]
  ])("offers only the real upload when %s", async (_, capabilities) => {
    mockAuth(capabilities);

    render(<DashboardPage />);

    expect(await screen.findByText("开始你的第一场复盘")).toBeInTheDocument();
    expect(screen.getByText("上传 .dem 比赛文件，即可查看战术回放和复盘建议。")).toBeInTheDocument();
    expect(screen.queryByText(/模拟比赛体验/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "示例比赛" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "示例比赛（模拟数据）" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传比赛" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "上传比赛 .dem" })).toBeEnabled();
    expect(api.createMockUpload).not.toHaveBeenCalled();
  });

  it("keeps an upload quota rejection on screen through polling and refresh until the next upload", async () => {
    vi.useFakeTimers();
    vi.mocked(api.listDemos).mockResolvedValue([parsingDemo()]);
    vi.mocked(api.uploadDemoFile).mockRejectedValueOnce(
      new api.ApiError(429, "Daily upload limit reached.", "upload_daily_limit", 5400)
    );

    render(<DashboardPage />);
    await flush();
    expect(rowFor("Ranked Mirage")).toBeInTheDocument();

    const input = screen.getByLabelText("选择 .dem 比赛文件");
    fireEvent.change(input, { target: { files: [new File(["demo"], "sample.dem")] } });
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent(DAILY_LIMIT);
    const loadsBefore = vi.mocked(api.listDemos).mock.calls.length;

    // The parsing demo keeps the library polling; each successful load used to wipe the message.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800 * 2);
    });
    expect(vi.mocked(api.listDemos).mock.calls.length).toBeGreaterThanOrEqual(loadsBefore + 2);
    expect(screen.getByRole("alert")).toHaveTextContent(DAILY_LIMIT);

    fireEvent.click(screen.getByRole("button", { name: "刷新比赛列表" }));
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(DAILY_LIMIT);

    vi.mocked(api.uploadDemoFile).mockResolvedValueOnce(
      demoSummary({ id: "demo-3", name: "Retry upload", original_filename: "retry.dem", status: "queued" })
    );
    fireEvent.change(input, { target: { files: [new File(["demo"], "retry.dem")] } });
    await flush();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("「Retry upload」已上传，正在排队处理，完成后会在这里提示。")).toBeInTheDocument();
  });

  it("explains a full parse queue on retry instead of the generic failure", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([
      demoSummary({
        status: "failed",
        completed_at: null,
        ingestion: ingestion({ phase: "failed", jobStatus: "failed", retryable: true })
      })
    ]);
    vi.mocked(api.retryDemoParse).mockRejectedValue(
      new api.ApiError(503, "Parse queue is full.", "parse_queue_full", 60)
    );

    render(<DashboardPage />);
    await user.click(await screen.findByRole("button", { name: "重新处理" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("服务繁忙，处理队列已满，请稍后再试。");
  });

  it("keeps a parse retry refused by the in-flight limit on screen through polling until the next retry", async () => {
    vi.useFakeTimers();
    const failed = demoSummary({
      status: "failed",
      completed_at: null,
      ingestion: ingestion({ phase: "failed", jobStatus: "failed", retryable: true })
    });
    vi.mocked(api.listDemos).mockResolvedValue([failed, parsingDemo()]);
    vi.mocked(api.retryDemoParse).mockRejectedValueOnce(
      new api.ApiError(429, "Too many demos are processing.", "active_parse_limit", 60)
    );

    render(<DashboardPage />);
    await flush();

    fireEvent.click(within(rowFor("Mock Match demo-1")).getByRole("button", { name: "重新处理" }));
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(ACTIVE_LIMIT);
    const loadsBefore = vi.mocked(api.listDemos).mock.calls.length;

    // The parsing demo that tripped the limit keeps the library polling.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800 * 2);
    });
    expect(vi.mocked(api.listDemos).mock.calls.length).toBeGreaterThanOrEqual(loadsBefore + 2);
    expect(screen.getByRole("alert")).toHaveTextContent(ACTIVE_LIMIT);

    vi.mocked(api.retryDemoParse).mockResolvedValueOnce(demoSummary({ status: "queued", completed_at: null }));
    fireEvent.click(within(rowFor("Mock Match demo-1")).getByRole("button", { name: "重新处理" }));
    await flush();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("正在重新处理「Mock Match demo-1」")).toBeInTheDocument();
  });

  it("shows a running upload as a pending row with progress, guards leaving, and cancels on request", async () => {
    const user = userEvent.setup();
    let options: api.UploadDemoFileOptions | undefined;
    vi.mocked(api.uploadDemoFile).mockImplementation(
      (_file, uploadOptions) =>
        new Promise((_resolve, reject) => {
          options = uploadOptions;
          uploadOptions?.signal?.addEventListener("abort", () => {
            const error = new Error("Upload cancelled");
            error.name = "AbortError";
            reject(error);
          });
        })
    );

    render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "big-match.dem")] }
    });

    const row = await screen.findByRole("article", { name: "正在上传：big-match.dem" });
    // The pending row replaces the first-run call-to-action.
    expect(screen.queryByText("开始你的第一场复盘")).not.toBeInTheDocument();
    act(() => options?.onProgress?.({ loaded: 120 * MB, total: 300 * MB }));
    expect(within(row).getByRole("progressbar", { name: "上传进度" })).toHaveAttribute("aria-valuenow", "40");
    expect(within(row).getByText("上传中 40%")).toBeInTheDocument();
    expect(within(row).getByText("120 / 300 MB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传中 40%" })).toBeDisabled();

    expect(leavingIsGuarded()).toBe(true);

    await user.click(within(row).getByRole("button", { name: "取消上传" }));

    expect(await screen.findByText("已取消上传「big-match.dem」。")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /正在上传/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(leavingIsGuarded()).toBe(false);
    expect(screen.getByRole("button", { name: "上传比赛" })).toHaveFocus();
  });

  it("says the server is checking the file once every byte is sent", async () => {
    let options: api.UploadDemoFileOptions | undefined;
    let finish: (demo: ReturnType<typeof demoSummary>) => void = () => {};
    vi.mocked(api.uploadDemoFile).mockImplementation(
      (_file, uploadOptions) =>
        new Promise((resolve) => {
          options = uploadOptions;
          finish = resolve;
        })
    );

    render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "done.dem")] }
    });
    const row = await screen.findByRole("article", { name: "正在上传：done.dem" });
    act(() => options?.onProgress?.({ loaded: 300 * MB, total: 300 * MB }));

    expect(row).toHaveTextContent("上传完成，服务器校验中…");
    expect(within(row).queryByRole("button", { name: "取消上传" })).not.toBeInTheDocument();

    await act(async () => finish(demoSummary({ id: "demo-7", name: "done.dem", status: "queued" })));
    expect(screen.queryByRole("article", { name: /正在上传/ })).not.toBeInTheDocument();
  });

  it("keeps an upload running across a client-side navigation and lists it once it lands", async () => {
    let finish: (demo: ReturnType<typeof demoSummary>) => void = () => {};
    vi.mocked(api.uploadDemoFile).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    const landed = demoSummary({ id: "demo-9", name: "away.dem", original_filename: "away.dem" });

    const first = render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "away.dem")] }
    });
    await screen.findByRole("article", { name: "正在上传：away.dem" });
    first.unmount();

    render(<DashboardPage />);
    expect(await screen.findByRole("article", { name: "正在上传：away.dem" })).toBeInTheDocument();
    vi.mocked(api.listDemos).mockResolvedValue([landed]);
    await act(async () => finish(landed));

    expect(await screen.findByRole("link", { name: "进入复盘：Inferno，2 回合" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /正在上传/ })).not.toBeInTheDocument();
  });

  it.each([
    ["while the player was on another page", true],
    ["after the dashboard remounted", false]
  ])("still reports an upload rejection that settles %s", async (_label, settleWhileAway) => {
    let refuse: (error: unknown) => void = () => {};
    vi.mocked(api.uploadDemoFile).mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          refuse = reject;
        })
    );
    const rejection = new api.ApiError(503, "Artifact storage is temporarily unavailable", "INTAKE_STORAGE_UNAVAILABLE");

    const first = render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "away.dem")] }
    });
    await screen.findByRole("article", { name: "正在上传：away.dem" });
    first.unmount();

    if (settleWhileAway) {
      await act(async () => refuse(rejection));
      render(<DashboardPage />);
    } else {
      render(<DashboardPage />);
      await screen.findByRole("article", { name: "正在上传：away.dem" });
      await act(async () => refuse(rejection));
    }

    expect(await screen.findByRole("alert")).toHaveTextContent("你的文件没有问题");
    expect(screen.queryByRole("article", { name: /正在上传/ })).not.toBeInTheDocument();
  });

  it("returns focus to the row after a successful retry", async () => {
    const user = userEvent.setup();
    const failed = demoSummary({
      status: "failed",
      completed_at: null,
      ingestion: ingestion({ phase: "failed", jobStatus: "failed", retryable: true })
    });
    vi.mocked(api.listDemos).mockResolvedValue([failed]);
    vi.mocked(api.retryDemoParse).mockResolvedValueOnce(
      demoSummary({ status: "queued", completed_at: null, ingestion: ingestion({ phase: "uploaded", active: true }) })
    );

    render(<DashboardPage />);
    await user.click(await screen.findByRole("button", { name: "重新处理" }));

    expect(await screen.findByRole("link", { name: "查看处理状态：Mock Match demo-1" })).toHaveFocus();
  });

  it("rejects a file that is not a .dem before sending it", async () => {
    render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");

    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["zip"], "match.dem.zip")] }
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("压缩包（.zip、.rar、.gz、.bz2 等）请先解压");
    expect(api.uploadDemoFile).not.toHaveBeenCalled();
  });

  it.each([
    [new api.ApiError(413, "Uploaded demo exceeds the configured size limit", "INTAKE_TOO_LARGE"), "文件超过上传上限（1 GB）"],
    [new api.ApiError(503, "Artifact storage is temporarily unavailable", "INTAKE_STORAGE_UNAVAILABLE"), "你的文件没有问题"],
    [new api.ApiError(400, "Uploaded demo is incomplete", "INTAKE_TRUNCATED"), "文件不完整"]
  ])("explains an intake rejection by its code (%s)", async (error, expected) => {
    vi.mocked(api.uploadDemoFile).mockRejectedValueOnce(error);

    render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "sample.dem")] }
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expected);
    expect(alert).not.toHaveTextContent("请检查文件后重试");
  });

  it("shows the remaining daily uploads and turns upload off, with the reason, once they are used up", async () => {
    vi.mocked(api.getUploadQuota).mockResolvedValueOnce({
      ...NO_LIMITS,
      dailyLimit: 10,
      dailyUsed: 3,
      activeLimit: 2
    });
    const { unmount } = render(<DashboardPage />);
    expect(await screen.findByText("今天还可上传 7 场")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传比赛" })).toBeEnabled();
    unmount();

    vi.mocked(api.getUploadQuota).mockResolvedValue({
      ...NO_LIMITS,
      dailyLimit: 10,
      dailyUsed: 10,
      dailyResetSeconds: 5400,
      activeLimit: 2
    });
    render(<DashboardPage />);

    expect(await screen.findByText(DAILY_LIMIT)).toBeInTheDocument();
    expect(screen.getByText("今天的次数已用完")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传比赛" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上传比赛 .dem" })).toBeDisabled();

    // A dropped file is held back too, instead of spending minutes on a refused transfer.
    dropFiles([new File(["demo"], "late.dem")]);
    expect(await screen.findByRole("alert")).toHaveTextContent(DAILY_LIMIT);
    expect(api.uploadDemoFile).not.toHaveBeenCalled();
  });

  it("turns upload back on once the daily quota resets, without a reload", async () => {
    vi.useFakeTimers();
    const usedUp = { ...NO_LIMITS, dailyLimit: 10, dailyUsed: 10, dailyResetSeconds: 60, activeLimit: 2 };
    vi.mocked(api.getUploadQuota).mockResolvedValue(usedUp);
    render(<DashboardPage />);
    await flush();
    expect(screen.getByRole("button", { name: "上传比赛" })).toBeDisabled();
    dropFiles([new File(["demo"], "late.dem")]);
    await flush();
    expect(screen.getByRole("alert")).toBeInTheDocument();

    vi.mocked(api.getUploadQuota).mockResolvedValue({ ...usedUp, dailyUsed: 9, dailyResetSeconds: 3600 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });

    expect(screen.getByRole("button", { name: "上传比赛" })).toBeEnabled();
    expect(screen.getByText("今天还可上传 1 场")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("checks a used-up daily quota again when the player returns to the tab", async () => {
    vi.useFakeTimers();
    const usedUp = { ...NO_LIMITS, dailyLimit: 10, dailyUsed: 10, dailyResetSeconds: 5400, activeLimit: 2 };
    vi.mocked(api.getUploadQuota).mockResolvedValue(usedUp);
    render(<DashboardPage />);
    await flush();
    expect(screen.getByText(DAILY_LIMIT)).toBeInTheDocument();

    vi.mocked(api.getUploadQuota).mockResolvedValue({ ...usedUp, dailyUsed: 4 });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flush();

    expect(screen.getByRole("button", { name: "上传比赛" })).toBeEnabled();
    expect(screen.queryByText(DAILY_LIMIT)).not.toBeInTheDocument();
  });

  it("uploads a .dem dropped anywhere on the page", async () => {
    vi.mocked(api.uploadDemoFile).mockResolvedValueOnce(
      demoSummary({ id: "demo-8", name: "dropped.dem", original_filename: "dropped.dem", status: "queued" })
    );
    render(<DashboardPage />);
    await screen.findByText("开始你的第一场复盘");

    fireEvent.dragOver(window, { dataTransfer: { types: ["Files"], files: [] } });
    expect(screen.getByText("松开即可上传 .dem")).toBeInTheDocument();

    const file = new File(["demo"], "dropped.dem");
    dropFiles([file]);

    await waitFor(() => expect(api.uploadDemoFile).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.uploadDemoFile).mock.calls[0][0]).toBe(file);
    expect(screen.queryByText("松开即可上传 .dem")).not.toBeInTheDocument();
  });

  it("follows an upload through parsing to a ready notice, announced from an always-present live region", async () => {
    vi.useFakeTimers();
    const uploaded = demoSummary({
      id: "demo-7",
      name: "ranked.dem",
      original_filename: "ranked.dem",
      status: "queued",
      completed_at: null,
      ingestion: ingestion({ phase: "uploaded", active: true, jobStatus: "queued" })
    });
    vi.mocked(api.listDemos)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploaded])
      .mockResolvedValue([{ ...uploaded, status: "completed", round_count: 24, ingestion: ingestion() }]);
    vi.mocked(api.uploadDemoFile).mockResolvedValueOnce(uploaded);

    render(<DashboardPage />);
    await flush();
    const region = document.querySelector(".library-notice-region");
    if (!(region instanceof HTMLElement)) throw new Error("no notice region");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toBeEmptyDOMElement();

    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "ranked.dem")] }
    });
    await flush();
    expect(region).toHaveTextContent("「ranked.dem」已上传，正在排队处理，完成后会在这里提示。");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });

    expect(region).toHaveTextContent("「ranked.dem」可以复盘了。");
    expect(within(region).getByRole("link", { name: "进入复盘" })).toHaveAttribute("href", "/demos/demo-7#player");
    // The row now reads as the match it is, with the file name underneath.
    expect(rowFor("Inferno，24 回合")).toHaveTextContent("ranked.dem");

    fireEvent.click(within(region).getByRole("button", { name: "关闭提示" }));
    expect(region).toBeEmptyDOMElement();
  });

  it("clears an in-flight-limit rejection once the parse it waited on is done", async () => {
    vi.useFakeTimers();
    // Initial load, then the reload after the refused upload: still parsing.
    vi.mocked(api.listDemos)
      .mockResolvedValueOnce([parsingDemo()])
      .mockResolvedValueOnce([parsingDemo()])
      .mockResolvedValue([{ ...parsingDemo(), status: "completed", ingestion: ingestion() }]);
    vi.mocked(api.uploadDemoFile).mockRejectedValueOnce(
      new api.ApiError(429, "Too many demos are processing.", "active_parse_limit", 60)
    );

    render(<DashboardPage />);
    await flush();
    fireEvent.change(screen.getByLabelText("选择 .dem 比赛文件"), {
      target: { files: [new File(["demo"], "next.dem")] }
    });
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(ACTIVE_LIMIT);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("当前比赛已处理完成，可以继续上传。")).toBeInTheDocument();
  });

  it("shows skeleton rows, not the first-run call-to-action, while the library loads", () => {
    vi.mocked(api.listDemos).mockReturnValue(new Promise(() => {}));

    render(<DashboardPage />);

    expect(screen.getByRole("status")).toHaveTextContent("正在加载比赛…");
    expect(screen.getByRole("region", { name: "比赛列表" })).toHaveAttribute("aria-busy", "true");
    expect(document.querySelectorAll(".library-skeleton-row")).toHaveLength(3);
    expect(screen.queryByText("开始你的第一场复盘")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上传比赛 .dem" })).not.toBeInTheDocument();
  });

  it("holds back counts for a processing row and names failures as failures", async () => {
    vi.mocked(api.listDemos).mockResolvedValue([
      parsingDemo(),
      demoSummary({
        id: "demo-3",
        name: "Broken",
        status: "failed",
        completed_at: null,
        ingestion: ingestion({ phase: "failed", jobStatus: "failed" })
      })
    ]);

    render(<DashboardPage />);
    await screen.findByText("2 场比赛");

    expect(within(rowFor("Ranked Mirage")).getByText("—")).toBeInTheDocument();
    expect(screen.getByText("1 场处理失败")).toBeInTheDocument();
    expect(screen.queryByText(/需要处理/)).not.toBeInTheDocument();
  });

  it("finds demos by the Chinese labels the rows show", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary(), parsingDemo()]);

    render(<DashboardPage />);
    await screen.findByText("2 场比赛");
    await user.type(screen.getByRole("searchbox", { name: "搜索比赛" }), "读取比赛中");

    expect(rowFor("Ranked Mirage")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Mock Match demo-1" })).not.toBeInTheDocument();
  });

  it("archives with an undo, keeping keyboard focus in the list", async () => {
    const user = userEvent.setup();
    const second = demoSummary({ id: "demo-2", name: "Second match" });
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary(), second]);
    vi.mocked(api.archiveDemo).mockResolvedValueOnce(demoSummary({ archived: true }));
    vi.mocked(api.updateDemo).mockResolvedValueOnce(demoSummary({ archived: false }));

    render(<DashboardPage />);
    await screen.findByText("2 场比赛");
    await user.click(screen.getByLabelText("Mock Match demo-1 的更多操作"));
    await user.click(within(rowFor("Mock Match demo-1")).getByRole("button", { name: "归档比赛" }));

    expect(await screen.findByText("已归档「Mock Match demo-1」，可在筛选中显示并恢复。")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Mock Match demo-1" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Second match" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "撤销" }));

    expect(api.updateDemo).toHaveBeenCalledWith("demo-1", { archived: false });
    expect(await screen.findByText("已恢复「Mock Match demo-1」")).toBeInTheDocument();
    expect(rowFor("Mock Match demo-1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mock Match demo-1" })).toHaveFocus();
  });

  it("cancels a rename with Escape and shows a failed save next to the field", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary()]);
    vi.mocked(api.updateDemo).mockRejectedValueOnce(new api.ApiError(400, "Name is invalid"));

    render(<DashboardPage />);
    await screen.findByText("1 场比赛");
    await user.click(screen.getByLabelText("Mock Match demo-1 的更多操作"));
    await user.click(within(rowFor("Mock Match demo-1")).getByRole("button", { name: "重命名" }));

    expect(screen.getByRole("textbox", { name: "重命名 Mock Match demo-1" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "重命名 Mock Match demo-1" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Mock Match demo-1 的更多操作")).toHaveFocus();

    await user.click(screen.getByLabelText("Mock Match demo-1 的更多操作"));
    await user.click(within(rowFor("Mock Match demo-1")).getByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox", { name: "重命名 Mock Match demo-1" });
    await user.clear(input);
    await user.type(input, "Grand final{Enter}");

    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("重命名失败，请重试。");
    expect(error.closest("article")).not.toBeNull();
    expect(screen.getByRole("textbox", { name: "重命名 Mock Match demo-1" })).toHaveValue("Grand final");
  });

  it("closes the filter popover with Escape or a click outside", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listDemos).mockResolvedValue([demoSummary()]);
    render(<DashboardPage />);
    await screen.findByText("1 场比赛");
    const popover = screen.getByText("筛选与排序").closest("details");
    if (!popover) throw new Error("no filter popover");

    await user.click(screen.getByText("筛选与排序"));
    expect(popover).toHaveAttribute("open");
    await user.keyboard("{Escape}");
    expect(popover).not.toHaveAttribute("open");

    await user.click(screen.getByText("筛选与排序"));
    expect(popover).toHaveAttribute("open");
    await user.click(screen.getByRole("heading", { name: "我的比赛" }));
    expect(popover).not.toHaveAttribute("open");
  });
});

const MB = 1024 * 1024;

function leavingIsGuarded() {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

function dropFiles(files: File[]) {
  fireEvent.drop(window, { dataTransfer: { types: ["Files"], files } });
}
