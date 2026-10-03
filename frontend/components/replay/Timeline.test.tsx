import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Timeline } from "@/components/replay/Timeline";
import { T_ENTRY_ID, coachingEvent, replayRounds } from "@/lib/test-fixtures/review";
import type { ReplayEvent } from "@/types/replay";

function kill(id: string, tick: number, attackerId: string, victimId: string, extra: Record<string, unknown> = {}): ReplayEvent {
  return {
    id, type: "kill", tick, roundNumber: 1, source: "parser", playerIds: [attackerId, victimId],
    playerId: attackerId, playerName: attackerId === T_ENTRY_ID ? "T Entry" : "CT Anchor",
    label: "killed", metadata: {
      attackerId, victimId,
      attackerName: attackerId === T_ENTRY_ID ? "T Entry" : "CT Anchor",
      victimName: victimId === T_ENTRY_ID ? "T Entry" : "CT Anchor",
      ...extra
    }
  };
}

const damage: ReplayEvent = {
  id: "damage-1", type: "damage", tick: 300, roundNumber: 1, source: "parser", playerIds: [T_ENTRY_ID],
  playerId: T_ENTRY_ID, playerName: "T Entry", label: "damage", metadata: {}
};

function renderTimeline(overrides: Partial<Parameters<typeof Timeline>[0]> = {}) {
  const props = {
    currentTick: 164,
    selectedRound: 1,
    rounds: replayRounds(),
    tickRate: 64,
    events: [coachingEvent({ id: "c1", tick_start: 420, tick_end: 420 })],
    parserEvents: [
      kill("k1", 228, T_ENTRY_ID, "enemy", { weapon: "ak47", headshot: true }),
      damage,
      kill("k2", 612, "enemy", T_ENTRY_ID, { weapon: "awp" })
    ],
    selectedPlayerId: T_ENTRY_ID,
    onSeek: vi.fn(),
    onSeekFinding: vi.fn(),
    ...overrides
  };
  render(<Timeline {...props} />);
  return props;
}

describe("Timeline", () => {
  it("tells the reviewed player's kills from their deaths and keeps damage off the lane", () => {
    renderTimeline();
    const lane = screen.getByRole("group", { name: "比赛事件" });
    const markers = within(lane).getAllByRole("button");
    expect(markers.map((marker) => marker.textContent)).toEqual(["杀", "亡"]);
    expect(markers[0]).toHaveAttribute("title", "T Entry 用 AK-47 击杀 CT Anchor（爆头） · 0:02");
    expect(markers[1]).toHaveAccessibleName("阵亡：CT Anchor 用 AWP 击杀 T Entry · 0:08");
    expect(markers[0]).toHaveClass("own-kill");
    expect(markers[1]).toHaveClass("own-death");
  });

  it("labels suggestion markers in Chinese and lands them like 查看这一刻", async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    const onSeekFinding = vi.fn();
    renderTimeline({ onSeek, onSeekFinding });
    const marker = within(screen.getByRole("group", { name: "建议" })).getByRole("button");
    expect(marker).toHaveAccessibleName("建议：检查首次交火的支援距离 · 0:05");
    expect(marker).toHaveAttribute("title", "建议：检查首次交火的支援距离 · 0:05");
    await user.click(marker);
    expect(onSeekFinding).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }));
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("gives each marker lane one Tab stop and moves between markers with the arrow keys", async () => {
    const user = userEvent.setup();
    renderTimeline({ currentTick: 700 });
    const markers = within(screen.getByRole("group", { name: "比赛事件" })).getAllByRole("button");
    // The stop is the marker at or before the playhead.
    expect(markers.map((marker) => marker.tabIndex)).toEqual([-1, 0]);

    markers[1].focus();
    await user.keyboard("{ArrowLeft}");
    expect(markers[0]).toHaveFocus();
    expect(markers.map((marker) => marker.tabIndex)).toEqual([0, -1]);
    await user.keyboard("{End}");
    expect(markers[1]).toHaveFocus();
  });

  it("steps the slider by five seconds, one with Shift and ten with Page keys, within the round", () => {
    const onSeek = vi.fn();
    renderTimeline({ currentTick: 400, onSeek });
    const slider = screen.getByRole("slider", { name: "拖动定位回放" });
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    fireEvent.keyDown(slider, { key: "ArrowLeft", shiftKey: true });
    fireEvent.keyDown(slider, { key: "PageUp" });
    fireEvent.keyDown(slider, { key: "End" });
    fireEvent.keyDown(slider, { key: "Home" });
    // 400 + 5 s; 400 - 1 s; +10 s clamps to the round end (900).
    expect(onSeek.mock.calls.map(([tick]) => tick)).toEqual([720, 336, 900, 900, 100]);
  });

  it("shades freeze time on the round lane", () => {
    renderTimeline();
    const stack = document.querySelector<HTMLElement>(".timeline-lane-stack")!;
    // Round 1 runs 100–900 and freeze time ends at 164: 8 % of the round.
    expect(stack.style.getPropertyValue("--timeline-freeze-end")).toBe("8%");
  });

  it("puts the moving playhead only on the spine and the slider, not on the lanes", () => {
    const props = { selectedRound: 1, rounds: replayRounds(), tickRate: 64, events: [], onSeek: vi.fn() };
    const { rerender } = render(<Timeline currentTick={500} {...props} />);
    const stack = document.querySelector<HTMLElement>(".timeline-lane-stack")!;
    const spine = document.querySelector<HTMLElement>(".timeline-current-spine")!;
    const slider = screen.getByRole("slider", { name: "拖动定位回放" });
    // Round 1 runs 100–900: tick 500 is halfway.
    expect(spine.style.getPropertyValue("--timeline-current-tick")).toBe("50%");
    expect(slider.style.getPropertyValue("--timeline-current-tick")).toBe("50%");
    // On the lane stack every marker would restyle each playback frame.
    expect(stack.style.getPropertyValue("--timeline-current-tick")).toBe("");
    rerender(<Timeline currentTick={700} {...props} />);
    expect(spine.style.getPropertyValue("--timeline-current-tick")).toBe("75%");
  });

  it("only adds a class in the compact dock: the same lanes, markers and slider", () => {
    renderTimeline({ compact: true });
    const panel = screen.getByRole("region", { name: "回合时间轴" });
    expect(panel).toHaveClass("timeline-panel", "evidence-timeline", "compact");
    expect(within(screen.getByRole("group", { name: "比赛事件" })).getAllByRole("button")).toHaveLength(2);
    expect(within(screen.getByRole("group", { name: "建议" })).getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("slider", { name: "拖动定位回放" })).toBeInTheDocument();
  });

  it("keeps the full timeline without the compact class by default", () => {
    renderTimeline();
    expect(screen.getByRole("region", { name: "回合时间轴" })).not.toHaveClass("compact");
  });
});
