import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MatchAnalysis } from "@/components/stats/MatchAnalysis";
import { CT_ANCHOR_ID, T_ENTRY_ID, replayData } from "@/lib/test-fixtures/review";
import type { PlayerSide, ReplayData, ReplayEvent, ReplayFrame, ReplayPlayer } from "@/types/replay";

const CT_RIFLER_ID = "76561198000000003";

const PLAYERS: ReplayPlayer[] = [
  { id: T_ENTRY_ID, name: "T Entry", side: "CT", color: "#f5b542" },
  { id: CT_ANCHOR_ID, name: "CT Anchor", side: "T", color: "#2ed3d0" },
  { id: CT_RIFLER_ID, name: "CT Rifler", side: "T", color: "#2ed3d0" }
];

// T Entry starts T; round 3 is after the switch of sides, so T Entry plays CT there.
function frame(tick: number, roundNumber: number, entrySide: PlayerSide): ReplayFrame {
  const other: PlayerSide = entrySide === "T" ? "CT" : "T";
  return {
    tick, timeSeconds: tick / 64, roundNumber,
    players: PLAYERS.map((player, index) => ({
      id: player.id, name: player.name, side: index === 0 ? entrySide : other,
      x: 20 + index * 10, y: 30, z: 0, alive: true, hp: 100, hasBomb: false
    })),
    bombState: { status: "carried" }
  };
}

function kill(tick: number, roundNumber: number, attackerId: string, victimId: string, position: { x: number; y: number; z?: number },
  extra: Record<string, unknown> = {}): ReplayEvent {
  const name = (id: string) => PLAYERS.find((player) => player.id === id)?.name;
  return {
    id: `kill-${tick}`, type: "kill", tick, roundNumber, source: "parser", playerIds: [attackerId, victimId],
    playerId: attackerId, label: "kill", ...position,
    metadata: { attackerId, attackerName: name(attackerId), victimId, victimName: name(victimId), weapon: "ak47", headshot: false, ...extra }
  };
}

function utility(type: ReplayEvent["type"], tick: number, roundNumber: number, playerId: string): ReplayEvent {
  return { id: `${type}-${tick}`, type, tick, roundNumber, source: "parser", playerIds: [playerId], playerId, label: type };
}

function matchReplay(overrides: Partial<ReplayData> = {}): ReplayData {
  return replayData({
    mapName: "de_mirage",
    players: PLAYERS,
    rounds: [
      { roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 900, winnerSide: "CT" },
      { roundNumber: 2, startTick: 1000, freezeEndTick: 1064, endTick: 1800, winnerSide: "T" },
      { roundNumber: 3, startTick: 2000, freezeEndTick: 2064, endTick: 2800, winnerSide: "T" }
    ],
    frames: [frame(100, 1, "T"), frame(500, 1, "T"), frame(1000, 2, "T"), frame(1400, 2, "T"), frame(2000, 3, "CT"), frame(2400, 3, "CT")],
    events: [
      kill(600, 1, CT_ANCHOR_ID, T_ENTRY_ID, { x: 40, y: 50, z: -100 }, { headshot: true }),
      kill(1300, 2, T_ENTRY_ID, CT_ANCHOR_ID, { x: 70, y: 20 }),
      kill(1500, 2, T_ENTRY_ID, CT_RIFLER_ID, { x: 72, y: 22 }),
      kill(2400, 3, CT_ANCHOR_ID, T_ENTRY_ID, { x: 42, y: 51, z: -90 }),
      utility("smoke", 300, 1, T_ENTRY_ID),
      utility("flash", 1100, 2, T_ENTRY_ID),
      utility("flash", 1150, 2, T_ENTRY_ID),
      utility("he", 2100, 3, T_ENTRY_ID),
      utility("molotov", 2200, 3, CT_ANCHOR_ID)
    ],
    ...overrides
  });
}

function renderAnalysis(overrides: Partial<Parameters<typeof MatchAnalysis>[0]> = {}) {
  const props = {
    replay: matchReplay(),
    player: PLAYERS[0],
    onSeekTick: vi.fn(),
    onSelectRound: vi.fn(),
    onChoosePlayer: vi.fn(),
    ...overrides
  };
  render(<MatchAnalysis {...props} />);
  return props;
}

describe("MatchAnalysis", () => {
  it("asks for a player first and shows no charts until one is chosen", async () => {
    const user = userEvent.setup();
    const props = renderAnalysis({ player: null });
    const section = screen.getByRole("region", { name: "数据" });
    expect(section).toHaveTextContent("选择要复盘的玩家后");
    expect(within(section).queryByRole("region", { name: "阵亡位置" })).not.toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "选择玩家" }));
    expect(props.onChoosePlayer).toHaveBeenCalledTimes(1);
  });

  it("says so when the replay has no events to count", () => {
    renderAnalysis({ replay: matchReplay({ events: [] }) });
    expect(screen.getByRole("region", { name: "数据：T Entry" })).toHaveTextContent("没有击杀和道具记录");
    expect(screen.queryByRole("region", { name: "每回合击杀" })).not.toBeInTheDocument();
  });

  it("puts one button per death on the radar and seeks to 3 s before it", async () => {
    const user = userEvent.setup();
    const props = renderAnalysis();
    const map = screen.getByRole("region", { name: "阵亡位置" });
    const dots = within(map).getAllByRole("button");
    expect(dots.map((dot) => dot.getAttribute("aria-label"))).toEqual([
      "第 1 回合 0:06 被 CT Anchor 用 AK-47 击杀（爆头）",
      "第 3 回合 0:05 被 CT Anchor 用 AK-47 击杀"
    ]);
    expect(map.querySelector("image")).toHaveAttribute("href", "/maps/de_mirage_radar.png");
    // Framed on the deaths: never tighter than half the radar, dots placed within that frame.
    expect(map.querySelector("svg")).toHaveAttribute("viewBox", "16 25.5 50 50");
    // At 48 % and 52 % across (15 px apart) the two dots would overlap, so they fan out above and below their midpoint.
    expect(dots.map((dot) => [dot.style.left, dot.style.top])).toEqual([["50%", "47%"], ["50%", "53%"]]);
    await user.click(dots[1]);
    // 3 s at 64 ticks before tick 2400, still after round 3's freeze time.
    expect(props.onSeekTick).toHaveBeenCalledWith(2400 - 192);
    expect(within(map).getByText("阵亡").nextElementSibling).toHaveTextContent("2 次");
    expect(within(map).getByText("被爆头").nextElementSibling).toHaveTextContent("1 次");
    expect(within(map).getByText("被谁击杀最多").nextElementSibling).toHaveTextContent("CT Anchor（2 次）");
    expect(map).toHaveTextContent("最集中的一片：2 次，第 1、3 回合");
  });

  it("never seeks into freeze time for an early death", async () => {
    const user = userEvent.setup();
    const early = kill(180, 1, CT_ANCHOR_ID, T_ENTRY_ID, { x: 10, y: 10 });
    const props = renderAnalysis({ replay: matchReplay({ events: [early] }) });
    await user.click(within(screen.getByRole("region", { name: "阵亡位置" })).getByRole("button"));
    expect(props.onSeekTick).toHaveBeenCalledWith(164);
  });

  it("says 自杀 for the player's own kill and leaves out team-switch deaths between rounds", () => {
    const replay = matchReplay({
      events: [
        // Between rounds 2 and 3 (a half-time switch), yet filed under round 3.
        kill(1900, 3, T_ENTRY_ID, T_ENTRY_ID, { x: 10, y: 10 }),
        kill(2300, 3, T_ENTRY_ID, T_ENTRY_ID, { x: 30, y: 30 }, { weapon: "hegrenade" })
      ]
    });
    renderAnalysis({ replay });
    const map = screen.getByRole("region", { name: "阵亡位置" });
    expect(within(map).getAllByRole("button").map((dot) => dot.getAttribute("aria-label")))
      .toEqual(["第 3 回合 0:03 自杀（手雷）"]);
    expect(within(map).queryByText("被谁击杀最多")).not.toBeInTheDocument();
  });

  it("draws kills per round in the side played, with deaths marked, and jumps to a round", async () => {
    const user = userEvent.setup();
    const props = renderAnalysis();
    const chart = screen.getByRole("region", { name: "每回合击杀" });
    const bars = within(chart).getAllByRole("button");
    expect(bars.map((bar) => bar.getAttribute("aria-label"))).toEqual([
      "第 1 回合，T 方，0 杀，阵亡",
      "第 2 回合，T 方，2 杀",
      "第 3 回合，CT 方，0 杀，阵亡"
    ]);
    expect(chart.querySelectorAll(".kills-chart-fill.side-t")).toHaveLength(1);
    expect(chart.querySelectorAll(".kills-chart-death")).toHaveLength(2);
    expect(chart).toHaveTextContent("共 2 杀，1 个回合有击杀，阵亡 2 次");
    await user.click(bars[2]);
    expect(props.onSelectRound).toHaveBeenLastCalledWith(3);

    // One tab stop; arrows move between rounds and Enter jumps.
    bars[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(bars[1]).toHaveFocus();
    expect(bars[1]).toHaveAttribute("tabindex", "0");
    await user.keyboard("{Enter}");
    expect(props.onSelectRound).toHaveBeenLastCalledWith(2);

    const table = within(chart).getByRole("table", { name: "每回合击杀" });
    const row = within(table).getByRole("row", { name: /第 2 回合/ });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["T", "2", "否"]);
  });

  it("lists the opening duels and jumps to 3 s before each", async () => {
    const user = userEvent.setup();
    const props = renderAnalysis();
    const duels = screen.getByRole("region", { name: "开局对枪" });
    expect(duels).toHaveTextContent("首杀 1 次");
    expect(duels).toHaveTextContent("首死 2 次");
    const list = within(duels).getByRole("list", { name: "开局对枪的回合" });
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "第 1 回合负CT Anchor0:06", "第 2 回合胜CT Anchor0:03", "第 3 回合负CT Anchor0:05"
    ]);
    await user.click(within(list).getByRole("button", { name: "第 2 回合 0:03 开局对枪胜，对手 CT Anchor" }));
    expect(props.onSeekTick).toHaveBeenCalledWith(1300 - 192);
  });

  it("counts the player's own utility with a per-round average", () => {
    renderAnalysis();
    const table = within(screen.getByRole("region", { name: "道具" })).getByRole("table");
    const values = (label: string) => within(within(table).getByRole("row", { name: new RegExp(`^${label}`) }))
      .getAllByRole("cell").slice(0, 2).map((cell) => cell.textContent);
    expect(values("烟雾弹")).toEqual(["1", "0.33"]);
    expect(values("闪光弹")).toEqual(["2", "0.67"]);
    expect(values("燃烧弹")).toEqual(["0", "0.00"]);
    expect(values("手雷")).toEqual(["1", "0.33"]);
    expect(values("合计")).toEqual(["4", "1.33"]);
  });

  it("splits Nuke deaths by floor and switches floors", async () => {
    const user = userEvent.setup();
    const replay = matchReplay({
      mapName: "de_nuke",
      events: [
        kill(600, 1, CT_ANCHOR_ID, T_ENTRY_ID, { x: 40, y: 50, z: -700 }),
        kill(1600, 2, CT_ANCHOR_ID, T_ENTRY_ID, { x: 60, y: 30, z: -600 }),
        kill(2400, 3, CT_ANCHOR_ID, T_ENTRY_ID, { x: 42, y: 51, z: 0 })
      ]
    });
    renderAnalysis({ replay });
    const map = screen.getByRole("region", { name: "阵亡位置" });
    const floors = within(map).getByRole("group", { name: "地图楼层" });
    // Most deaths were on the lower floor, so it opens there.
    expect(within(floors).getByRole("button", { name: "下层 2" })).toHaveAttribute("aria-pressed", "true");
    expect(map.querySelector("image")).toHaveAttribute("href", "/maps/de_nuke_lower_radar.png");
    expect(within(map).getAllByRole("button", { name: /击杀/ })).toHaveLength(2);
    await user.click(within(floors).getByRole("button", { name: "上层 1" }));
    expect(map.querySelector("image")).toHaveAttribute("href", "/maps/de_nuke_radar.png");
    expect(within(map).getAllByRole("button", { name: /击杀/ }).map((dot) => dot.getAttribute("aria-label")))
      .toEqual(["第 3 回合 0:05 被 CT Anchor 用 AK-47 击杀"]);
  });


  it("times moments from the end of freeze time, so a half-time break does not inflate them", () => {
    const replay = matchReplay({
      // Round 3 opens after a long break: 1,280 ticks (20 s) of freeze time.
      rounds: [
        { roundNumber: 1, startTick: 100, freezeEndTick: 164, endTick: 900, winnerSide: "CT" },
        { roundNumber: 2, startTick: 1000, freezeEndTick: 1064, endTick: 1800, winnerSide: "T" },
        { roundNumber: 3, startTick: 2000, freezeEndTick: 3280, endTick: 4800, winnerSide: "T" }
      ],
      events: [kill(3600, 3, CT_ANCHOR_ID, T_ENTRY_ID, { x: 42, y: 51 })]
    });
    renderAnalysis({ replay });
    expect(within(screen.getByRole("region", { name: "阵亡位置" })).getByRole("button"))
      .toHaveAccessibleName("第 3 回合 0:05 被 CT Anchor 用 AK-47 击杀");
    expect(within(screen.getByRole("list", { name: "开局对枪的回合" })).getByRole("listitem")).toHaveTextContent("0:05");
  });

  it("counts a death after the round ended for that round and seeks no later than its end", async () => {
    const user = userEvent.setup();
    // Round 2 ends at 1800; the parser files this exit kill under round 3.
    const props = renderAnalysis({ replay: matchReplay({ events: [kill(1995, 3, CT_ANCHOR_ID, T_ENTRY_ID, { x: 42, y: 51 })] }) });
    const dot = within(screen.getByRole("region", { name: "阵亡位置" })).getByRole("button");
    expect(dot.getAttribute("aria-label")).toMatch(/^第 2 回合 /);
    await user.click(dot);
    expect(props.onSeekTick).toHaveBeenCalledWith(1800);
    const bars = within(screen.getByRole("region", { name: "每回合击杀" })).getAllByRole("button");
    expect(bars.map((bar) => bar.getAttribute("aria-label"))).toEqual([
      "第 1 回合，T 方，0 杀", "第 2 回合，T 方，0 杀，阵亡", "第 3 回合，CT 方，0 杀"
    ]);
  });

  it("fans out deaths on the same spot so each dot stays clickable", async () => {
    const user = userEvent.setup();
    const props = renderAnalysis({
      replay: matchReplay({
        events: [
          kill(600, 1, CT_ANCHOR_ID, T_ENTRY_ID, { x: 40, y: 50 }),
          kill(2400, 3, CT_ANCHOR_ID, T_ENTRY_ID, { x: 40.2, y: 50.1 })
        ]
      })
    });
    const dots = within(screen.getByRole("region", { name: "阵亡位置" })).getAllByRole("button");
    expect(dots).toHaveLength(2);
    const position = (dot: HTMLElement) => [parseFloat(dot.style.left), parseFloat(dot.style.top)];
    const [first, second] = dots.map(position);
    // 20 px buttons on a canvas of about 340 px: at least 6 % apart.
    expect(Math.hypot(first[0] - second[0], first[1] - second[1])).toBeGreaterThanOrEqual(5.99);
    await user.click(dots[1]);
    expect(props.onSeekTick).toHaveBeenLastCalledWith(2400 - 192);
  });
});
