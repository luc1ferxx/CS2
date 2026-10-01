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

describe("ReplayViewer live roster", () => {
  it("shows each side's team, live equipment value and every player's kit (v2)", () => {
    renderViewer();
    expect(within(panel("T")).getByRole("heading", { level: 3 })).toHaveTextContent("Spirit T");
    expect(within(panel("CT")).getByRole("heading", { level: 3 })).toHaveTextContent("MOUZ CT");
    expect(panel("T").querySelector(".roster-equipment")).toHaveTextContent("装备 $2,100");
    expect(panel("CT").querySelector(".roster-equipment")).toHaveTextContent("装备 $1,650");

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
    expect(panel("CT").querySelector(".roster-equipment")).toHaveTextContent("装备 $10,550");
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
    expect(panel("CT").querySelector(".roster-equipment")).toHaveTextContent("装备 $800");
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
    expect(document.querySelector(".roster-equipment")).toBeNull();
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
    expect(above).toBe(children.length - 1);
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
    fireEvent.click(document.querySelectorAll(".map-player-dot")[3]);
    expect(onSelectPlayer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "切换为他的视角" }));
    expect(onSelectPlayer).toHaveBeenCalledWith(V2_DELTA);
  });
});
