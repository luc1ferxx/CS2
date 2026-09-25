import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { coachingCopy } from "@/lib/coaching-copy";
import { coachingEvent, replayPlayers, replayRounds } from "@/lib/test-fixtures/review";
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

  it("keeps rounds the player opened in 全部回合 when a card from another round is watched", async () => {
    const user = userEvent.setup();
    const inRound = (round: number) => coachingEvent({ id: `round-${round}`, round_number: round, tick_start: round * 1000 + 200,
      tick_end: round * 1000 + 200, structured_context_json: { ruleId: "poor_spacing" } });
    const panel = renderPanel([inRound(1), inRound(2), inRound(3)]);

    await user.click(screen.getByRole("button", { name: /^全部回合/ }));
    await user.click(screen.getByRole("button", { name: /第 3 回合/ }));
    const header = (round: number) => screen.getByRole("button", { name: new RegExp(`第 ${round} 回合`) });
    expect(header(1)).toHaveAttribute("aria-expanded", "true");
    expect(header(3)).toHaveAttribute("aria-expanded", "true");

    // 查看这一刻 on the round 2 card moves the shared round to 2.
    panel.rerender({ selectedRound: 2 });
    expect(header(1)).toHaveAttribute("aria-expanded", "true");
    expect(header(2)).toHaveAttribute("aria-expanded", "true");
    expect(header(3)).toHaveAttribute("aria-expanded", "true");
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

  it("shows no save note for a handler that returns nothing", async () => {
    const user = userEvent.setup();
    renderPanel([untradedLate]);

    await user.click(within(cardFor(untradedLate)).getByRole("button", { name: "有帮助" }));
    expect(within(cardFor(untradedLate)).getByRole("status")).toBeEmptyDOMElement();
  });
});
