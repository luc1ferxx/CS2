import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_UTILITY_FINDER_STATE,
  UtilityFinder,
  UtilityFinderPending,
  type UtilityFinderState
} from "@/components/replay/UtilityFinder";
import { matchTeams } from "@/lib/match-stats";
import { V2_BRAVO, V2_CHARLIE, V2_DELTA, replayV2 } from "@/lib/test-fixtures/replay-v2";
import type { ReplayData, ReplayUtility } from "@/types/replay";

function thrown(id: string, overrides: Partial<ReplayUtility> & Pick<ReplayUtility, "points">): ReplayUtility {
  const first = overrides.points[0].tick;
  const last = overrides.points[overrides.points.length - 1].tick;
  return {
    id, type: "smoke", throwerId: null, throwerName: null, throwerSide: null, roundNumber: 1,
    throwTick: first, detonateTick: last, endTick: last, ...overrides
  };
}

// Round 1: Alpha's smoke (fixture) lands at 40,42; Charlie's at 70,20. Round 2: Bravo's at 42,45; Delta flashes.
function finderReplay(): ReplayData {
  const base = replayV2();
  return {
    ...base,
    utility: [
      ...(base.utility ?? []),
      thrown("utility-smoke-401-420", {
        throwerId: V2_CHARLIE, throwerName: "Charlie", throwerSide: "CT",
        points: [{ tick: 420, x: 80, y: 30 }, { tick: 470, x: 70, y: 20 }]
      }),
      thrown("utility-smoke-402-1200", {
        throwerId: V2_BRAVO, throwerName: "Bravo", throwerSide: "T", roundNumber: 2,
        points: [{ tick: 1200, x: 30, y: 40 }, { tick: 1260, x: 42, y: 45 }]
      }),
      thrown("utility-flash-403-1300", {
        type: "flash", throwerId: V2_DELTA, throwerName: "Delta", throwerSide: "CT", roundNumber: 2,
        points: [{ tick: 1300, x: 15, y: 15 }, { tick: 1330, x: 10, y: 10 }]
      })
    ]
  };
}

function Harness({ replay, currentRound = 1, onJump = () => {} }: {
  replay: ReplayData;
  currentRound?: number | null;
  onJump?: (utility: ReplayUtility) => void;
}) {
  const [state, setState] = useState<UtilityFinderState>(DEFAULT_UTILITY_FINDER_STATE);
  return (
    <div className="review-app">
      <UtilityFinder replay={replay} teams={matchTeams(replay)} currentRound={currentRound} state={state}
        onStateChange={setState} onJump={onJump} />
    </div>
  );
}

// A 500 px square map: 5 px per radar percent.
function mockSquare(container: HTMLElement): SVGSVGElement {
  const svg = container.querySelector(".utility-finder-map svg") as SVGSVGElement;
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500, toJSON: () => ({})
  });
  return svg;
}

function count() {
  return screen.getByRole("heading", { level: 3 });
}

function rows() {
  return screen.queryAllByRole("button", { name: /看这颗$/ });
}

// jsdom has no PointerEvent; without one the pointer coordinates never reach the handlers.
beforeAll(() => {
  if (typeof window.PointerEvent === "function") return;
  class PointerEventStub extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  Object.defineProperty(window, "PointerEvent", { configurable: true, writable: true, value: PointerEventStub });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("UtilityFinder", () => {
  it("lists every smoke by default with the round clock counted from freeze end", () => {
    render(<Harness replay={finderReplay()} />);
    expect(count()).toHaveTextContent("烟雾弹 3 颗");
    expect(rows().map((row) => row.textContent)).toEqual([
      "第 1 回合0:02Alpha看这颗",
      "第 1 回合0:04Charlie看这颗",
      "第 2 回合0:02Bravo看这颗"
    ]);
    expect(screen.getByRole("button", { name: "烟雾" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters by kind, team, player and rounds", async () => {
    const user = userEvent.setup();
    render(<Harness replay={finderReplay()} currentRound={2} />);
    const kinds = screen.getByRole("group", { name: "道具类型" });
    await user.click(within(kinds).getByRole("button", { name: "闪光" }));
    expect(count()).toHaveTextContent("闪光弹 1 颗");
    await user.click(within(kinds).getByRole("button", { name: "烟雾" }));

    const throwers = screen.getByRole("group", { name: "投掷者" });
    await user.click(within(throwers).getByRole("button", { name: "队伍 B" }));
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:04Charlie看这颗"]);
    // The player list follows the team.
    expect(within(screen.getByRole("combobox", { name: "投掷玩家" })).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["全部玩家", "Charlie", "Delta"]);
    await user.click(within(throwers).getByRole("button", { name: "全部" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "投掷玩家" }), V2_BRAVO);
    expect(rows().map((row) => row.textContent)).toEqual(["第 2 回合0:02Bravo看这颗"]);
    await user.selectOptions(screen.getByRole("combobox", { name: "投掷玩家" }), "");

    const scopes = screen.getByRole("group", { name: "回合范围" });
    await user.click(within(scopes).getByRole("button", { name: "本回合（2）" }));
    expect(count()).toHaveTextContent("烟雾弹 1 颗");
    await user.click(within(scopes).getByRole("button", { name: "上半场" }));
    expect(count()).toHaveTextContent("烟雾弹 3 颗");
    await user.click(within(scopes).getByRole("button", { name: "下半场" }));
    expect(count()).toHaveTextContent("烟雾弹 0 颗");
    expect(screen.getByText("没有符合条件的烟雾弹。")).toBeInTheDocument();
  });

  it("selects a landing area with a drag and clears it again", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness replay={finderReplay()} />);
    const svg = container.querySelector(".utility-finder-map svg") as SVGSVGElement;
    // 500 px square: 5 px per radar percent.
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500, toJSON: () => ({})
    });

    fireEvent.pointerDown(svg, { button: 0, pointerId: 1, clientX: 35 * 5, clientY: 38 * 5 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 45 * 5, clientY: 50 * 5 });
    expect(screen.getByTestId("utility-selection")).toHaveClass("drafting");
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 45 * 5, clientY: 50 * 5 });

    expect(count()).toHaveTextContent("落在选区里的烟雾弹 2 颗");
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:02Alpha看这颗", "第 2 回合0:02Bravo看这颗"]);
    expect(container.querySelector('[data-utility-id="utility-smoke-401-420"]')).toHaveClass("dimmed");
    // A click without a drag leaves the selection alone.
    fireEvent.pointerDown(svg, { button: 0, pointerId: 2, clientX: 5, clientY: 5 });
    fireEvent.pointerUp(svg, { pointerId: 2, clientX: 6, clientY: 6 });
    expect(count()).toHaveTextContent("落在选区里的烟雾弹 2 颗");

    await user.click(screen.getByRole("button", { name: "清除选区" }));
    expect(count()).toHaveTextContent("烟雾弹 3 颗");
    expect(screen.queryByTestId("utility-selection")).toBeNull();
    expect(screen.queryByRole("button", { name: "清除选区" })).toBeNull();
  });

  it("starts a selection on a trajectory too, and only a press in place on one picks it", () => {
    const onJump = vi.fn();
    const { container } = render(<Harness replay={finderReplay()} onJump={onJump} />);
    const svg = mockSquare(container);
    const path = container.querySelector('[data-utility-id="utility-smoke-401-420"] .utility-finder-hit') as Element;

    // A drag that begins on Charlie's trajectory still draws the rectangle.
    fireEvent.pointerDown(path, { button: 0, pointerId: 1, clientX: 75 * 5, clientY: 25 * 5 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 36 * 5, clientY: 50 * 5 });
    expect(screen.getByTestId("utility-selection")).toHaveClass("drafting");
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 36 * 5, clientY: 50 * 5 });
    expect(onJump).not.toHaveBeenCalled();
    expect(count()).toHaveTextContent("落在选区里的烟雾弹 2 颗");

    fireEvent.pointerDown(path, { button: 0, pointerId: 2, clientX: 75 * 5, clientY: 25 * 5 });
    fireEvent.pointerUp(svg, { pointerId: 2, clientX: 75 * 5 + 1, clientY: 25 * 5 });
    expect(onJump).toHaveBeenCalledTimes(1);
    expect(onJump).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-401-420" }));
    expect(count()).toHaveTextContent("落在选区里的烟雾弹 2 颗");
  });

  it("follows the floor on screen on a two-floor map, in the list and its count", async () => {
    const user = userEvent.setup();
    const replay = finderReplay();
    const lowered = (replay.utility ?? []).map((utility) => utility.id === "utility-smoke-401-420"
      ? { ...utility, points: utility.points.map((point) => ({ ...point, z: -700 })) } : utility);
    render(<Harness replay={{ ...replay, mapName: "de_nuke", utility: lowered }} />);
    expect(count()).toHaveTextContent("上层的烟雾弹 2 颗");
    expect(rows().map((row) => row.textContent)).not.toContain("第 1 回合0:04Charlie看这颗");
    await user.selectOptions(screen.getByRole("combobox", { name: "落点图楼层" }), "lower");
    // Bravo's smoke has no height: it shows on both floors.
    expect(count()).toHaveTextContent("下层的烟雾弹 2 颗");
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:04Charlie看这颗", "第 2 回合0:02Bravo看这颗"]);
  });

  it("hands the chosen throw to the page from a row or from its trajectory", async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    const { container } = render(<Harness replay={finderReplay()} onJump={onJump} />);
    await user.click(rows()[2]);
    expect(onJump).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-402-1200", throwTick: 1200 }));
    const svg = mockSquare(container);
    const trajectory = container.querySelector('[data-utility-id="utility-smoke-401-420"] .utility-finder-path') as Element;
    fireEvent.pointerDown(trajectory, { button: 0, pointerId: 3, clientX: 70 * 5, clientY: 20 * 5 });
    fireEvent.pointerUp(svg, { pointerId: 3, clientX: 70 * 5, clientY: 20 * 5 });
    expect(onJump).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-401-420" }));
    // Rows are plain buttons: the keyboard reaches them without the map.
    rows()[0].focus();
    await user.keyboard("{Enter}");
    expect(onJump).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-301-300" }));
  });

  it("says the data is still coming for a match waiting on the upgrade", () => {
    render(<UtilityFinderPending />);
    expect(screen.getByRole("status")).toHaveTextContent("这场比赛还在补充道具数据，稍后刷新");
  });
});
