import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Scoreboard } from "@/components/stats/Scoreboard";
import { matchTeams, playerMatchStats } from "@/lib/match-stats";
import { matchStatsReplay } from "@/lib/test-fixtures/match-stats";

function renderScoreboard(reviewedPlayerId: string | null = "a2") {
  return render(
    <Scoreboard
      teams={matchTeams(matchStatsReplay, [{ key: "A", name: "Alpha" }])}
      stats={playerMatchStats(matchStatsReplay)}
      reviewedPlayerId={reviewedPlayerId}
    />
  );
}

function rowsOf(region: HTMLElement) {
  return within(region).getAllByRole("row").slice(1);
}

describe("Scoreboard", () => {
  it("gives each team a section tinted by its starting side, with its name and score", () => {
    renderScoreboard();
    const board = screen.getByRole("region", { name: "计分板" });
    const alpha = within(board).getByRole("region", { name: "Alpha" });
    expect(alpha.querySelector(".panel-bar")).toHaveClass("panel-bar-t");
    expect(alpha.querySelector(".scoreboard-team-score")).toHaveTextContent("15");
    const bravo = within(board).getByRole("region", { name: "队伍 B" });
    expect(bravo.querySelector(".panel-bar")).toHaveClass("panel-bar-ct");
    expect(bravo.querySelector(".scoreboard-team-score")).toHaveTextContent("13");
  });

  it("lists the numbers in the column order of the header, sorted by +/- then kills", () => {
    renderScoreboard();
    const alpha = screen.getByRole("table", { name: "Alpha" }) as HTMLTableElement;
    expect([...alpha.tHead!.rows[0].cells].map((cell) => cell.textContent)).toEqual([
      "玩家", "击杀-阵亡", "+/-", "助攻", "伤害/回合", "爆头率", "KAST", "首杀", "首死"
    ]);
    const [ace, ash] = rowsOf(screen.getByRole("region", { name: "Alpha" }));
    expect([...ace.children].map((cell) => cell.textContent)).toEqual([
      "Ace", "3-3", "0", "0", "3.6", "33%", "93%", "2", "0"
    ]);
    expect(ash.children[0]).toHaveTextContent("Ash复盘中");

    const bravoRows = rowsOf(screen.getByRole("region", { name: "队伍 B" }));
    expect(bravoRows.map((row) => row.children[0].textContent)).toEqual(["Cinder", "Blaze", "Bolt"]);
    const bolt = bravoRows[2];
    expect(bolt.children[2]).toHaveTextContent("-2");
    expect(bolt.children[2]).toHaveClass("minus");
    // No kills: no headshot rate.
    expect(bravoRows[0].children[5]).toHaveTextContent("—");
  });

  it("marks the reviewed player's row and scales the damage bars to the best ADR of the match", () => {
    renderScoreboard("a2");
    const alphaRows = rowsOf(screen.getByRole("region", { name: "Alpha" }));
    expect(alphaRows[1]).toHaveClass("selected");
    expect(alphaRows[1]).toHaveAttribute("aria-current", "true");
    expect(alphaRows[0]).not.toHaveClass("selected");
    const bar = (row: HTMLElement) => row.querySelector<HTMLElement>(".scoreboard-adr-bar > i")!.style.width;
    expect(bar(alphaRows[0])).toBe("100%");
    // Blaze: 50 over 28 rounds against the best 100 over 28.
    expect(bar(rowsOf(screen.getByRole("region", { name: "队伍 B" }))[1])).toBe("50%");
  });

  it("keeps each table in a keyboard-reachable scroll region and marks nobody without a reviewed player", () => {
    const { container } = renderScoreboard(null);
    const scrollers = container.querySelectorAll(".scoreboard-scroll");
    expect(scrollers).toHaveLength(2);
    scrollers.forEach((scroller) => expect(scroller).toHaveAttribute("tabindex", "0"));
    expect(container.querySelector("tr.selected")).toBeNull();
    expect(screen.queryByText("复盘中")).toBeNull();
  });

  it("renders nothing without teams", () => {
    const { container } = render(<Scoreboard teams={[]} stats={[]} reviewedPlayerId={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
