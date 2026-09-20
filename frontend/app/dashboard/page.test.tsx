import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/dashboard/page";
import * as api from "@/lib/api";
import { demoSummary, ingestion } from "@/lib/test-fixtures/review";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    state: {
      status: "authenticated",
      account: { displayName: "Local development", avatarUrl: null, provider: "development" }
    },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {})
  })
}));

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

function rowFor(name: string) {
  const link = screen.getByRole("link", { name: new RegExp(`：${name}$`) });
  const row = link.closest("article");
  if (!row) throw new Error(`no library row for ${name}`);
  return row;
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.mocked(api.listDemos).mockResolvedValue([]);
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
});
