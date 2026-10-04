import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import type { AuthCapabilities } from "@/lib/auth";
import { replayV1, replayV2 } from "@/lib/test-fixtures/replay-v2";
import { coachingEvent, demoStatus, ingestion, renderWorkerStatus, replayVideo } from "@/lib/test-fixtures/review";
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

function mockAuth(capabilities: AuthCapabilities) {
  vi.mocked(useAuth).mockReturnValue({
    state: {
      status: "authenticated",
      account: { displayName: "Local development", avatarUrl: null, provider: "development" },
      capabilities
    },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {}),
    markSignedOut: vi.fn()
  });
}

function slider() {
  return screen.getByRole("slider", { name: "拖动定位回放" });
}

function analysisPanel() {
  return screen.queryByRole("complementary", { name: "道具投掷分析" });
}

const setupMatchMedia = window.matchMedia;

// The workbench media query answers true; every other query (reduced motion…) stays false.
function viewportFitsWorkbench() {
  vi.stubGlobal("matchMedia", (query: string): MediaQueryList => ({
    matches: query === WORKBENCH_QUERY,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  }));
}

function viewSwitch() {
  return screen.getByRole("group", { name: "回放视图" });
}

async function openReview() {
  const user = userEvent.setup();
  render(<DemoDetailPage />);
  await screen.findByRole("region", { name: "播放控制" });
  return user;
}

describe("DemoDetailPage 道具反查 (S10)", () => {
  beforeEach(() => {
    mockAuth({ devTools: false, renderClips: false });
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent()]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus());
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({ map_name: "de_mirage" }));
    vi.mocked(api.getReplay).mockResolvedValue(replayV2());
  });

  afterEach(() => {
    vi.stubGlobal("matchMedia", setupMatchMedia);
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("sits between 战术回放 and 第一人称 and draws the throws on the playback map", async () => {
    mockAuth({ devTools: false, renderClips: true });
    const { container } = render(<DemoDetailPage />);
    await screen.findByRole("region", { name: "播放控制" });
    expect(within(viewSwitch()).getAllByRole("button").map((tab) => tab.textContent)).toEqual(["战术回放", "道具反查", "第一人称"]);
    // The playback map carries the utility layer through the viewer's top overlay slot, over the players.
    expect(container.querySelector(".map-overlay-slot.above")).not.toBeNull();
  });

  it("jumps from a throw to 战术回放 two seconds before it and watches the thrower until 显示全部", async () => {
    const user = await openReview();
    expect(within(viewSwitch()).getAllByRole("button").map((tab) => tab.textContent)).toEqual(["战术回放", "道具反查"]);
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    expect(within(viewSwitch()).getByRole("button", { name: "道具反查" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "烟雾弹 1 颗" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "战术地图" })).toBeNull();

    // A row opens the throw's analysis; 在战术回放里看 is the old 看这颗.
    await user.click(screen.getByRole("button", { name: /^第 1 回合.*Alpha.*分析$/ }));
    await user.click(within(analysisPanel() as HTMLElement).getByRole("button", { name: "在战术回放里看" }));
    // Thrown at tick 300; 2 s at 64 ticks earlier is 172, after freeze time ended (164).
    expect(slider()).toHaveValue("172");
    expect(within(viewSwitch()).getByRole("button", { name: "战术回放" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "战术地图" })).toBeInTheDocument();
    const strip = screen.getByText("只看").closest(".review-focus-strip") as HTMLElement;
    expect(strip).toHaveTextContent("只看 Alpha");
    // Everyone but the thrower fades on the map; the thrower keeps a name label.
    expect(document.querySelectorAll(".map-player-dot.focus-dimmed")).toHaveLength(3);
    expect(document.querySelector(".map-player-dot.focus-focused .map-player-name")).toHaveTextContent("Alpha");

    await user.click(within(strip).getByRole("button", { name: "显示全部" }));
    expect(screen.queryByText("只看")).toBeNull();
    expect(document.querySelectorAll(".map-player-dot.focus-dimmed")).toHaveLength(0);
    // The strip is gone with its button; focus lands on the stage, not the page.
    expect(document.activeElement).toBe(document.getElementById("player"));
  });

  it("keeps the finder's filters across a trip to the map, and Play leaves it for the map", async () => {
    const user = await openReview();
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    await user.click(within(screen.getByRole("group", { name: "道具类型" })).getByRole("button", { name: "闪光" }));
    expect(screen.getByRole("heading", { name: "闪光弹 0 颗" })).toBeInTheDocument();
    await user.click(within(viewSwitch()).getByRole("button", { name: "战术回放" }));
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    expect(screen.getByRole("heading", { name: "闪光弹 0 颗" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "播放" }));
    expect(within(viewSwitch()).getByRole("button", { name: "战术回放" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "战术地图" })).toBeInTheDocument();
  });

  it("opens a throw's analysis from its row, and ✕ goes back to the list with the filters kept", async () => {
    const user = await openReview();
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    await user.click(screen.getByRole("button", { name: /^第 1 回合.*Alpha.*分析$/ }));

    const panel = analysisPanel() as HTMLElement;
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent("Alpha");
    // The panel takes the filters' place; the map draws only this throw.
    expect(screen.queryByRole("group", { name: "道具类型" })).toBeNull();
    expect(document.querySelector(".utility-finder")).toHaveClass("analyzing");
    expect(document.querySelectorAll(".utility-finder-throw")).toHaveLength(0);
    expect(document.querySelector(".utility-finder-map .throw-analysis-layer")).not.toBeNull();
    // Still 道具反查: the analysis runs on its own clock and leaves the page's playhead alone.
    expect(within(viewSwitch()).getByRole("button", { name: "道具反查" })).toHaveAttribute("aria-pressed", "true");

    await user.click(within(panel).getByRole("button", { name: "关闭分析" }));
    expect(analysisPanel()).toBeNull();
    expect(screen.getByRole("heading", { name: "烟雾弹 1 颗" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "回合范围" })).getByRole("button", { name: "本回合（1）" }))
      .toHaveAttribute("aria-pressed", "true");
  });

  it("opens 道具反查 on the analysis of a grenade clicked in flight on 战术回放", async () => {
    // Round 1 at tick 340: Alpha's smoke (thrown at 300) is in the air until 380.
    window.history.replaceState(null, "", "/demos/demo-1?r=1&t=340");
    const user = await openReview();
    expect(slider()).toHaveValue("340");
    // Filters left over from earlier get reset to the throw: its kind, everyone, this round.
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    await user.click(within(screen.getByRole("group", { name: "道具类型" })).getByRole("button", { name: "闪光" }));
    await user.click(within(screen.getByRole("group", { name: "回合范围" })).getByRole("button", { name: "全部" }));
    await user.click(within(viewSwitch()).getByRole("button", { name: "战术回放" }));

    const marker = document.querySelector('.utility-flight[data-utility-id="utility-smoke-301-300"] .utility-flight-select');
    expect(marker).not.toBeNull();
    expect(marker?.querySelector("title")).toHaveTextContent("分析这颗道具");
    fireEvent.click(marker?.querySelector(".utility-flight-hit") as Element);

    expect(within(viewSwitch()).getByRole("button", { name: "道具反查" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("region", { name: "战术地图" })).toBeNull();
    expect(analysisPanel()).toHaveTextContent("Alpha");
    expect(analysisPanel()).toHaveTextContent("烟雾弹");
    // The page clock stays where the click was.
    expect(slider()).toHaveValue("340");

    await user.click(within(analysisPanel() as HTMLElement).getByRole("button", { name: "关闭分析" }));
    expect(screen.getByRole("heading", { name: "烟雾弹 1 颗" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "回合范围" })).getByRole("button", { name: "本回合（1）" }))
      .toHaveAttribute("aria-pressed", "true");
  });

  it("gives the map the dock's height while an analysis is open in the workbench", async () => {
    viewportFitsWorkbench();
    const user = await openReview();
    const stage = screen.getByRole("region", { name: "回放" });
    expect(document.querySelector("main")).toHaveClass("is-workbench");
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    expect(stage).not.toHaveClass("analysis-open");

    await user.click(screen.getByRole("button", { name: /^第 1 回合.*Alpha.*分析$/ }));
    expect(stage).toHaveClass("analysis-open");
    // Hidden by CSS only: the dock stays mounted.
    expect(stage.querySelector(".timeline-panel")).not.toBeNull();
    expect(stage.querySelector(".review-command-bar")).not.toBeNull();

    // Back on 战术回放 the dock returns, and with it 道具反查 reopens on the same analysis.
    await user.click(within(viewSwitch()).getByRole("button", { name: "战术回放" }));
    expect(stage).not.toHaveClass("analysis-open");
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    expect(stage).toHaveClass("analysis-open");

    await user.click(within(analysisPanel() as HTMLElement).getByRole("button", { name: "关闭分析" }));
    expect(stage).not.toHaveClass("analysis-open");
  });

  it("offers the tab on an old match only while the background upgrade is pending", async () => {
    vi.mocked(api.getReplay).mockResolvedValue(replayV1());
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({
      map_name: "de_mirage",
      ingestion: ingestion({ replayUpgradePending: true })
    }));
    const user = await openReview();
    await user.click(within(viewSwitch()).getByRole("button", { name: "道具反查" }));
    expect(screen.getByText("这场比赛还在补充道具数据，稍后刷新")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /颗$/ })).toBeNull();
  });

  it("hides the tab on an old match that is not being upgraded", async () => {
    vi.mocked(api.getReplay).mockResolvedValue(replayV1());
    await openReview();
    expect(screen.queryByRole("group", { name: "回放视图" })).toBeNull();
    expect(screen.queryByRole("button", { name: "道具反查" })).toBeNull();
    expect(document.querySelector(".map-overlay-slot")).toBeNull();
  });
});
