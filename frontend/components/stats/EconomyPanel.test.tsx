import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EconomyPanel } from "@/components/stats/EconomyPanel";
import { matchTeams } from "@/lib/match-stats";
import { roundEconomies } from "@/lib/round-economy";
import { replayData } from "@/lib/test-fixtures/review";
import { FULL_BUY, economyReplay, type EconomyRoundSpec } from "@/lib/test-fixtures/round-economy";
import type { ReplayData } from "@/types/replay";

// Five a side; MOUZ (team A) starts T, the sides swap after round 3, so round 4 is the second pistol round.
// MOUZ: 手枪局 2 (won 1), 全起 6 (won 3). Spirit: 手枪局 2 (won 1), 全起 3 (won 2), 强起 1, ECO 1 (won), 半起 1.
const SPECS: EconomyRoundSpec[] = [
  { sideA: "T", winner: "CT", a: [700, 100], b: [850, 0] },
  { sideA: "T", winner: "T", a: [4500, 1500], b: [4200, 500] },
  { sideA: "T", winner: "T", a: FULL_BUY, b: [2500, 200] },
  { sideA: "CT", winner: "CT", a: [900, 0], b: [800, 50] },
  { sideA: "CT", winner: "T", a: [5000, 300], b: [600, 3000] },
  { sideA: "CT", winner: "T", a: FULL_BUY, b: FULL_BUY },
  { sideA: "CT", winner: "CT", a: [4000, 0], b: [2500, 1500] },
  { sideA: "CT", winner: "T", a: FULL_BUY, b: FULL_BUY }
];
const NAMES = [{ key: "A", name: "MOUZ" }, { key: "B", name: "Spirit" }];

function renderPanel(replay: ReplayData = economyReplay(SPECS), overrides: Partial<Parameters<typeof EconomyPanel>[0]> = {}) {
  const props = {
    economies: roundEconomies(replay),
    teams: matchTeams(replay, NAMES),
    selectedRound: 1,
    onSelectRound: vi.fn(),
    ...overrides
  };
  const view = render(<EconomyPanel {...props} />);
  return { ...props, container: view.container };
}

const pairs = () => within(screen.getByRole("group", { name: /每回合装备价值柱状图/ })).getAllByRole("button");

describe("EconomyPanel", () => {
  it("draws two bars per round in the side each team played, with the winner marked", () => {
    renderPanel();
    expect(screen.getByRole("region", { name: "经济" })).toBeInTheDocument();
    expect(pairs().map((pair) => pair.getAttribute("aria-label"))).toEqual([
      "第 1 回合，MOUZ 手枪局 $3,500，Spirit 手枪局 $4,250，Spirit 胜",
      "第 2 回合，MOUZ 全起 $22,500，Spirit 全起 $21,000，MOUZ 胜",
      "第 3 回合，MOUZ 全起 $22,500，Spirit 强起 $12,500，MOUZ 胜",
      "第 4 回合，MOUZ 手枪局 $4,500，Spirit 手枪局 $4,000，MOUZ 胜",
      "第 5 回合，MOUZ 全起 $25,000，Spirit ECO $3,000，Spirit 胜",
      "第 6 回合，MOUZ 全起 $22,500，Spirit 全起 $22,500，Spirit 胜",
      "第 7 回合，MOUZ 全起 $20,000，Spirit 半起 $12,500，MOUZ 胜",
      "第 8 回合，MOUZ 全起 $22,500，Spirit 全起 $22,500，Spirit 胜"
    ]);
    const [first, , , fourth] = pairs();
    // MOUZ on the left in T gold for the first half, Spirit on the right (the lighter bar) in CT blue; swapped after the switch.
    expect([...first.querySelectorAll(".economy-chart-bar")].map((bar) => bar.getAttribute("class")))
      .toEqual(["economy-chart-bar team-a side-t", "economy-chart-bar team-b side-ct"]);
    expect([...fourth.querySelectorAll(".economy-chart-bar")].map((bar) => bar.getAttribute("class")))
      .toEqual(["economy-chart-bar team-a side-ct", "economy-chart-bar team-b side-t"]);
    // Bar heights follow the equipment value; one winner mark per round; one divider at the switch of sides.
    const height = (pair: HTMLElement, team: "a" | "b") => Number(pair.querySelector(`.economy-chart-bar.team-${team}`)?.getAttribute("height"));
    expect(height(pairs()[4], "a")).toBeGreaterThan(height(pairs()[4], "b") * 8);
    expect(document.querySelectorAll(".economy-chart-win")).toHaveLength(8);
    expect(document.querySelectorAll(".economy-chart-half")).toHaveLength(1);
    // Dashed 5-player thresholds and a $30k top (nobody bought more).
    expect([...document.querySelectorAll(".economy-chart-tick")].map((tick) => tick.textContent)).toEqual(["$30k", "$5k", "$10k", "$20k", "$0"]);
    expect(document.querySelectorAll(".economy-chart-threshold")).toHaveLength(3);
    // The key says what the dashed lines are.
    expect(document.querySelector(".economy-legend")).toHaveTextContent("5 人 ECO、半起、全起分界");
    expect(document.querySelectorAll(".economy-legend .swatch.threshold")).toHaveLength(1);
  });

  describe("round numbers", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });
    const labels = () => [...document.querySelectorAll(".economy-chart-round")]
      .map((label) => `${label.textContent}${label.classList.contains("selected") ? "*" : ""}`);

    it("number every 3rd round and always the selected one, in bold", () => {
      renderPanel(undefined, { selectedRound: 4 });
      expect(labels()).toEqual(["3", "4*", "6"]);
    });

    it("let a neighbour give way to the selected round's number where the slots are narrow", () => {
      // 180 px: 16 px per round, so 6 and 7 would touch; 3 and 4 sit either side of the half-time gap.
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 180 } as DOMRect);
      renderPanel(undefined, { selectedRound: 7 });
      expect(labels()).toEqual(["3", "7*"]);
    });
  });

  it("jumps to a round on click and moves between rounds with one Tab stop", async () => {
    const user = userEvent.setup();
    const props = renderPanel(undefined, { selectedRound: 3 });
    const all = pairs();
    expect(all.map((pair) => pair.getAttribute("tabindex"))).toEqual(["-1", "-1", "0", "-1", "-1", "-1", "-1", "-1"]);
    expect(all[2]).toHaveAttribute("aria-current", "step");
    await user.click(all[4]);
    expect(props.onSelectRound).toHaveBeenLastCalledWith(5);

    all[4].focus();
    await user.keyboard("{ArrowRight}");
    expect(all[5]).toHaveFocus();
    expect(all[5]).toHaveAttribute("tabindex", "0");
    await user.keyboard("{Enter}");
    expect(props.onSelectRound).toHaveBeenLastCalledWith(6);
    await user.keyboard("{Home}");
    expect(all[0]).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(all[0]).toHaveFocus();
    await user.keyboard("{End}");
    expect(all[7]).toHaveFocus();
    await user.keyboard(" ");
    expect(props.onSelectRound).toHaveBeenLastCalledWith(8);
  });

  it("repeats the chart as a table for screen readers", () => {
    renderPanel();
    const table = screen.getByRole("table", { name: "每回合装备价值" });
    const row = within(table).getByRole("row", { name: /第 5 回合/ });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "CT 方，全起，$25,000", "T 方，ECO，$3,000", "Spirit 胜"
    ]);
  });

  it("counts rounds and wins per buy type, with the full-buy win rate, and compares full buys in one sentence", () => {
    renderPanel();
    const table = screen.getByRole("table", { name: "经济类型战绩" });
    const values = (kind: string) => within(within(table).getByRole("row", { name: new RegExp(`^${kind}`) }))
      .getAllByRole("cell").map((cell) => cell.textContent);
    expect(within(table).getAllByRole("rowheader").map((header) => header.textContent)).toEqual(["手枪局", "全起", "强起", "半起", "ECO"]);
    expect(values("手枪局")).toEqual(["2", "1", "2", "1"]);
    expect(values("全起")).toEqual(["6", "3 (50%)", "3", "2 (67%)"]);
    // The rate is its own unbreakable piece, so a narrow column wraps it under the number instead of
    // running out of the cell; the win columns are the wider ones.
    expect([...table.querySelectorAll(".economy-wins .economy-rate")].map((rate) => rate.textContent)).toEqual(["(50%)", "(67%)"]);
    expect([...table.querySelectorAll("colgroup > col")].map((col) => col.className)).toEqual([
      "economy-col-kind", "economy-col-rounds", "economy-col-wins", "economy-col-rounds", "economy-col-wins"
    ]);
    expect(values("强起")).toEqual(["—", "—", "1", "0"]);
    expect(values("半起")).toEqual(["—", "—", "1", "0"]);
    expect(values("ECO")).toEqual(["—", "—", "1", "1"]);
    expect(within(table).getByRole("columnheader", { name: "Spirit 胜" })).toBeInTheDocument();
    const sentence = document.querySelector(".economy-sentence");
    expect(sentence).toHaveTextContent(/^全起对全起：MOUZ 全起 6 回合赢 3，Spirit 全起 3 回合赢 2。$/);
    // A wrap can only fall between the two teams' clauses.
    expect([...(sentence?.querySelectorAll(".economy-clause") ?? [])].map((clause) => clause.textContent))
      .toEqual(["MOUZ 全起 6 回合赢 3，", "Spirit 全起 3 回合赢 2。"]);
  });

  it("leaves the sentence out until both teams have three full buys, and names teams 队伍 A / 队伍 B without names", () => {
    renderPanel(economyReplay(SPECS.slice(0, 3)), { teams: [] });
    expect(screen.queryByText(/全起对全起/)).not.toBeInTheDocument();
    expect(pairs()[1]).toHaveAccessibleName("第 2 回合，队伍 A 全起 $22,500，队伍 B 全起 $21,000，队伍 A 胜");
  });

  it("is not shown for a replay without economy data", () => {
    const withoutStates = renderPanel({ ...economyReplay(SPECS), playerStates: {} });
    expect(withoutStates.container).toBeEmptyDOMElement();
    const v1 = renderPanel(replayData());
    expect(v1.container).toBeEmptyDOMElement();
    expect(screen.queryByRole("region", { name: "经济" })).not.toBeInTheDocument();
  });
});
