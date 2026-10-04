import { act, fireEvent, render, screen, within } from "@testing-library/react";
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

// Most cases look across the whole match; the default (this round) has its own case.
const ALL_ROUNDS: UtilityFinderState = { ...DEFAULT_UTILITY_FINDER_STATE, roundScope: "all" };

function Harness({ replay, currentRound = 1, onJump = () => {}, onState, initial = ALL_ROUNDS }: {
  replay: ReplayData;
  currentRound?: number | null;
  onJump?: (utility: ReplayUtility) => void;
  onState?: (state: UtilityFinderState) => void;
  initial?: UtilityFinderState;
}) {
  const [state, setState] = useState<UtilityFinderState>(initial);
  return (
    <div className="review-app">
      <UtilityFinder replay={replay} teams={matchTeams(replay)} currentRound={currentRound} state={state}
        onStateChange={(next) => {
          onState?.(next);
          setState(next);
        }} onJump={onJump} />
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
  return screen.queryAllByRole("button", { name: /分析$/ });
}

function panel() {
  return screen.queryByRole("complementary", { name: "道具投掷分析" });
}

function mapViewBox(container: HTMLElement): string | null {
  return container.querySelector(".utility-finder-map svg")?.getAttribute("viewBox") ?? null;
}

// Under prefers-reduced-motion the map's zoom is applied at once instead of tweened.
function reduceMotion() {
  vi.stubGlobal("matchMedia", (query: string): MediaQueryList => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  }));
}

const setupMatchMedia = window.matchMedia;

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
  vi.stubGlobal("matchMedia", setupMatchMedia);
});

describe("UtilityFinder", () => {
  it("opens on the reviewed round's smokes", () => {
    render(<Harness replay={finderReplay()} currentRound={2} initial={DEFAULT_UTILITY_FINDER_STATE} />);
    expect(screen.getByRole("button", { name: "本回合（2）" })).toHaveAttribute("aria-pressed", "true");
    expect(count()).toHaveTextContent("烟雾弹 1 颗");
    expect(rows().map((row) => row.textContent)).toEqual(["第 2 回合0:02Bravo分析"]);
  });

  it("lists every smoke across the match with the round clock counted from freeze end", () => {
    render(<Harness replay={finderReplay()} />);
    expect(count()).toHaveTextContent("烟雾弹 3 颗");
    expect(rows().map((row) => row.textContent)).toEqual([
      "第 1 回合0:02Alpha分析",
      "第 1 回合0:04Charlie分析",
      "第 2 回合0:02Bravo分析"
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
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:04Charlie分析"]);
    // The player list follows the team.
    expect(within(screen.getByRole("combobox", { name: "投掷玩家" })).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["全部玩家", "Charlie", "Delta"]);
    await user.click(within(throwers).getByRole("button", { name: "全部" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "投掷玩家" }), V2_BRAVO);
    expect(rows().map((row) => row.textContent)).toEqual(["第 2 回合0:02Bravo分析"]);
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
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:02Alpha分析", "第 2 回合0:02Bravo分析"]);
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

  it("starts a selection on a trajectory too, and only a press in place on one opens it", () => {
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

    expect(panel()).toBeNull();

    fireEvent.pointerDown(path, { button: 0, pointerId: 2, clientX: 75 * 5, clientY: 25 * 5 });
    fireEvent.pointerUp(svg, { pointerId: 2, clientX: 75 * 5 + 1, clientY: 25 * 5 });
    // The click opens the throw's analysis in place; nothing leaves for the map.
    expect(onJump).not.toHaveBeenCalled();
    expect(panel()).toHaveTextContent("Charlie");
    // ✕ goes back to the list with the selection kept.
    fireEvent.click(within(panel() as HTMLElement).getByRole("button", { name: "关闭分析" }));
    expect(count()).toHaveTextContent("落在选区里的烟雾弹 2 颗");
  });

  it("follows the floor on screen on a two-floor map, in the list and its count", async () => {
    const user = userEvent.setup();
    const replay = finderReplay();
    const lowered = (replay.utility ?? []).map((utility) => utility.id === "utility-smoke-401-420"
      ? { ...utility, points: utility.points.map((point) => ({ ...point, z: -700 })) } : utility);
    render(<Harness replay={{ ...replay, mapName: "de_nuke", utility: lowered }} />);
    expect(count()).toHaveTextContent("上层的烟雾弹 2 颗");
    expect(rows().map((row) => row.textContent)).not.toContain("第 1 回合0:04Charlie分析");
    await user.selectOptions(screen.getByRole("combobox", { name: "落点图楼层" }), "lower");
    // Bravo's smoke has no height: it shows on both floors.
    expect(count()).toHaveTextContent("下层的烟雾弹 2 颗");
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:04Charlie分析", "第 2 回合0:02Bravo分析"]);
  });

  it("opens a row's throw in the analysis in place of the filters and draws only that throw", async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    const onState = vi.fn();
    const { container } = render(<Harness replay={finderReplay()} onJump={onJump} onState={onState} />);
    await user.click(within(screen.getByRole("group", { name: "投掷者" })).getByRole("button", { name: "队伍 A" }));
    await user.click(rows()[1]);

    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ analysisId: "utility-smoke-402-1200", team: "A" }));
    expect(container.querySelector(".utility-finder")).toHaveClass("analyzing");
    expect(panel()).toHaveTextContent("Bravo");
    expect(screen.queryByRole("group", { name: "道具类型" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 3 })).toBeNull();
    expect(container.querySelectorAll(".utility-finder-throw")).toHaveLength(0);
    expect(container.querySelectorAll(".utility-finder-map .throw-analysis-layer")).toHaveLength(1);
    expect(screen.getByRole("img", { name: /Bravo的烟雾弹投掷图/ })).toBeInTheDocument();
    // The row is gone: focus moves into the panel, so Esc works straight away.
    expect(document.activeElement).toBe(within(panel() as HTMLElement).getByRole("button", { name: "关闭分析" }));

    // 在战术回放里看 is the old 看这颗: the page seeks and shows 战术回放.
    await user.click(within(panel() as HTMLElement).getByRole("button", { name: "在战术回放里看" }));
    expect(onJump).toHaveBeenCalledTimes(1);
    expect(onJump).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-402-1200", throwTick: 1200 }));

    // Esc goes back to the list, filters kept, with focus on the throw's own row.
    await user.keyboard("{Escape}");
    expect(panel()).toBeNull();
    expect(container.querySelector(".utility-finder")).not.toHaveClass("analyzing");
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ analysisId: null, team: "A" }));
    expect(rows().map((row) => row.textContent)).toEqual(["第 1 回合0:02Alpha分析", "第 2 回合0:02Bravo分析"]);
    expect(document.activeElement).toBe(rows()[1]);
  });

  it("reaches the analysis from the keyboard through the list rows", async () => {
    const user = userEvent.setup();
    render(<Harness replay={finderReplay()} />);
    // Rows are plain buttons: the keyboard reaches them without the map.
    rows()[0].focus();
    await user.keyboard("{Enter}");
    expect(panel()).toHaveTextContent("Alpha");
    await user.keyboard("{Escape}");
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(rows()[0]);
  });

  it("zooms the map onto the throw on every open, and 全图 shows the whole map again", async () => {
    reduceMotion();
    const user = userEvent.setup();
    const { container } = render(<Harness replay={finderReplay()} />);
    expect(mapViewBox(container)).toBe("0 0 100 100");
    await user.click(rows()[1]);
    const zoomed = mapViewBox(container);
    expect(zoomed).toMatch(/^[\d.]+ [\d.]+ ([\d.]+) \1$/);
    expect(zoomed).not.toBe("0 0 100 100");

    await user.click(within(panel() as HTMLElement).getByRole("button", { name: "全图" }));
    expect(mapViewBox(container)).toBe("0 0 100 100");
    await user.click(within(panel() as HTMLElement).getByRole("button", { name: "放大" }));
    expect(mapViewBox(container)).toBe(zoomed);

    // 全图 lasts for that one look: the next open zooms in again.
    await user.click(within(panel() as HTMLElement).getByRole("button", { name: "全图" }));
    await user.click(within(panel() as HTMLElement).getByRole("button", { name: "关闭分析" }));
    expect(mapViewBox(container)).toBe("0 0 100 100");
    await user.click(rows()[1]);
    expect(mapViewBox(container)).toBe(zoomed);
  });

  it("runs the slow motion from Space and K in the analysis instead of the page's playback", async () => {
    const user = userEvent.setup();
    const seen: { key: string; prevented: boolean }[] = [];
    const listener = (event: KeyboardEvent) => seen.push({ key: event.key, prevented: event.defaultPrevented });
    window.addEventListener("keydown", listener);
    try {
      render(<Harness replay={finderReplay()} initial={{ ...ALL_ROUNDS, analysisId: "utility-smoke-401-420" }} />);
      const view = within(panel() as HTMLElement);
      view.getByRole("slider", { name: "出手前后时间" }).focus();
      await user.keyboard(" ");
      expect(view.getByRole("button", { name: "暂停" })).toBeInTheDocument();
      await user.keyboard("k");
      expect(view.getByRole("button", { name: "播放" })).toBeInTheDocument();
      // The page's own shortcuts skip what the finder already handled.
      expect(seen).toEqual([{ key: " ", prevented: true }, { key: "k", prevented: true }]);
      // Space on a button still presses that button.
      view.getByRole("button", { name: "全图" }).focus();
      await user.keyboard(" ");
      expect(view.getByRole("button", { name: "全图" })).toHaveAttribute("aria-pressed", "true");
      expect(view.getByRole("button", { name: "播放" })).toBeInTheDocument();
    } finally {
      window.removeEventListener("keydown", listener);
    }
  });

  it("draws no selection while the analysis is open", () => {
    const { container } = render(<Harness replay={finderReplay()} initial={{ ...ALL_ROUNDS, analysisId: "utility-smoke-401-420" }} />);
    const svg = mockSquare(container);
    expect(panel()).toHaveTextContent("Charlie");
    fireEvent.pointerDown(svg, { button: 0, pointerId: 1, clientX: 10 * 5, clientY: 10 * 5 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 60 * 5, clientY: 60 * 5 });
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 60 * 5, clientY: 60 * 5 });
    expect(screen.queryByTestId("utility-selection")).toBeNull();
    expect(panel()).toHaveTextContent("Charlie");
  });

  it("falls back to the list when the named throw is not in this match", () => {
    render(<Harness replay={finderReplay()} initial={{ ...ALL_ROUNDS, analysisId: "utility-smoke-999-1" }} />);
    expect(panel()).toBeNull();
    expect(count()).toHaveTextContent("烟雾弹 3 颗");
  });

  it("opens a throw on the floor it landed on", () => {
    const replay = finderReplay();
    const lowered = (replay.utility ?? []).map((utility) => utility.id === "utility-smoke-401-420"
      ? { ...utility, points: utility.points.map((point) => ({ ...point, z: -700 })) } : utility);
    const onState = vi.fn();
    const { container } = render(<Harness replay={{ ...replay, mapName: "de_nuke", utility: lowered }} onState={onState} />);
    const svg = mockSquare(container);
    // On the upper floor Charlie's lower smoke is dimmed but still on the map: a click opens it.
    const path = container.querySelector('[data-utility-id="utility-smoke-401-420"] .utility-finder-hit') as Element;
    fireEvent.pointerDown(path, { button: 0, pointerId: 1, clientX: 75 * 5, clientY: 25 * 5 });
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 75 * 5, clientY: 25 * 5 });
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ analysisId: "utility-smoke-401-420", floor: "lower" }));
    expect(screen.getByRole("img", { name: /（下层）$/ })).toBeInTheDocument();
    fireEvent.click(within(panel() as HTMLElement).getByRole("button", { name: "关闭分析" }));
    expect(screen.getByRole("combobox", { name: "落点图楼层" })).toHaveValue("lower");
  });

  it("shows the thrower's keys when the match has them, and says so when it does not", () => {
    const withKeys = { ...finderReplay(), inputs: { [V2_CHARLIE]: [[400, 0], [415, 1], [421, 0]] as [number, number][] } };
    const opened = { ...ALL_ROUNDS, analysisId: "utility-smoke-401-420" };
    const { unmount } = render(<Harness replay={withKeys} initial={opened} />);
    expect(panel()).not.toHaveTextContent("这场比赛没有按键记录");
    // Opens a tick before the release (421): the left mouse button is still held.
    const keys = (panel() as HTMLElement).querySelector(".keyboard-overlay.embedded");
    expect(keys).toHaveAttribute("aria-label", expect.stringContaining("左键"));
    unmount();
    render(<Harness replay={finderReplay()} initial={opened} />);
    expect(panel()).toHaveTextContent("这场比赛没有按键记录");
  });

  // The finder's throws come through replayUtility(), which rebuilds each one field by field: the
  // command must still reach the panel from the replay's own release pose (contract v4 throwOrigin).
  it("copies the release pose as a position command, and says so when a throw has none", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const replay = finderReplay();
    const posed: ReplayData = {
      ...replay,
      utility: (replay.utility ?? []).map((utility) => utility.id === "utility-smoke-401-420"
        ? { ...utility, throwOrigin: { x: 1376.2, y: -119.3, z: -129.3, pitch: -20.33, yaw: 163.28, speed: 120, airborne: false } }
        : utility)
    };
    const command = "setpos 1376.2 -119.3 -129.3; setang -20.33 163.28 0";
    try {
      const { unmount } = render(<Harness replay={posed} initial={{ ...ALL_ROUNDS, analysisId: "utility-smoke-401-420" }} />);
      const view = within(panel() as HTMLElement);
      expect(view.getByText(command, { selector: "code" })).toBeInTheDocument();
      // No key track here: the pose's speed alone says the thrower was still moving.
      expect(panel()).toHaveTextContent("出手时还在移动：站在出手点不动扔，落点会偏");
      await act(async () => {
        fireEvent.click(view.getByRole("button", { name: "复制站位指令" }));
      });
      expect(writeText).toHaveBeenCalledWith(command);
      expect(view.getByRole("button", { name: "已复制" })).toBeInTheDocument();
      unmount();

      // Bravo's smoke has no pose (a replay from before v4, or a throw the parser could not place).
      render(<Harness replay={posed} initial={{ ...ALL_ROUNDS, analysisId: "utility-smoke-402-1200" }} />);
      expect(panel()).toHaveTextContent("这场比赛还在补充站位数据");
      expect(within(panel() as HTMLElement).queryByRole("button", { name: "复制站位指令" })).toBeNull();
    } finally {
      Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    }
  });

  it("says the data is still coming for a match waiting on the upgrade", () => {
    render(<UtilityFinderPending />);
    expect(screen.getByRole("status")).toHaveTextContent("这场比赛还在补充道具数据，稍后刷新");
  });
});
