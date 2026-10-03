import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { coachingCopy } from "@/lib/coaching-copy";
import { coachingEvent, deathCoachingEvent, replayPlayers, replayRounds } from "@/lib/test-fixtures/review";
import type { CoachingEvent } from "@/types/coaching";

// Wraps the real helper so a test can count how often cards render.
vi.mock("@/lib/coaching-copy", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/coaching-copy")>();
  return { ...actual, coachingCopy: vi.fn(actual.coachingCopy) };
});

type PanelProps = ComponentProps<typeof CoachingPanel>;

function panelProps(events: CoachingEvent[], overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    events,
    players: replayPlayers(),
    rounds: replayRounds(),
    tickRate: 64,
    currentTick: 100,
    selectedRound: 1,
    selectedPlayerName: "T Entry",
    renderJobByEventId: new Map(),
    requestingEventId: null,
    onSeek: vi.fn(),
    onFeedback: vi.fn(),
    ...overrides
  };
}

function renderPanel(events: CoachingEvent[], overrides: Partial<PanelProps> = {}) {
  const props = panelProps(events, overrides);
  const view = render(<CoachingPanel {...props} />);
  return { ...props, rerender: (next: Partial<PanelProps>) => view.rerender(<CoachingPanel {...props} {...next} />) };
}

function cardTitles() {
  return screen.getAllByRole("article").map((card) => card.getAttribute("aria-label"));
}

function cardFor(event: CoachingEvent) {
  return document.getElementById(`coaching-event-${event.id}`) as HTMLElement;
}

// Real rules only emit medium and low: this is the shape a real demo has.
const spacingEarly = coachingEvent({
  id: "spacing-early", tick_start: 200, tick_end: 200, severity: "low",
  structured_context_json: { ruleId: "poor_spacing" }
});
const untradedLate = coachingEvent({
  id: "untraded-late", tick_start: 600, tick_end: 600, severity: "medium",
  structured_context_json: { ruleId: "untraded_death", attackerName: "donk", windowSeconds: 5 }
});
const entryMiddle = coachingEvent({
  id: "entry-middle", tick_start: 300, tick_end: 300, severity: "medium",
  structured_context_json: { ruleId: "isolated_entry" }
});

describe("CoachingPanel", () => {
  it("leads each round with the most severe suggestion, then the earliest", () => {
    renderPanel([spacingEarly, untradedLate, entryMiddle]);

    expect(cardTitles()).toEqual([
      coachingCopy(entryMiddle).title,
      coachingCopy(untradedLate).title,
      coachingCopy(spacingEarly).title
    ]);
  });

  it("offers only the severity levels present, with their counts", async () => {
    const user = userEvent.setup();
    renderPanel([spacingEarly, untradedLate, entryMiddle]);

    await user.click(screen.getByText("筛选建议"));
    const severity = screen.getByRole("group", { name: "重要程度" });
    expect(within(severity).getAllByRole("button").map((button) => button.textContent))
      .toEqual(["全部 3", "值得留意 2", "细节建议 1"]);
    // No high findings in this match, so no filter that could only come back empty.
    expect(within(severity).queryByRole("button", { name: /优先回看/ })).not.toBeInTheDocument();

    await user.click(within(severity).getByRole("button", { name: "值得留意 2" }));
    expect(cardTitles()).toEqual([coachingCopy(entryMiddle).title, coachingCopy(untradedLate).title]);
    expect(within(severity).getByRole("button", { name: "值得留意 2" })).toHaveAttribute("aria-pressed", "true");
  });

  it("drops the severity filter when every suggestion shares one level", async () => {
    const user = userEvent.setup();
    renderPanel([spacingEarly, coachingEvent({ ...spacingEarly, id: "spacing-late", tick_start: 700 })]);

    await user.click(screen.getByText("筛选建议"));
    expect(screen.queryByRole("group", { name: "重要程度" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "建议类型" })).toBeInTheDocument();
  });

  it("names suggestion types in Chinese and finds a card by the facts it shows", async () => {
    const user = userEvent.setup();
    renderPanel([spacingEarly, untradedLate, entryMiddle]);

    await user.click(screen.getByText("筛选建议"));
    const options = within(screen.getByRole("combobox", { name: "建议类型" })).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["全部建议类型", "首杀交火缺少支援（1）", "队友站位间距（1）", "无人补枪的阵亡（1）"]);

    await user.type(screen.getByRole("textbox", { name: "搜索建议" }), "donk");
    expect(cardTitles()).toEqual([coachingCopy(untradedLate).title]);
    expect(within(cardFor(untradedLate)).getByText("donk")).toBeInTheDocument();
    expect(within(cardFor(untradedLate)).getByText("5 秒内没有队友补枪")).toBeInTheDocument();
  });

  it("asks for the viewer's player and points to the picker while nobody is selected", async () => {
    const user = userEvent.setup();
    const props = renderPanel([], { selectedPlayerName: null, onChoosePlayer: vi.fn() });

    expect(screen.getByText("选择你在这场比赛中的玩家后，这里会列出对应的建议。")).toBeInTheDocument();
    expect(screen.queryByText(/选手/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "选择玩家" }));
    expect(props.onChoosePlayer).toHaveBeenCalledTimes(1);
  });

  it("does not offer the picker once a player is being reviewed", () => {
    renderPanel([], { onChoosePlayer: vi.fn() });

    expect(screen.getByText("暂未发现值得回看的时刻，可以直接观看比赛。没有建议不代表每次选择都正确。")).toBeInTheDocument();
    expect(screen.queryByText(/线索/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "选择玩家" })).not.toBeInTheDocument();
  });

  describe("全部回合 round grid", () => {
    const inRound = (round: number, id = `round-${round}`) => coachingEvent({ id, round_number: round, tick_start: round * 1000 + 200,
      tick_end: round * 1000 + 200, structured_context_json: { ruleId: "poor_spacing" } });
    // Four rounds in the match, suggestions in rounds 1 to 3 (two in round 3), none in round 4.
    const fourRounds = [...replayRounds(),
      { roundNumber: 3, startTick: 2000, freezeEndTick: 2064, endTick: 2800, winnerSide: "CT" as const },
      { roundNumber: 4, startTick: 3000, freezeEndTick: 3064, endTick: 3800, winnerSide: "T" as const }];
    const events = [inRound(1), inRound(2), inRound(3), inRound(3, "round-3-late")];
    const cell = (round: number, count: number) => screen.getByRole("button", { name: `第 ${round} 回合，${count} 条建议` });
    const listedIds = () => Array.from(document.querySelectorAll("article"), (card) => card.id.replace("coaching-event-", ""));

    it("shows every round of the match as a cell with its count, a round without suggestions dimmed and disabled", async () => {
      const user = userEvent.setup();
      renderPanel(events, { rounds: fourRounds });
      expect(screen.queryByRole("group", { name: "按回合查看建议" })).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: /^全部回合/ }));
      const grid = screen.getByRole("group", { name: "按回合查看建议" });
      expect(within(grid).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual([
        "第 1 回合，1 条建议", "第 2 回合，1 条建议", "第 3 回合，2 条建议", "第 4 回合，0 条建议"
      ]);
      expect(cell(3, 2).querySelector(".coaching-round-cell-number")).toHaveTextContent("3");
      expect(cell(3, 2).querySelector(".coaching-round-cell-count")).toHaveTextContent("2");
      expect(cell(4, 0).querySelector(".coaching-round-cell-count")).toBeEmptyDOMElement();
      expect(cell(4, 0)).toBeDisabled();
      expect(cell(4, 0)).toHaveAttribute("aria-expanded", "false");
      expect(cell(1, 1)).toBeEnabled();
    });

    it("opens one round at a time under the grid, and closes it on a second click, without seeking", async () => {
      const user = userEvent.setup();
      const panel = renderPanel(events, { rounds: fourRounds });

      await user.click(screen.getByRole("button", { name: /^全部回合/ }));
      // The round being watched opens first.
      expect(cell(1, 1)).toHaveAttribute("aria-expanded", "true");
      expect(cell(1, 1)).toHaveClass("expanded");
      expect(cell(1, 1)).toHaveAttribute("aria-controls", "coaching-round-1-events");
      expect(listedIds()).toEqual(["round-1"]);

      await user.click(cell(3, 2));
      expect(cell(1, 1)).toHaveAttribute("aria-expanded", "false");
      expect(cell(1, 1)).not.toHaveAttribute("aria-controls");
      expect(cell(3, 2)).toHaveAttribute("aria-expanded", "true");
      const opened = screen.getByRole("region", { name: "第 3 回合建议" });
      expect(within(opened).getByText("第 3 回合")).toBeInTheDocument();
      expect(listedIds()).toEqual(["round-3", "round-3-late"]);

      await user.click(cell(3, 2));
      expect(cell(3, 2)).toHaveAttribute("aria-expanded", "false");
      expect(listedIds()).toEqual([]);
      expect(screen.queryByRole("region", { name: /回合建议$/ })).not.toBeInTheDocument();

      // Opening a round is the panel's own business: the shared round and tick stay put.
      expect(panel.onSeek).not.toHaveBeenCalled();
    });

    it("opens the round the page moves to (查看这一刻 on another round's card, the timeline, the round strip)", async () => {
      const user = userEvent.setup();
      const panel = renderPanel(events, { rounds: fourRounds });

      await user.click(screen.getByRole("button", { name: /^全部回合/ }));
      await user.click(cell(3, 2));
      panel.rerender({ selectedRound: 2 });
      expect(cell(2, 1)).toHaveAttribute("aria-expanded", "true");
      expect(cell(3, 2)).toHaveAttribute("aria-expanded", "false");
      expect(listedIds()).toEqual(["round-2"]);

      // A round with nothing to list opens nothing.
      panel.rerender({ selectedRound: 4 });
      expect(screen.getByRole("group", { name: "按回合查看建议" }).querySelector(".expanded")).toBeNull();
      expect(listedIds()).toEqual([]);
    });
  });

  it("marks the cards at the playhead from activeEventIds without needing the tick", () => {
    renderPanel([spacingEarly, untradedLate], { currentTick: undefined, activeEventIds: new Set([untradedLate.id]) });
    expect(cardFor(untradedLate)).toHaveClass("active");
    expect(cardFor(spacingEarly)).not.toHaveClass("active");
  });

  it("does not re-render the cards on a playback frame, and still calls the latest handlers", async () => {
    const user = userEvent.setup();
    const panel = renderPanel([spacingEarly, untradedLate, entryMiddle]);
    const renders = () => vi.mocked(coachingCopy).mock.calls.length;
    const before = renders();

    // A new frame and fresh page closures, but no card enters or leaves the playhead.
    const latestFeedback = vi.fn();
    panel.rerender({ currentTick: 110, onSeek: vi.fn(), onFeedback: latestFeedback });
    expect(renders()).toBe(before);

    await user.click(within(cardFor(entryMiddle)).getByRole("button", { name: "有帮助" }));
    expect(latestFeedback).toHaveBeenCalledWith(entryMiddle, "helpful");
    expect(panel.onFeedback).not.toHaveBeenCalled();

    // Moving the playhead onto another card re-renders just the two cards that changed.
    const beforeMove = renders();
    panel.rerender({ currentTick: 600, onFeedback: latestFeedback });
    expect(renders() - beforeMove).toBe(2);
    expect(cardFor(untradedLate)).toHaveClass("active");
  });

  it("says on the card when a verdict was not saved, and re-sends it from there", async () => {
    const user = userEvent.setup();
    const onFeedback = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    renderPanel([untradedLate], { onFeedback });
    const card = cardFor(untradedLate);

    await user.click(within(card).getByRole("button", { name: "有帮助" }));
    expect(await within(card).findByText("评价没有保存。")).toBeInTheDocument();
    expect(within(card).getByRole("status")).toHaveTextContent("评价没有保存。");

    await user.click(within(card).getByRole("button", { name: "重新保存" }));
    expect(onFeedback).toHaveBeenLastCalledWith(untradedLate, "helpful");
    await waitFor(() => expect(within(card).getByRole("status")).toBeEmptyDOMElement());
  });

  it("treats a rejected save as not saved", async () => {
    const user = userEvent.setup();
    renderPanel([untradedLate], { onFeedback: vi.fn().mockRejectedValue(new Error("offline")) });

    await user.click(within(cardFor(untradedLate)).getByRole("button", { name: "无关" }));
    expect(await screen.findByText("评价没有保存。")).toBeInTheDocument();
  });

  it("lets only the latest click on a card decide what it says", async () => {
    const user = userEvent.setup();
    let finishFirst: (saved: boolean) => void = () => undefined;
    const onFeedback = vi.fn()
      .mockReturnValueOnce(new Promise<boolean>((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce(true);
    renderPanel([untradedLate], { onFeedback });
    const card = cardFor(untradedLate);

    await user.click(within(card).getByRole("button", { name: "有帮助" }));
    await user.click(within(card).getByRole("button", { name: "无关" }));
    await act(async () => finishFirst(false));

    expect(within(card).getByRole("status")).toBeEmptyDOMElement();
    expect(within(card).getByRole("group", { name: /这条建议是否有帮助/ })).not.toHaveAttribute("aria-busy");
  });

  it("asks for a reload with no re-send when the save says the suggestion is gone", async () => {
    const user = userEvent.setup();
    const onFeedback = vi.fn().mockResolvedValueOnce("stale").mockRejectedValueOnce({ status: 404 });
    renderPanel([untradedLate, entryMiddle], { onFeedback });

    await user.click(within(cardFor(untradedLate)).getByRole("button", { name: "有帮助" }));
    expect(await within(cardFor(untradedLate)).findByText("建议已按新规则更新，请刷新页面")).toBeInTheDocument();
    expect(within(cardFor(untradedLate)).queryByRole("button", { name: "重新保存" })).not.toBeInTheDocument();

    // A rejected 404 means the same.
    await user.click(within(cardFor(entryMiddle)).getByRole("button", { name: "无关" }));
    expect(await within(cardFor(entryMiddle)).findByText("建议已按新规则更新，请刷新页面")).toBeInTheDocument();
  });

  it("shows no save note for a handler that returns nothing", async () => {
    const user = userEvent.setup();
    renderPanel([untradedLate]);

    await user.click(within(cardFor(untradedLate)).getByRole("button", { name: "有帮助" }));
    expect(within(cardFor(untradedLate)).getByRole("status")).toBeEmptyDOMElement();
  });
});

describe("CoachingPanel 本场最值得回看", () => {
  // Seven suggestions for the reviewed player; the importance order is e, b, c, d, a, then f and g.
  const death = (id: string, round: number, tick: number, impact: Record<string, unknown>) =>
    deathCoachingEvent({ id, round_number: round, tick_start: tick, tick_end: tick }, { impact, extraReasons: [] });
  const a = death("a", 1, 200, { roundLost: false, firstDeath: false, manDisadvantage: false });
  const b = death("b", 2, 1200, { roundLost: true });
  const c = death("c", 3, 3200, { roundLost: false, firstDeath: true });
  const d = death("d", 3, 3400, { roundLost: false, manDisadvantage: true });
  const e = death("e", 4, 4200, { roundLost: true, firstDeath: true });
  const f = coachingEvent({ id: "f", tick_start: 300, tick_end: 300, severity: "low",
    structured_context_json: { ruleId: "poor_spacing", spacingType: "stacked", minPairDistance: 80 } });
  const g = coachingEvent({ id: "g", round_number: 2, tick_start: 1300, tick_end: 1300, severity: "medium",
    structured_context_json: { ruleId: "retake_desync", nearbyCount: 2, windowSeconds: 3 } });
  const seven = [a, b, c, d, e, f, g];

  const block = () => screen.queryByRole("region", { name: "本场最值得回看" });
  const ids = (container: HTMLElement | Document = document) =>
    Array.from(container.querySelectorAll("article"), (card) => card.id.replace("coaching-event-", ""));

  async function showAllRounds(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /^全部回合/ }));
  }

  it("leads 全部回合 with the five most important suggestions and lists only the rest by round", async () => {
    const user = userEvent.setup();
    renderPanel(seven);
    expect(block()).not.toBeInTheDocument();

    await showAllRounds(user);
    const top = block() as HTMLElement;
    expect(within(top).getByText("本场最值得回看")).toBeInTheDocument();
    // No caption under the heading: the order explains itself (S14 subtraction).
    expect(within(top).queryByText("按回合输赢、首个阵亡、人数劣势排序")).not.toBeInTheDocument();
    expect(ids(top)).toEqual(["e", "b", "c", "d", "a"]);
    // Cards out of their round group name the round above the clock.
    expect(top.querySelector(".coaching-feed-round")).toHaveTextContent("第 4 回合");

    // Rounds 3 and 4 had only top suggestions: their cells cannot open. Rounds 1 and 2 keep the rest,
    // one card each; the top five are not counted in the grid (no caption says so any more).
    expect(screen.getByRole("button", { name: "第 3 回合，0 条建议" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "第 4 回合，0 条建议" })).toBeDisabled();
    expect(screen.queryByText("不含上面的 5 条")).not.toBeInTheDocument();
    expect(ids(screen.getByRole("region", { name: "第 1 回合建议" }))).toEqual(["f"]);
    // Every suggestion once: the page finds cards by id for P/N and 返回建议.
    expect(ids().sort()).toEqual(["a", "b", "c", "d", "e", "f"]);
    await user.click(screen.getByRole("button", { name: "第 2 回合，1 条建议" }));
    expect(screen.queryByRole("region", { name: "第 1 回合建议" })).not.toBeInTheDocument();
    expect(ids(screen.getByRole("region", { name: "第 2 回合建议" }))).toEqual(["g"]);
    expect(ids().sort()).toEqual(["a", "b", "c", "d", "e", "g"]);
    // The totals elsewhere still count every suggestion.
    expect(screen.getByRole("button", { name: /^全部回合/ })).toHaveTextContent("全部回合 7 条");
    expect(screen.getByText("已评价 0/7")).toBeInTheDocument();
  });

  it("seeks, rates and highlights from a card in the block like from any other card", async () => {
    const user = userEvent.setup();
    const panel = renderPanel(seven, { currentTick: undefined, activeEventIds: new Set(["b"]) });
    await showAllRounds(user);
    const top = block() as HTMLElement;

    // The first card listed carries the view's one solid amber 查看这一刻.
    expect(within(top).getAllByRole("button", { name: /^查看这一刻/ }).map((button) => button.classList.contains("primary-button"))).toEqual([true, false, false, false, false]);
    await user.click(within(top).getAllByRole("button", { name: /^查看这一刻/ })[0]);
    expect(panel.onSeek).toHaveBeenCalledWith(4200, "e", "card");
    expect(cardFor(b)).toHaveClass("active");
    expect(top).toContainElement(cardFor(b));

    await user.click(within(cardFor(c)).getByRole("button", { name: "判断不足" }));
    expect(panel.onFeedback).toHaveBeenCalledWith(c, "unsure");
  });

  it("stays out of 当前回合, of a player with five suggestions or fewer, and of any filter or search", async () => {
    const user = userEvent.setup();
    const five = renderPanel(seven.slice(0, 5));
    await showAllRounds(user);
    expect(block()).not.toBeInTheDocument();
    five.rerender({ events: seven });
    expect(block()).toBeInTheDocument();

    await user.click(screen.getByText("筛选建议"));
    await user.type(screen.getByRole("textbox", { name: "搜索建议" }), "CT Anchor");
    expect(block()).not.toBeInTheDocument();
    expect(ids().length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole("button", { name: "清除筛选" })[0]);
    expect(block()).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "建议类型" }), "poor_spacing");
    expect(block()).not.toBeInTheDocument();
    expect(ids()).toEqual(["f"]);
    await user.click(screen.getAllByRole("button", { name: "清除筛选" })[0]);

    await user.click(screen.getByRole("button", { name: /^当前回合/ }));
    expect(block()).not.toBeInTheDocument();
    expect(ids().sort()).toEqual(["a", "f"]);
  });
});
