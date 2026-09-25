import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecentSteamMatches } from "@/components/steam/RecentSteamMatches";
import * as api from "@/lib/api";
import type { SteamConnection, SteamMatch } from "@/types/steam";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getSteamConnection: vi.fn(),
    listSteamMatches: vi.fn(),
    importSteamMatch: vi.fn(),
    saveSteamConnectionCredentials: vi.fn(),
    syncSteamMatches: vi.fn(),
    deleteSteamConnection: vi.fn()
  };
});

function connection(overrides: Partial<SteamConnection> = {}): SteamConnection {
  return {
    connected: true,
    status: "caught_up",
    credentials_configured: true,
    scheduled_sync_enabled: false,
    last_sync_started_at: "2026-09-25T07:00:00Z",
    last_sync_completed_at: "2026-09-25T07:01:00Z",
    next_retry_at: null,
    last_error_code: null,
    last_error_message: null,
    demo_import_available: false,
    demo_source_provider: "disabled",
    manual_upload_supported: true,
    ...overrides
  };
}

function match(overrides: Partial<SteamMatch> = {}): SteamMatch {
  return {
    id: "match-1",
    status: "discovered",
    source: "steam_match_history",
    discovered_at: "2026-09-25T07:04:00Z",
    updated_at: "2026-09-25T07:04:00Z",
    demo_id: null,
    provider_id: null,
    map_name: null,
    duration_seconds: null,
    ct_round_wins: null,
    t_round_wins: null,
    players: null,
    import_error_code: null,
    import_error_message: null,
    import_retryable: false,
    parser_dispatch_pending: false,
    manual_upload_supported: true,
    ...overrides
  };
}

// Words a Chinese player should never meet in this panel.
const ENGLISH_COPY = /Recent Steam Matches|Sync now|Refresh|Disconnect|Manual upload|Open 2D review|licensed|provider|parser|Not yet|Loading/;

describe("RecentSteamMatches", () => {
  beforeEach(() => {
    vi.mocked(api.getSteamConnection).mockResolvedValue(connection());
    vi.mocked(api.listSteamMatches).mockResolvedValue([]);
  });

  it("is in Chinese and says plainly that this build cannot import automatically", async () => {
    vi.mocked(api.listSteamMatches).mockResolvedValue([
      match(),
      match({
        id: "match-2",
        status: "ready",
        demo_id: "demo-2",
        map_name: "de_dust2",
        duration_seconds: 1875,
        ct_round_wins: 13,
        t_round_wins: 10,
        players: ["one", "two"]
      })
    ]);
    const { container } = render(<RecentSteamMatches />);

    expect(await screen.findByRole("heading", { name: "最近的 Steam 比赛" })).toBeInTheDocument();
    expect(screen.getByText(/当前版本还不能从 Steam 自动下载比赛录像/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "手动上传 .dem" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Dust II")).toBeInTheDocument();
    expect(screen.getByText("31 分 15 秒，CT 13 比 T 10，one、two")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入复盘" })).toHaveAttribute("href", "/demos/demo-2");
    expect(container.textContent).not.toMatch(ENGLISH_COPY);
  });

  it("explains a failed load in Chinese and retries it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listSteamMatches).mockRejectedValueOnce(new api.ApiError(500, "boom"));

    render(<RecentSteamMatches />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法读取最近的 Steam 比赛，请刷新重试。");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(api.listSteamMatches).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("还没有发现比赛")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("labels the code form in Chinese when nothing is connected yet", async () => {
    vi.mocked(api.getSteamConnection).mockResolvedValue(
      connection({ connected: false, status: "disconnected", credentials_configured: false })
    );

    render(<RecentSteamMatches />);

    expect(await screen.findByLabelText("游戏验证码（Game Authentication Code）")).toBeInTheDocument();
    expect(screen.getByLabelText("最近一场比赛的分享代码（一个月内）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存连接" })).toBeDisabled();
    expect(screen.getAllByText("未连接").length).toBeGreaterThan(0);
  });
});
