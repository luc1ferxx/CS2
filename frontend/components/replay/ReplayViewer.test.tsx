import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReplayViewer, type ReplayMapOverlayContext } from "@/components/replay/ReplayViewer";
import { V2_ALPHA, V2_CHARLIE, V2_DELTA, replayV1, replayV2 } from "@/lib/test-fixtures/replay-v2";
import type { ReplayData } from "@/types/replay";

const TEAMS = [{ key: "A", name: "Spirit" }, { key: "B", name: "MOUZ" }];

function renderViewer(props: Partial<Parameters<typeof ReplayViewer>[0]> & { replay?: ReplayData } = {}) {
  return render(
    <ReplayViewer replay={replayV2()} currentTick={200} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} {...props} />
  );
}

function row(name: string): HTMLElement {
  const button = within(screen.getByRole("group", { name: "玩家名单" })).getByRole("button", { name: new RegExp(`^${name}`) });
  return button.closest(".roster-row") as HTMLElement;
}

function panel(side: "T" | "CT"): HTMLElement {
  return document.querySelector(`.side-roster.side-${side.toLowerCase()}`) as HTMLElement;
}

function hud(side: "T" | "CT"): HTMLElement {
  return document.querySelector(`.map-hud-team.side-${side.toLowerCase()}`) as HTMLElement;
}

function dotOf(side: "T" | "CT", index: number): SVGGElement {
  return [...document.querySelectorAll<SVGGElement>(`.map-player-dot.side-${side.toLowerCase()}`)]
    .find((dot) => dot.querySelector(".map-player-index")?.textContent === String(index))!;
}

function traces() {
  return [...document.querySelectorAll(".map-kill-trace")].map((line) => ({
    side: line.classList.contains("side-t") ? "T" : line.classList.contains("side-ct") ? "CT" : "?",
    x1: line.getAttribute("x1"), y1: line.getAttribute("y1"), x2: line.getAttribute("x2"), y2: line.getAttribute("y2"),
    opacity: line.getAttribute("opacity"),
    dashed: line.classList.contains("cross-floor")
  }));
}

function feedRows(): string[] {
  return [...document.querySelectorAll(".map-kill-feed .map-kill-row")].map((item) => item.textContent ?? "");
}

// Extra copies of round 1's first kill (Alpha kills Delta) at the given ticks, to count windows and caps.
function withKills(ticks: number[]): ReplayData {
  const base = replayV2();
  const extra = ticks.map((tick, index) => ({
    ...base.events[0],
    id: `extra-${index}`,
    tick,
    metadata: { ...base.events[0].metadata, headshot: false }
  }));
  return { ...base, events: [...base.events, ...extra] };
}

describe("ReplayViewer live roster", () => {
  it("shows each side's team, live equipment value and every player's kit (v2)", () => {
    renderViewer();
    expect(within(panel("T")).getByRole("heading", { level: 3 })).toHaveTextContent("Spirit T");
    expect(within(panel("CT")).getByRole("heading", { level: 3 })).toHaveTextContent("MOUZ CT");
    // The side's equipment value is in the map's status bar, not the roster header.
    expect(document.querySelector(".roster-equipment")).toBeNull();
    expect(hud("T").querySelector(".map-hud-equipment")).toHaveTextContent("装备 $2,100");
    expect(hud("CT").querySelector(".map-hud-equipment")).toHaveTextContent("装备 $1,650");

    const alpha = row("Alpha");
    expect(alpha).toHaveClass("reviewed");
    expect(within(alpha).getByText("复盘中")).toBeInTheDocument();
    expect(alpha.querySelector(".roster-bomb")).not.toBeNull();
    expect(alpha.querySelector(".roster-money")).toHaveTextContent("$50");
    expect(alpha.querySelector(".roster-kd")).toHaveTextContent("0/0");
    expect(alpha.querySelector(".roster-hp")).toHaveTextContent("100");
    expect(alpha.querySelector(".roster-weapon")).toHaveTextContent("Glock-18");
    expect([...alpha.querySelectorAll(".roster-grenades .roster-icon")].map((icon) => icon.getAttribute("class")))
      .toEqual([expect.stringContaining("grenade-flash"), expect.stringContaining("grenade-flash"), expect.stringContaining("grenade-smoke")]);
    // Armour without a helmet vs with one, and the kit only on CT.
    expect(alpha.querySelector(".roster-armor")).toHaveClass("lucide-shield-half");
    expect(row("Bravo").querySelector(".roster-armor")).toHaveClass("lucide-shield");
    expect(row("Bravo").querySelector(".roster-hp-bar > span")).toHaveStyle({ width: "100%" });
    expect(row("Charlie").querySelector(".roster-defuser")).not.toBeNull();
    expect(row("Charlie").querySelector(".roster-armor")).toBeNull();
    expect(row("Delta").querySelector(".roster-defuser")).toBeNull();
  });

  it("gives each row's name button the same facts as text", () => {
    renderViewer();
    const alpha = screen.getByRole("button", { name: /^Alpha/ });
    expect(alpha).toHaveAccessibleDescription(
      "Alpha，复盘中，存活，血量 100，护甲 100，手持 Glock-18，道具 闪光弹 ×2、烟雾弹，携带炸弹，金钱 $50，击杀 0 死亡 0"
    );
    expect(screen.getByRole("button", { name: "Charlie" })).toHaveAccessibleDescription(expect.stringContaining("有拆弹器"));
    // The icons and numbers are shorthand for that text.
    expect(row("Alpha").querySelector(".roster-line-detail")).toHaveAttribute("aria-hidden", "true");
  });

  it("updates during playback: low HP, K/D so far and who killed a dead player with what", () => {
    const { rerender } = renderViewer({ currentTick: 600 });
    const delta = row("Delta");
    expect(delta).toHaveClass("dead");
    expect(delta.querySelector(".roster-death")?.textContent).toBe("阵亡　被 Alpha 用 AK-47 击杀");
    expect(delta.querySelector(".roster-hp-bar")).toBeNull();
    expect(delta.querySelector(".roster-kd")).toHaveTextContent("0/1");
    expect(row("Alpha").querySelector(".roster-kd")).toHaveTextContent("1/0");
    expect(screen.getByRole("button", { name: "Delta" })).toHaveAccessibleDescription(expect.stringContaining("阵亡，被 Alpha 用 AK-47 击杀"));

    rerender(<ReplayViewer replay={replayV2()} currentTick={750} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    expect(row("Charlie").querySelector(".roster-hp-bar > span")).toHaveClass("low");
    expect(row("Charlie").querySelector(".roster-hp")).toHaveTextContent("23");
    expect(row("Bravo").querySelector(".roster-death")?.textContent).toBe("阵亡　被 Charlie 用 M4A1-S 击杀");
    expect(row("Charlie").querySelector(".roster-kd")).toHaveTextContent("1/0");

    // Next round: everyone is back, the counts keep going.
    rerender(<ReplayViewer replay={replayV2()} currentTick={1100} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    expect(document.querySelectorAll(".roster-row.dead")).toHaveLength(0);
    expect(row("Delta").querySelector(".roster-weapon")).toHaveTextContent("AWP");
    expect(row("Delta").querySelector(".roster-kd")).toHaveTextContent("0/1");
    expect(row("Alpha").querySelector(".roster-armor")).toHaveClass("lucide-shield");
    expect(hud("CT").querySelector(".map-hud-equipment")).toHaveTextContent("装备 $10,550");
  });

  it("shows a held grenade or knife by its short name", () => {
    const base = replayV2();
    const states = { ...base.playerStates, [V2_CHARLIE]: [{ tick: 100, weapon: "Butterfly Knife", equipValue: 200 }] };
    renderViewer({ replay: { ...base, playerStates: states }, currentTick: 310 });
    expect(row("Alpha").querySelector(".roster-weapon")).toHaveTextContent("烟雾弹");
    expect(row("Alpha").querySelector(".roster-weapon")).toHaveAttribute("title", "Smoke Grenade");
    expect(row("Charlie").querySelector(".roster-weapon")).toHaveTextContent("刀");
    expect(screen.getByRole("button", { name: /^Alpha/ })).toHaveAccessibleDescription(expect.stringContaining("手持 烟雾弹"));
  });

  it("leaves dead players out of the side's equipment value", () => {
    const base = replayV2();
    // Real demos keep a dead player's last equipment value (Delta dies at 500 carrying $850).
    const states = { ...base.playerStates, [V2_DELTA]: base.playerStates![V2_DELTA].map((entry) =>
      entry.tick === 500 ? { ...entry, equipValue: 850 } : entry) };
    renderViewer({ replay: { ...base, playerStates: states }, currentTick: 600 });
    expect(hud("CT").querySelector(".map-hud-equipment")).toHaveTextContent("装备 $800");
  });

  it("counts from matchReplay when the map gets a per-player subset of events", () => {
    const full = replayV2();
    const scoped = { ...full, events: full.events.filter((event) => event.playerIds.includes(V2_ALPHA)) };
    renderViewer({ replay: scoped, matchReplay: full, currentTick: 750 });
    expect(row("Charlie").querySelector(".roster-kd")).toHaveTextContent("1/0");
    expect(row("Bravo").querySelector(".roster-death")?.textContent).toBe("阵亡　被 Charlie 用 M4A1-S 击杀");
  });

  it("hides the parts a v1 replay has no data for", () => {
    renderViewer({ replay: replayV1(), teamNames: null, currentTick: 600 });
    expect(within(panel("T")).getByRole("heading", { level: 3 })).toHaveTextContent("进攻方 T");
    expect(document.querySelector(".map-hud-equipment")).toBeNull();
    expect(document.querySelector(".roster-money")).toBeNull();
    expect(document.querySelector(".roster-armor")).toBeNull();
    expect(document.querySelector(".roster-grenades")).toBeNull();
    expect(document.querySelector(".roster-defuser")).toBeNull();
    expect(document.querySelector(".roster-weapon")).toBeNull();
    // Frames and kill events are still there: HP, bomb, K/D and deaths. A living player is one line.
    expect(row("Bravo").querySelector(".roster-line-main .roster-hp")).toHaveTextContent("76");
    expect(row("Bravo").querySelector(".roster-line-detail")).toBeNull();
    expect(row("Delta").querySelector(".roster-line-detail")).not.toBeNull();
    expect(row("Alpha").querySelector(".roster-bomb")).not.toBeNull();
    expect(row("Alpha").querySelector(".roster-kd")).toHaveTextContent("1/0");
    expect(row("Delta").querySelector(".roster-death")?.textContent).toBe("阵亡　被 Alpha 用 AK-47 击杀");
    expect(screen.getByRole("button", { name: "Bravo" })).toHaveAccessibleDescription("Bravo，存活，血量 76，击杀 0 死亡 0");
  });

  it("keeps the roster's keyboard behaviour: one Tab stop, arrows move, a click highlights", async () => {
    const user = userEvent.setup();
    renderViewer();
    const roster = screen.getByRole("group", { name: "玩家名单" });
    const buttons = within(roster).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["Alpha 复盘中", "Bravo", "Charlie", "Delta"]);
    expect(buttons.filter((button) => button.tabIndex === 0)).toEqual([buttons[0]]);
    buttons[0].focus();
    await user.keyboard("{ArrowDown}");
    expect(buttons[1]).toHaveFocus();
    await user.keyboard("{End}");
    expect(buttons[3]).toHaveFocus();
    await user.click(buttons[2]);
    expect(buttons[2]).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".map-highlight-card")).toHaveTextContent("Charlie");
  });
});

describe("ReplayViewer extension points", () => {
  it("draws the overlay under the players and overlayAbove over everything, with the map context", () => {
    const overlay = vi.fn((context: ReplayMapOverlayContext) => <circle data-testid="under" r={context.roundNumber} />);
    renderViewer({ overlay, overlayAbove: <rect data-testid="above" width="10" height="10" />, currentTick: 1100 });
    const context = overlay.mock.calls.at(-1)![0];
    expect(context).toMatchObject({ hasFloors: false, floor: null, currentTick: 1100, roundNumber: 2, focusPlayerId: null });
    expect(context.map.displayName).toBe("Mirage");

    const svg = document.querySelector(".map-frame > svg")!;
    const children = [...svg.children];
    const under = children.indexOf(screen.getByTestId("under").parentElement!);
    const firstDot = children.indexOf(svg.querySelector(".map-player-dot")!);
    const above = children.indexOf(screen.getByTestId("above").parentElement!);
    expect(under).toBeGreaterThan(0);
    expect(under).toBeLessThan(firstDot);
    // Over every dot and marker; only the reviewed player's lock (a HUD mark) comes after it.
    expect(above).toBe(children.length - 2);
    expect(children.at(-1)).toHaveClass("map-lock-marker");
  });

  it("renders nothing extra without overlays", () => {
    renderViewer();
    expect(document.querySelector(".map-overlay-slot")).toBeNull();
  });

  it("tells a multi-floor overlay which floor is on screen", () => {
    const overlay = vi.fn(() => null);
    renderViewer({ replay: replayV2({ mapName: "de_nuke" }), overlay, levelMode: "lower" });
    expect(overlay.mock.calls.at(-1)).toEqual([expect.objectContaining({ hasFloors: true, floor: "lower" })]);
  });

  it("dims everyone but the focused player and names the focused one", () => {
    const { rerender } = renderViewer({ focusPlayerId: V2_CHARLIE, selectedPlayerId: null });
    const dots = [...document.querySelectorAll<SVGGElement>(".map-player-dot")];
    expect(dots.filter((dot) => dot.classList.contains("focus-dimmed"))).toHaveLength(3);
    expect(dots.filter((dot) => dot.classList.contains("focus-focused"))).toHaveLength(1);
    const focused = dots.find((dot) => dot.classList.contains("focus-focused"))!;
    expect(focused.querySelector(".map-player-name")).toHaveTextContent("Charlie");
    expect(focused).toHaveAttribute("opacity", "1");
    expect(dots.find((dot) => dot.classList.contains("focus-dimmed"))).toHaveAttribute("opacity", "0.25");
    // The roster is not dimmed.
    expect(document.querySelector(".roster-row.focus-dimmed")).toBeNull();

    rerender(<ReplayViewer replay={replayV2()} currentTick={200} selectedPlayerId={null} onSelectPlayer={vi.fn()}
      focusPlayerId={null} />);
    expect(document.querySelector(".focus-dimmed, .focus-focused")).toBeNull();
  });

  it("still switches the review only through the explicit button", () => {
    const onSelectPlayer = vi.fn();
    renderViewer({ onSelectPlayer });
    fireEvent.click(dotOf("CT", 2));
    expect(onSelectPlayer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "切换为他的视角" }));
    expect(onSelectPlayer).toHaveBeenCalledWith(V2_DELTA);
  });
});

describe("ReplayViewer map HUD", () => {
  it("shows each side's alive pips, the round clock and who carries the bomb", () => {
    const { rerender } = renderViewer({ currentTick: 200 });
    const bar = screen.getByRole("group", { name: "回合状态" });
    expect(within(bar).getByRole("img", { name: "T 存活 2/2" })).toBeInTheDocument();
    expect(hud("T").querySelector(".map-hud-name")).toHaveTextContent("Spirit");
    expect(hud("CT").querySelector(".map-hud-name")).toHaveTextContent("MOUZ");
    // Clock from the round's start, as the transport counts it.
    expect(bar.querySelector(".map-hud-clock")).toHaveTextContent("0:01");
    expect(screen.getByTestId("bomb-status")).toHaveTextContent("炸弹：Alpha 携带");

    rerender(<ReplayViewer replay={replayV2()} currentTick={600} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    const ctPips = within(bar).getByRole("img", { name: "CT 存活 1/2" });
    expect(ctPips.querySelectorAll("i")).toHaveLength(2);
    expect(ctPips.querySelectorAll("i.alive")).toHaveLength(1);
    expect(bar.querySelector(".map-hud-clock")).toHaveTextContent("0:07");
  });

  it("gives the round's verdict in the winner's colour from the round's end, instead of the bomb", () => {
    const { rerender } = renderViewer({ currentTick: 899 });
    expect(document.querySelector(".map-hud-verdict")).toBeNull();
    rerender(<ReplayViewer replay={replayV2()} currentTick={900} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    const verdict = document.querySelector(".map-hud-verdict")!;
    expect(verdict).toHaveTextContent("T 胜：全歼");
    expect(verdict).toHaveClass("side-t");
    expect(screen.queryByTestId("bomb-status")).toBeNull();

    rerender(<ReplayViewer replay={replayV2()} currentTick={1800} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    expect(document.querySelector(".map-hud-verdict")).toHaveTextContent("CT 胜：时间耗尽");
    expect(document.querySelector(".map-hud-verdict")).toHaveClass("side-ct");
  });

  it("names a planted bomb with its site when the demo gives one, and stays quiet when the bomb is unknown", () => {
    const base = replayV2();
    const planted = { ...base, frames: base.frames.map((frame) => frame.tick === 300
      ? { ...frame, bombState: { status: "planted" as const, x: 40, y: 40, site: "A" } } : frame) };
    const { rerender } = renderViewer({ replay: planted, currentTick: 300 });
    expect(screen.getByTestId("bomb-status")).toHaveTextContent("炸弹：已安装 A 点");
    rerender(<ReplayViewer replay={planted} currentTick={1100} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    expect(screen.getByTestId("bomb-status")).toBeEmptyDOMElement();
  });

  it("draws killer-to-victim traces for this round's kills of the last 2 s, stepping down in opacity", () => {
    const { rerender } = renderViewer({ currentTick: 499 });
    expect(traces()).toEqual([]);
    const at = (tick: number) => rerender(<ReplayViewer replay={replayV2()} currentTick={tick} selectedPlayerId={V2_ALPHA}
      onSelectPlayer={vi.fn()} variant="featured" teamNames={TEAMS} />);
    at(520);
    // Alpha (killer) and Delta (victim) where they stood at the kill tick, in the killer's colour.
    expect(traces()).toEqual([{ side: "T", x1: "25", y1: "30", x2: "70", y2: "60", opacity: "0.95", dashed: false }]);
    at(560);
    expect(traces().map((line) => line.opacity)).toEqual(["0.7"]);
    at(620);
    expect(traces().map((line) => line.opacity)).toEqual(["0.45"]);
    at(640);
    expect(traces()).toEqual([]);
    // Only the round on screen: round 2 shows its own kill, never round 1's.
    at(1310);
    expect(traces()).toEqual([expect.objectContaining({ side: "CT", opacity: "0.95" })]);
  });

  it("caps the traces at four", () => {
    renderViewer({ replay: withKills([505, 510, 515, 520, 525]), currentTick: 530 });
    expect(traces()).toHaveLength(4);
  });

  it("dashes a trace with one end on the other floor and drops one with both ends there", () => {
    const base = replayV2({ mapName: "de_nuke" });
    const lowerDelta = { ...base, frames: base.frames.map((frame) => frame.tick === 500
      ? { ...frame, players: frame.players.map((player) => player.id === V2_DELTA ? { ...player, z: -600 } : player) } : frame) };
    const { rerender } = renderViewer({ replay: lowerDelta, currentTick: 520 });
    expect(traces()).toEqual([expect.objectContaining({ dashed: true })]);
    rerender(<ReplayViewer replay={lowerDelta} currentTick={520} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} levelMode="lower" />);
    expect(traces()).toEqual([expect.objectContaining({ dashed: true })]);
    const bothLower = { ...lowerDelta, frames: lowerDelta.frames.map((frame) => frame.tick === 500
      ? { ...frame, players: frame.players.map((player) => player.id === V2_ALPHA ? { ...player, z: -600 } : player) } : frame) };
    rerender(<ReplayViewer replay={bothLower} currentTick={520} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} levelMode="upper" />);
    expect(traces()).toEqual([]);
  });

  it("lists up to five kills of the last 5 s in the feed, newest first, with the weapon and 爆头", () => {
    const { rerender } = renderViewer({ currentTick: 720 });
    expect(feedRows()).toEqual(["Charlie 击杀 ✕BravoM4A1-S", "Alpha 击杀 ✕DeltaAK-47爆头"]);
    const names = [...document.querySelectorAll(".map-kill-feed .map-kill-name")];
    expect(names[0]).toHaveClass("side-ct");
    expect(names[1]).toHaveClass("side-t");
    rerender(<ReplayViewer replay={withKills([505, 510, 515, 520, 525, 530])} currentTick={720} selectedPlayerId={V2_ALPHA}
      onSelectPlayer={vi.fn()} variant="featured" teamNames={TEAMS} />);
    expect(feedRows()).toHaveLength(5);
    expect(feedRows()[0]).toContain("Charlie");
    // Kills of another round never show.
    rerender(<ReplayViewer replay={replayV2()} currentTick={1100} selectedPlayerId={V2_ALPHA} onSelectPlayer={vi.fn()}
      variant="featured" teamNames={TEAMS} />);
    expect(feedRows()).toEqual([]);
  });

  it("reads the traces and the feed from the whole match when the map gets the reviewed player's events only", () => {
    const full = replayV2();
    const scoped = { ...full, events: full.events.filter((event) => event.playerIds.includes(V2_DELTA)) };
    renderViewer({ replay: scoped, matchReplay: full, selectedPlayerId: V2_DELTA, currentTick: 720 });
    expect(feedRows()[0]).toContain("Charlie");
    expect(traces()).toEqual([expect.objectContaining({ side: "CT" })]);
  });

  it("locks the reviewed player in corner brackets and keeps the dashed ring for a clicked player", () => {
    renderViewer();
    const alpha = dotOf("T", 1);
    expect(document.querySelector(".map-player-reviewed")).toBeNull();
    // The dot is drawn last among the dots; the bracket and the name on its plate go over everything,
    // at the dot's position.
    expect([...document.querySelectorAll(".map-player-dot")].at(-1)).toBe(alpha);
    const lock = document.querySelector(".map-lock-marker")!;
    expect(lock.getAttribute("transform")).toBe(alpha.getAttribute("transform"));
    expect(lock.querySelector(".map-lock path")).not.toBeNull();
    expect(lock.querySelector(".map-player-label-plate")).not.toBeNull();
    expect(lock.querySelector(".map-player-name")).toHaveTextContent("Alpha");
    expect(alpha.querySelector(".map-player-name")).toBeNull();
    fireEvent.click(dotOf("CT", 1));
    expect(dotOf("CT", 1).querySelector(".map-player-highlight")).not.toBeNull();
    expect(dotOf("CT", 1).querySelector(".map-player-name")).toHaveTextContent("Charlie");
    expect(document.querySelectorAll(".map-lock")).toHaveLength(1);
  });

  it("dims the lock while the finder's 只看 is on somebody else, and draws none without a reviewed player", () => {
    const { rerender } = renderViewer({ focusPlayerId: V2_CHARLIE });
    expect(document.querySelector(".map-lock-marker")).toHaveClass("focus-dimmed");
    expect(document.querySelector(".map-lock-marker")).toHaveAttribute("opacity", "0.25");
    rerender(<ReplayViewer replay={replayV2()} currentTick={200} selectedPlayerId={null} onSelectPlayer={vi.fn()} />);
    expect(document.querySelector(".map-lock-marker")).toBeNull();
  });

  it("snaps the bracket once per jump to a suggestion or new reviewed player, never on playback", () => {
    const view = (props: { tick?: number; jump?: unknown; player?: string }) => (
      <ReplayViewer replay={replayV2()} currentTick={props.tick ?? 200} selectedPlayerId={props.player ?? V2_ALPHA}
        onSelectPlayer={vi.fn()} variant="featured" teamNames={TEAMS} lockSnapKey={props.jump ?? null} />
    );
    const { rerender } = render(view({}));
    expect(document.querySelector(".map-lock")).not.toHaveClass("snap");
    rerender(view({ tick: 300 }));
    expect(document.querySelector(".map-lock")).not.toHaveClass("snap");

    const jump = { id: "finding-1" };
    rerender(view({ tick: 310, jump }));
    const snapped = document.querySelector(".map-lock")!;
    expect(snapped).toHaveClass("snap");
    fireEvent.animationEnd(snapped);
    expect(document.querySelector(".map-lock")).not.toHaveClass("snap");
    // Playback with the same suggestion in focus does not replay it.
    rerender(view({ tick: 330, jump }));
    expect(document.querySelector(".map-lock")).not.toHaveClass("snap");
    // A manual seek (null) is not a jump; a second jump, even to the same suggestion, is a new value.
    rerender(view({ tick: 340, jump: null }));
    expect(document.querySelector(".map-lock")).not.toHaveClass("snap");
    rerender(view({ tick: 350, jump: { id: "finding-1" } }));
    expect(document.querySelector(".map-lock")).toHaveClass("snap");
    fireEvent.animationEnd(document.querySelector(".map-lock")!);
    // A new reviewed player snaps too.
    rerender(view({ tick: 350, jump: null, player: V2_CHARLIE }));
    expect(document.querySelector(".map-lock-marker .map-player-name")).toHaveTextContent("Charlie");
    expect(document.querySelector(".map-lock")).toHaveClass("snap");
  });
});
