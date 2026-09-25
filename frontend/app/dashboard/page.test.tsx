import { act, fireEvent, render, screen, within } from "@testing-library/react";
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

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function rowFor(name: string) {
  const link = screen.getByRole("link", { name: new RegExp(`：${name}$`) });
  const row = link.closest("article");
  if (!row) throw new Error(`no library row for ${name}`);
  return row;
}

describe("DashboardPage", () => {
  beforeEach(() => {
    mockAuth({ devTools: true, renderClips: true });
    vi.mocked(api.listDemos).mockResolvedValue([]);
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

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法连接服务，请确认应用已启动，然后刷新重试。");
    const emptyState = screen.getByText("暂时无法加载比赛").closest(".library-empty-state");
    if (!emptyState) throw new Error("library empty state not rendered");

    // The toolbar has its own refresh control; the empty state offers the same
    // action as the recovery path, which is the one under test here.
    await user.click(within(emptyState as HTMLElement).getByRole("button", { name: "刷新比赛列表" }));

    expect(await screen.findByText("1 场比赛")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(rowFor("Mock Match demo-1")).toBeInTheDocument();
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
    vi.mocked(api.createDemoUpload).mockRejectedValueOnce(
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

    vi.mocked(api.createDemoUpload).mockResolvedValueOnce(
      demoSummary({ id: "demo-3", name: "Retry upload", original_filename: "retry.dem", status: "queued" })
    );
    fireEvent.change(input, { target: { files: [new File(["demo"], "retry.dem")] } });
    await flush();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("retry.dem 已上传，正在准备复盘。")).toBeInTheDocument();
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
});
