import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import { roundEconomies } from "@/lib/round-economy";
import { replayV1, replayV2 } from "@/lib/test-fixtures/replay-v2";
import { coachingEvent, demoStatus, renderWorkerStatus, replayVideo } from "@/lib/test-fixtures/review";

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

// Counted, not replaced: the page computes the economy once and hands it to the strip and 经济.
vi.mock("@/lib/round-economy", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/round-economy")>();
  return { ...actual, roundEconomies: vi.fn(actual.roundEconomies) };
});

const SUMMARY = {
  teams: [
    { key: "A" as const, name: "MOUZ", startSide: "T" as const, score: 1 },
    { key: "B" as const, name: "Spirit", startSide: "CT" as const, score: 1 }
  ],
  rounds: 2,
  version: 2
};

async function openReview() {
  const user = userEvent.setup();
  render(<DemoDetailPage />);
  await screen.findByRole("region", { name: "播放控制" });
  return user;
}

const roundCells = () => within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");

describe("DemoDetailPage 回合经济 (S11)", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue({
      state: {
        status: "authenticated",
        account: { displayName: "Local development", avatarUrl: null, provider: "development" },
        capabilities: { devTools: false, renderClips: false }
      },
      provider: "steam",
      refreshSession: vi.fn(async () => true),
      signIn: vi.fn(),
      signOut: vi.fn(async () => {}),
      markSignedOut: vi.fn()
    });
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent()]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus());
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({ map_name: "de_mirage", matchSummary: SUMMARY }));
    vi.mocked(api.getReplay).mockResolvedValue(replayV2());
    vi.mocked(roundEconomies).mockClear();
  });

  afterEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("puts the buys under the round strip with the stored team names and 经济 right before 计分板", async () => {
    await openReview();
    expect(document.querySelector(".round-strip-teams")).toHaveTextContent("MOUZSpirit");
    expect(roundCells().map((cell) => cell.querySelector(".round-strip-econ.team-a")?.textContent)).toEqual(["枪", expect.any(String)]);
    expect(roundCells()[0]).toHaveAccessibleName(/MOUZ 手枪局 \$\d[\d,]*，Spirit 手枪局 \$\d[\d,]*/);

    const economy = screen.getByRole("region", { name: "经济" });
    const scoreboard = screen.getByRole("region", { name: "计分板" });
    expect(economy.nextElementSibling).toBe(scoreboard);
    expect(economy.previousElementSibling).toHaveClass("match-analysis");
  });

  it("jumps to a round from the chart through the shared round state, while the strip's filter leaves it alone", async () => {
    const user = await openReview();
    expect(roundCells()[0]).toHaveAttribute("aria-pressed", "true");

    await user.click(within(screen.getByRole("group", { name: "按经济类型筛选回合" })).getByRole("button", { name: "全起" }));
    await user.click(within(screen.getByRole("group", { name: "筛选哪支队伍" })).getByRole("button", { name: "Spirit" }));
    expect(roundCells()[0]).toHaveAttribute("aria-pressed", "true");
    expect(roundCells()[0]).toHaveClass("econ-faded");

    const pairs = within(screen.getByRole("group", { name: /每回合装备价值柱状图/ })).getAllByRole("button");
    await user.click(pairs[1]);
    expect(roundCells()[1]).toHaveAttribute("aria-pressed", "true");
    expect(pairs[1]).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("region", { name: "本回合" })).toHaveTextContent("第 2 回合");
  });

  it("computes the economy once per loaded replay and shares it between the strip and 经济", async () => {
    const user = await openReview();
    await user.click(within(screen.getByRole("group", { name: "按经济类型筛选回合" })).getByRole("button", { name: "手枪局" }));
    await user.click(within(screen.getByRole("group", { name: /每回合装备价值柱状图/ })).getAllByRole("button")[1]);
    await user.click(roundCells()[0]);
    expect(roundEconomies).toHaveBeenCalledTimes(1);
  });

  it("shows no economy for a v1 replay", async () => {
    vi.mocked(api.getReplay).mockResolvedValue(replayV1());
    await openReview();
    expect(screen.queryByRole("region", { name: "经济" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按经济类型筛选回合" })).not.toBeInTheDocument();
    expect(document.querySelector(".round-strip-econ")).toBeNull();
  });
});
