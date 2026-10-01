import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RoundReviewPanel, RoundStrip } from "@/components/replay/RoundReviewPanel";
import { matchTeams } from "@/lib/match-stats";
import { roundEconomies } from "@/lib/round-economy";
import { T_ENTRY_ID, coachingEvent, replayData } from "@/lib/test-fixtures/review";
import { FULL_BUY, economyReplay, type EconomyRoundSpec } from "@/lib/test-fixtures/round-economy";
import type { ReplayEvent } from "@/types/replay";

// T Entry plays T in both fixture rounds: CT wins round 1 (a loss), T wins round 2.
const death: ReplayEvent = {
  id: "kill-death", type: "kill", tick: 612, roundNumber: 1, source: "parser",
  playerIds: ["enemy", T_ENTRY_ID], playerId: "enemy", playerName: "CT Anchor", label: "CT Anchor killed T Entry",
  metadata: { attackerId: "enemy", victimId: T_ENTRY_ID, attackerName: "CT Anchor", victimName: "T Entry" }
};

function renderPanel(overrides: Partial<Parameters<typeof RoundReviewPanel>[0]> = {}) {
  const props = {
    replay: replayData({ events: [death] }),
    coachingEvents: [coachingEvent()],
    currentRoundNumber: 1,
    selectedRound: 1,
    selectedPlayerId: T_ENTRY_ID,
    selectedPlayerName: "T Entry",
    // A v1 replay: the page passes [] (no player states, no economy).
    economies: [],
    onSelectRound: vi.fn(),
    onSeek: vi.fn(),
    ...overrides
  };
  // The page shows both: the strip above the stage, the selected round's jumps below it.
  const view = render(<><RoundStrip {...props} /><RoundReviewPanel {...props} /></>);
  return { ...props, rerender: (next: typeof props) => view.rerender(<><RoundStrip {...next} /><RoundReviewPanel {...next} /></>) };
}

describe("RoundReviewPanel", () => {
  it("marks rounds the reviewed player lost or died in, in words as well as colour", () => {
    renderPanel();
    const rail = screen.getByRole("group", { name: "回合列表" });
    const [first, second] = within(rail).getAllByRole("button");
    expect(first).toHaveAccessibleName("第 1 回合 CT 胜，输掉本回合，1 条建议，阵亡，正在播放");
    expect(first).toHaveClass("lost", "player-died");
    expect(second).toHaveAccessibleName("第 2 回合 T 胜，赢下本回合，0 条建议");
    expect(second).toHaveClass("won");
  });

  it("keeps the quick jumps in view with round times, including the player's own death", async () => {
    const user = userEvent.setup();
    const props = renderPanel();
    const jumps = screen.getByRole("group", { name: "快速跳转" });
    expect(within(jumps).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "回合开始0:00", "冻结时间结束0:01", "全场首杀0:08", "安装炸弹", "个人首杀", "阵亡0:08"
    ]);
    expect(within(jumps).getByRole("button", { name: /^安装炸弹/ })).toBeDisabled();
    await user.click(within(jumps).getByRole("button", { name: /^阵亡/ }));
    expect(props.onSeek).toHaveBeenCalledWith(612);
    // Player-facing values are round times; the raw tick only survives as a hover hint.
    expect(document.querySelector(".round-ribbon-metrics")).not.toHaveTextContent(/Tick|612/);
  });

  it("fills each round with the side that won it and marks the switch of sides", () => {
    // Round 2's frames put everyone on the other side: that is half time.
    const replay = replayData({ events: [death] });
    const swapped = {
      ...replay,
      frames: replay.frames.map((frame) => frame.roundNumber === 2
        ? { ...frame, players: frame.players.map((player) => ({ ...player, side: player.side === "T" ? "CT" as const : "T" as const })) }
        : frame)
    };
    renderPanel({ replay: swapped, selectedPlayerId: null, selectedPlayerName: null });
    const rail = screen.getByRole("group", { name: "回合列表" });
    const [first, second] = within(rail).getAllByRole("button");
    expect(first).toHaveClass("winner-ct");
    expect(first).not.toHaveClass("winner-t");
    expect(second).toHaveClass("winner-t");
    expect(first.nextElementSibling).toHaveClass("round-strip-half");
    expect(rail.querySelectorAll(".round-strip-half")).toHaveLength(1);
    // The selected (current) round carries the pressed state the chalk outline is drawn from.
    expect(first).toHaveAttribute("aria-pressed", "true");
    expect(second).toHaveAttribute("aria-pressed", "false");
    // One dot per suggestion under the number.
    expect(first.querySelector(".round-strip-dots")?.children).toHaveLength(1);
  });

  it("moves between rounds with the arrow, Home and End keys and keeps one Tab stop", async () => {
    const user = userEvent.setup();
    const props = renderPanel();
    const [first, second] = within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");
    expect([first.tabIndex, second.tabIndex]).toEqual([0, -1]);

    first.focus();
    await user.keyboard("{ArrowRight}");
    expect(second).toHaveFocus();
    expect(props.onSelectRound).toHaveBeenLastCalledWith(2);

    props.rerender({ ...props, selectedRound: 2, currentRoundNumber: 2 });
    expect([first.tabIndex, second.tabIndex]).toEqual([-1, 0]);
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
    expect(props.onSelectRound).toHaveBeenLastCalledWith(1);
    await user.keyboard("{End}");
    expect(second).toHaveFocus();
    expect(props.onSelectRound).toHaveBeenLastCalledWith(2);
    // Already at the last round: the key is swallowed, nothing else is selected.
    vi.mocked(props.onSelectRound).mockClear();
    await user.keyboard("{ArrowRight}");
    expect(props.onSelectRound).not.toHaveBeenCalled();
  });

  it("draws how each round ended as an icon, says it in words and names the icons in the legend", () => {
    const [first, second] = replayData().rounds;
    renderPanel({
      replay: replayData({
        events: [death],
        rounds: [{ ...first, winnerReason: "bomb_defused" }, { ...second, winnerReason: "bomb_exploded" }]
      })
    });
    const [defused, exploded] = within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");
    expect(defused).toHaveAccessibleName("第 1 回合 CT 胜，拆除炸弹，输掉本回合，1 条建议，阵亡，正在播放");
    expect(exploded).toHaveAccessibleName("第 2 回合 T 胜，炸弹爆炸，赢下本回合，0 条建议");
    expect(exploded).toHaveAttribute("title", "第 2 回合 T 胜，炸弹爆炸，赢下本回合，0 次击杀，0 条建议");
    // The icon sits in the side-coloured square and is hidden from assistive tech with it.
    expect(defused.querySelector(".round-strip-fill[aria-hidden='true'] svg.round-strip-reason")).not.toBeNull();
    expect(exploded.querySelector(".round-strip-fill svg.round-strip-reason")).not.toBeNull();
    // One legend, under the track (the bar keeps its room for the economy filter); no economy key on a v1 replay.
    expect(document.querySelector(".round-strip-head .round-strip-legend")).toBeNull();
    const legend = document.querySelector(".round-strip > .round-strip-legend");
    expect(legend).toHaveTextContent("T 胜CT 胜炸弹爆炸拆除炸弹全歼时间耗尽阵亡建议");
    expect(legend?.querySelectorAll("svg.round-strip-legend-reason")).toHaveLength(4);
  });

  it("draws no icon for a round whose end reason is missing or unknown", () => {
    const [first, second] = replayData().rounds;
    renderPanel({ replay: replayData({ rounds: [first, { ...second, winnerReason: "surrender" }] }) });
    const cells = within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");
    expect(cells.map((cell) => cell.querySelector(".round-strip-reason"))).toEqual([null, null]);
    expect(cells[1]).toHaveAccessibleName("第 2 回合 T 胜，赢下本回合，0 条建议");
  });

  it("leaves out outcome and personal jumps when nobody is being reviewed", () => {
    renderPanel({ selectedPlayerId: null, selectedPlayerName: null, coachingEvents: [] });
    const [first] = within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");
    expect(first).toHaveAccessibleName("第 1 回合 CT 胜，0 条建议，正在播放");
    expect(within(screen.getByRole("group", { name: "快速跳转" })).queryByRole("button", { name: /阵亡|个人首杀/ })).toBeNull();
  });

  it("shows no economy rows, filter or key for a replay without player states", () => {
    renderPanel();
    expect(screen.queryByRole("group", { name: "按经济类型筛选回合" })).not.toBeInTheDocument();
    expect(document.querySelector(".round-strip-teams")).toBeNull();
    expect(document.querySelector(".round-strip-econ")).toBeNull();
    expect(document.querySelector(".round-strip-legend")).not.toHaveTextContent("手枪局");
  });
});

// Five a side; MOUZ (team A) starts T, the sides swap after round 3, so round 4 is the second pistol round.
const ECONOMY_SPECS: EconomyRoundSpec[] = [
  { sideA: "T", winner: "CT", a: [700, 100], b: [850, 0] },
  { sideA: "T", winner: "T", a: [4500, 1500], b: [4200, 500] },
  { sideA: "T", winner: "T", a: FULL_BUY, b: [2500, 200] },
  { sideA: "CT", winner: "CT", a: [900, 0], b: [800, 50] },
  { sideA: "CT", winner: "T", a: [5000, 300], b: [600, 3000] },
  { sideA: "CT", winner: "T", a: FULL_BUY, b: FULL_BUY },
  { sideA: "CT", winner: "CT", a: [4000, 0], b: [2500, 1500] },
  { sideA: "CT", winner: "T", a: FULL_BUY, b: FULL_BUY }
];

describe("RoundStrip economy", () => {
  function renderStrip(overrides: Partial<Parameters<typeof RoundStrip>[0]> = {}) {
    const replay = economyReplay(ECONOMY_SPECS);
    const props = {
      replay,
      coachingEvents: [],
      currentRoundNumber: null,
      selectedRound: 1,
      selectedPlayerId: null,
      economies: roundEconomies(replay),
      teams: matchTeams(replay, [{ key: "A", name: "MOUZ" }, { key: "B", name: "Spirit" }]),
      onSelectRound: vi.fn(),
      ...overrides
    };
    render(<RoundStrip {...props} />);
    return props;
  }
  const cells = () => within(screen.getByRole("group", { name: "回合列表" })).getAllByRole("button");
  const rowText = (team: "a" | "b") => cells().map((cell) => cell.querySelector(`.round-strip-econ.team-${team}`)?.textContent);

  it("puts each team's buy under every round, with the names in a column beside them", () => {
    renderStrip();
    expect(rowText("a")).toEqual(["枪", "全", "全", "枪", "全", "全", "全", "全"]);
    expect(rowText("b")).toEqual(["枪", "全", "强", "枪", "经", "全", "半", "全"]);
    const names = document.querySelector(".round-strip-teams");
    expect(names).toHaveAttribute("aria-hidden", "true");
    expect([...(names?.children ?? [])].map((name) => name.textContent)).toEqual(["MOUZ", "Spirit"]);
    // The kind's name and both teams' equipment value are said in words and in the hover title.
    expect(cells()[2]).toHaveAccessibleName("第 3 回合 T 胜，MOUZ 全起 $22,500，Spirit 强起 $12,500，0 条建议");
    expect(cells()[2]).toHaveAttribute("title", "第 3 回合 T 胜，MOUZ 全起 $22,500，Spirit 强起 $12,500，0 次击杀，0 条建议");
    const legend = document.querySelector(".round-strip > .round-strip-legend");
    expect(legend).toHaveTextContent("枪 = 手枪局全 = 全起强 = 强起（钱花光）半 = 半起（留了钱）经 = ECO 经济局");
    // Two groups, so a wrap falls between the round key and the economy key, never inside an item.
    const groups = [...(legend?.querySelectorAll(":scope > .round-strip-legend-group") ?? [])];
    expect(groups.map((group) => group.textContent)).toEqual([
      "T 胜CT 胜炸弹爆炸拆除炸弹全歼时间耗尽阵亡建议",
      "枪 = 手枪局全 = 全起强 = 强起（钱花光）半 = 半起（留了钱）经 = ECO 经济局"
    ]);
    expect(groups[1].children).toHaveLength(5);
  });

  it("falls back to 队伍 A and 队伍 B without team names", () => {
    renderStrip({ teams: [] });
    expect(document.querySelector(".round-strip-teams")).toHaveTextContent("队伍 A队伍 B");
    expect(cells()[0]).toHaveAccessibleName("第 1 回合 CT 胜，队伍 A 手枪局 $3,500，队伍 B 手枪局 $4,250，0 条建议");
  });

  it("fades the rounds where the chosen team's buy does not match, without changing the round", async () => {
    const user = userEvent.setup();
    const props = renderStrip();
    const kinds = screen.getByRole("group", { name: "按经济类型筛选回合" });
    const teams = screen.getByRole("group", { name: "筛选哪支队伍" });
    expect(within(kinds).getAllByRole("button").map((button) => button.textContent)).toEqual(["全部", "手枪局", "全起", "强起", "半起", "ECO"]);
    expect(within(kinds).getByRole("button", { name: "全部" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelectorAll(".econ-faded")).toHaveLength(0);
    // Under 全部 the team choice filters nothing, so it is unavailable rather than a pressed filter.
    const mouz = within(teams).getByRole("button", { name: "MOUZ" });
    const spirit = within(teams).getByRole("button", { name: "Spirit" });
    expect(mouz).toHaveAttribute("aria-disabled", "true");
    expect(spirit).toHaveAttribute("aria-disabled", "true");
    await user.click(spirit);
    expect(mouz).toHaveAttribute("aria-pressed", "true");
    // The count is a live region from the start (an inserted one is not announced), empty under 全部.
    const count = document.querySelector(".round-strip-filter-count");
    expect(count).toHaveAttribute("aria-live", "polite");
    expect(count).toHaveAttribute("aria-atomic", "true");
    expect(count).toBeEmptyDOMElement();

    // MOUZ never forced: every round fades.
    await user.click(within(kinds).getByRole("button", { name: "强起" }));
    expect(mouz).not.toHaveAttribute("aria-disabled");
    expect(cells().filter((cell) => cell.classList.contains("econ-faded"))).toHaveLength(8);
    expect(count).toHaveTextContent(/^MOUZ 强起：0 个回合$/);
    await user.click(spirit);
    expect(spirit).toHaveAttribute("aria-pressed", "true");
    expect(cells().map((cell) => cell.classList.contains("econ-faded"))).toEqual([true, true, false, true, true, true, true, true]);
    expect(cells()[2]).not.toHaveAccessibleName(/不符合筛选/);
    expect(cells()[4]).toHaveAccessibleName(/，不符合筛选$/);
    // Said in full; on screen only the count, after the team segments.
    expect(count).toHaveTextContent(/^Spirit 强起：1 个回合$/);
    expect(count?.querySelector(".visually-hidden")).toHaveTextContent("Spirit 强起：");
    // The filtered team's name and its letters in the matching rounds stand out.
    expect(screen.getByRole("group", { name: "回合列表" })).toHaveClass("filter-team-b");

    await user.click(within(kinds).getByRole("button", { name: "ECO" }));
    expect(cells().map((cell) => cell.classList.contains("econ-faded"))).toEqual([true, true, true, true, false, true, true, true]);
    // Filtering never touched the shared round; a faded round is still a click away.
    expect(props.onSelectRound).not.toHaveBeenCalled();
    await user.click(cells()[0]);
    expect(props.onSelectRound).toHaveBeenLastCalledWith(1);

    await user.click(within(kinds).getByRole("button", { name: "全部" }));
    expect(document.querySelectorAll(".econ-faded")).toHaveLength(0);
    expect(count).toBeEmptyDOMElement();
    expect(screen.getByRole("group", { name: "回合列表" })).not.toHaveClass("filter-team-b");
  });

  describe("on a narrow strip", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    // A 150 px track: the 60 px team column, then 32 px per round from x = 70, minus the scroll.
    function layOut(track: HTMLElement) {
      vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function rect(this: Element) {
        const box = (left: number, width: number) => ({ left, right: left + width, width, top: 0, bottom: 80, height: 80, x: left, y: 0, toJSON: () => ({}) }) as DOMRect;
        if (this === track) return box(0, 150);
        if (this.classList.contains("round-strip-teams")) return box(0, 60);
        const index = cells().indexOf(this as HTMLElement);
        return index >= 0 ? box(70 + index * 32 - track.scrollLeft, 30) : box(0, 0);
      });
    }

    it("brings the first match into view when none is in view, without changing the round", async () => {
      const user = userEvent.setup();
      const props = renderStrip();
      const track = screen.getByRole("group", { name: "回合列表" });
      layOut(track);
      const scrollTo = vi.spyOn(track, "scrollTo");

      // Spirit's only ECO is round 5, at x = 198: off the 150 px track.
      await user.click(within(screen.getByRole("group", { name: "按经济类型筛选回合" })).getByRole("button", { name: "ECO" }));
      await user.click(within(screen.getByRole("group", { name: "筛选哪支队伍" })).getByRole("button", { name: "Spirit" }));
      // Scrolled so round 5 sits 24 px clear of the team column: 198 - 60 - 24.
      expect(scrollTo).toHaveBeenLastCalledWith({ left: 114, behavior: "smooth" });
      expect(props.onSelectRound).not.toHaveBeenCalled();

      // MOUZ's pistol rounds (1 at x = 70) are already in view: no scroll.
      scrollTo.mockClear();
      await user.click(within(screen.getByRole("group", { name: "按经济类型筛选回合" })).getByRole("button", { name: "手枪局" }));
      await user.click(within(screen.getByRole("group", { name: "筛选哪支队伍" })).getByRole("button", { name: "MOUZ" }));
      expect(scrollTo).not.toHaveBeenCalled();
    });
  });

  it("keeps one Tab stop and the arrow keys across faded rounds", async () => {
    const user = userEvent.setup();
    const props = renderStrip();
    await user.click(within(screen.getByRole("group", { name: "按经济类型筛选回合" })).getByRole("button", { name: "手枪局" }));
    const all = cells();
    expect(all.map((cell) => cell.tabIndex)).toEqual([0, -1, -1, -1, -1, -1, -1, -1]);
    all[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(all[1]).toHaveFocus();
    expect(props.onSelectRound).toHaveBeenLastCalledWith(2);
    await user.keyboard("{End}");
    expect(all[7]).toHaveFocus();
    expect(props.onSelectRound).toHaveBeenLastCalledWith(8);
  });
});
