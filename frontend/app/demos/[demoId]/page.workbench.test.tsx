import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import { coachingEvent, demoStatus, renderWorkerStatus, replayData, replayVideo, T_ENTRY_ID } from "@/lib/test-fixtures/review";
import { WORKBENCH_QUERY } from "@/lib/use-workbench-layout";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn() } }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ demoId: "demo-1" }),
  useRouter: () => router
}));

vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getDemoStatus: vi.fn(),
    getReplay: vi.fn(),
    getCoaching: vi.fn(),
    getRenderJobs: vi.fn(),
    getDemoVideo: vi.fn(),
    getRenderWorkerStatus: vi.fn()
  };
});

const setupMatchMedia = window.matchMedia;

// The workbench media query answers `matches`; every other query (reduced motion…) stays false.
function viewportFitsWorkbench(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string): MediaQueryList => ({
    matches: matches && query === WORKBENCH_QUERY,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  }));
}

async function openReview() {
  const user = userEvent.setup();
  render(<DemoDetailPage />);
  await screen.findByRole("region", { name: "播放控制" });
  await user.selectOptions(screen.getByRole("combobox", { name: /复盘玩家/ }), T_ENTRY_ID);
  await screen.findByRole("heading", { name: /^正在复盘 / });
  return user;
}

const stage = () => screen.getByRole("region", { name: "回放" });

describe("DemoDetailPage review workbench (S15)", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({
      state: {
        status: "authenticated",
        account: { displayName: "Local development", avatarUrl: null, provider: "development" },
        capabilities: { devTools: false, renderClips: true }
      },
      provider: "steam",
      refreshSession: vi.fn(async () => true),
      signIn: vi.fn(),
      signOut: vi.fn(async () => {}),
      markSignedOut: vi.fn()
    });
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent({ id: "first", tick_start: 400, tick_end: 400 })]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus({ connected: true, status: "connected" }));
  });

  afterEach(() => {
    vi.stubGlobal("matchMedia", setupMatchMedia);
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("puts the breadcrumb in the top bar, with the match as the current page, in every layout", async () => {
    viewportFitsWorkbench(false);
    await openReview();

    const trail = screen.getByRole("navigation", { name: "当前位置" });
    expect(trail.closest(".topbar")).not.toBeNull();
    expect(within(trail).getByRole("link", { name: "我的比赛" })).toHaveAttribute("href", "/dashboard");
    expect(within(trail).getByText("Mock Match demo-1")).toHaveAttribute("aria-current", "page");
    // One way back to the library: no second breadcrumb row on the page, no second nav link.
    expect(document.querySelector(".review-breadcrumb")).toBeNull();
    expect(screen.getAllByRole("link", { name: "我的比赛" })).toHaveLength(1);
    // The demo name stays the page heading for screen readers.
    expect(screen.getByRole("heading", { level: 1, name: "Mock Match demo-1" })).toBeInTheDocument();
  });

  it("keeps the stacked page when the screen is too small for the workbench", async () => {
    viewportFitsWorkbench(false);
    await openReview();

    expect(document.querySelector("main")).not.toHaveClass("is-workbench");
    expect(document.querySelector(".match-banner")).not.toHaveClass("compact");
    // 回合记录 above the workspace, the full strip; the finding row under the stage tabs.
    const strip = document.querySelector(".round-strip")!;
    expect(stage().contains(strip)).toBe(false);
    expect(strip).not.toHaveClass("compact");
    expect(document.querySelector(".replay-workbench")).toBeNull();
    expect(stage().querySelector(".timeline-panel")).not.toHaveClass("compact");
    expect(stage().querySelector(".review-command-bar")).not.toHaveClass("compact");
    expect(stage().querySelector(".review-stage-toolbar .review-finding-slot")).toBeNull();
    expect(stage().querySelector(":scope > .review-finding-slot")).not.toBeNull();
  });

  it("lays the review out as one screen on a wide and tall screen", async () => {
    viewportFitsWorkbench(true);
    const user = await openReview();

    expect(document.querySelector("main")).toHaveClass("is-workbench");
    // The band: the compact banner shows the round count; the halves move into the team's tooltip.
    const banner = document.querySelector(".match-banner")!;
    expect(banner).toHaveClass("compact");
    expect(banner.querySelector(".match-banner-meta")).toHaveTextContent("2 回合");
    expect(banner.querySelector(".match-banner-halves")).toBeNull();
    expect(banner.querySelector(".team-a")).toHaveAttribute("title", expect.stringContaining("上半场"));
    // The stage: rosters beside the map, then the dock (回合记录 in one row, timeline, transport).
    expect(stage().querySelector(".replay-panel")).toHaveClass("replay-workbench");
    const strip = stage().querySelector(".round-strip")!;
    expect(strip).toHaveClass("compact");
    expect(within(stage()).getByRole("group", { name: "回合列表" })).toBeInTheDocument();
    const timeline = stage().querySelector(".timeline-panel")!;
    const transport = within(stage()).getByRole("region", { name: "播放控制" });
    expect(timeline).toHaveClass("compact");
    expect(transport).toHaveClass("compact");
    expect(strip.compareDocumentPosition(timeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(timeline.compareDocumentPosition(transport) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The right column keeps 重点建议 above 本回合; the 数据 section stays below the workbench.
    const workbench = document.querySelector(".review-workbench")!;
    expect(workbench.contains(screen.getByRole("region", { name: "本回合" }))).toBe(true);
    expect(workbench.contains(screen.getByRole("region", { name: "计分板" }))).toBe(false);

    // The suggestion being watched shows inside the tab bar, so the map does not move.
    await user.click(within(document.getElementById("coaching-event-first")!).getByRole("button", { name: /查看这一刻/ }));
    const findingStrip = document.querySelector(".review-finding-strip")!;
    expect(findingStrip.closest(".review-stage-toolbar")).not.toBeNull();
    expect(findingStrip).toHaveTextContent("第 1 回合");
    expect(stage().querySelector(":scope > .review-finding-slot")).toBeNull();
  });
});
