import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoundReviewPanel, RoundStrip } from "@/components/replay/RoundReviewPanel";
import { T_ENTRY_ID, coachingEvent, replayData } from "@/lib/test-fixtures/review";
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
    const legend = document.querySelector(".round-strip-head .round-strip-legend");
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
});
