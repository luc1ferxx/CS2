import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import type { AuthCapabilities } from "@/lib/auth";
import { replayV1, replayV2 } from "@/lib/test-fixtures/replay-v2";
import { coachingEvent, demoStatus, ingestion, renderWorkerStatus, replayVideo } from "@/lib/test-fixtures/review";

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

    await user.click(screen.getByRole("button", { name: /^第 1 回合.*Alpha.*看这颗$/ }));
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
