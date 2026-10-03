import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MatchScoreBanner } from "@/components/replay/MatchScoreBanner";
import { matchTeams } from "@/lib/match-stats";
import { matchStatsReplay } from "@/lib/test-fixtures/match-stats";

function renderBanner(overrides: Partial<Parameters<typeof MatchScoreBanner>[0]> = {}) {
  return render(
    <MatchScoreBanner
      title="spirit-vs-mouz-m2-mirage.dem"
      teams={matchTeams(matchStatsReplay, [{ key: "B", name: "Bravo" }])}
      mapName="Mirage"
      date="2026-09-17T04:01:09Z"
      roundCount={28}
      yourTeamKey="A"
      reviewedTeamKey="A"
      {...overrides}
    >
      <span className="fact">3 条建议</span>
    </MatchScoreBanner>
  );
}

describe("MatchScoreBanner", () => {
  it("shows both teams, the final score with the leader highlighted and each half in its side's colour", () => {
    const { container } = renderBanner();
    const board = container.querySelector(".match-banner-board")!;
    expect(board).toHaveAttribute("aria-hidden", "true");
    const [scoreA, scoreB] = [...board.querySelectorAll(".match-banner-score")];
    expect(scoreA).toHaveTextContent("15");
    expect(scoreA).toHaveClass("leading");
    expect(scoreB).toHaveTextContent("13");
    expect(scoreB).not.toHaveClass("leading");

    const teamA = board.querySelector(".team-a")!;
    // A missing name falls back to the team's letter; the viewer's own team is marked.
    expect(teamA).toHaveTextContent("队伍 A你的队伍");
    const halves = [...teamA.querySelectorAll(".match-banner-half")].map((half) => half.textContent);
    expect(halves).toEqual(["上半场T 3", "下半场CT 9", "加时3"]);
    expect(teamA.querySelector(".match-banner-half-score.side-t")).toHaveTextContent("T 3");
    expect(teamA.querySelector(".match-banner-half-score.side-ct")).toHaveTextContent("CT 9");
    expect(board.querySelector(".team-b")).toHaveTextContent(/^Bravo上半场CT 9下半场T 3加时1$/);
  });

  it("reads the score as one sentence to screen readers", () => {
    renderBanner();
    expect(screen.getByText(/^比分 /)).toHaveTextContent(
      "比分 队伍 A（你的队伍） 15 比 13 Bravo。上半场 队伍 A T 3，Bravo CT 9；下半场 队伍 A CT 9，Bravo T 3；加时 队伍 A 3，Bravo 1。"
    );
  });

  it("keeps the demo name as the page heading with the match facts beside it", () => {
    const { container } = renderBanner();
    const meta = container.querySelector<HTMLElement>(".match-banner-meta")!;
    expect(within(meta).getByRole("heading", { level: 1, name: "spirit-vs-mouz-m2-mirage.dem" })).toBeInTheDocument();
    expect(meta).toHaveTextContent("Mirage");
    expect(meta).not.toHaveTextContent("地图");
    expect(meta).toHaveTextContent(/上传2026年9月1[67]日/);
    expect(meta).toHaveTextContent("28 回合");
    expect(meta).toHaveTextContent("3 条建议");
    expect(meta.textContent).not.toContain("·");
  });

  it("marks neither team when the reviewed player is unknown and both lead on a draw", () => {
    const teams = matchTeams(matchStatsReplay).map((team) => ({ ...team, score: 12 }));
    const { container } = renderBanner({ teams, yourTeamKey: null, reviewedTeamKey: null, date: null });
    expect(container.querySelectorAll(".match-banner-score.leading")).toHaveLength(2);
    expect(container.querySelector(".match-banner-mine")).toBeNull();
    expect(container.querySelector(".match-banner-meta")).not.toHaveTextContent("上传");
  });

  it("never calls the reviewed player's team yours when the viewer reviews someone else", () => {
    const { container } = renderBanner({ yourTeamKey: "A", reviewedTeamKey: "B" });
    expect(container.querySelector(".team-a")).toHaveTextContent("队伍 A你的队伍");
    expect(container.querySelector(".team-b")).toHaveTextContent(/^Bravo复盘中的队伍/);
    expect(screen.getByText(/^比分 /)).toHaveTextContent(/^比分 队伍 A（你的队伍） 15 比 13 Bravo（复盘中的队伍）。/);

    const { container: noIdentity } = renderBanner({ yourTeamKey: null, reviewedTeamKey: "A" });
    expect(noIdentity.querySelector(".team-a")).toHaveTextContent("队伍 A复盘中的队伍");
    expect(noIdentity.textContent).not.toContain("你的队伍");
  });

  it("compacts to the workbench band: halves in each team's tooltip, the round count shown", () => {
    const { container } = renderBanner({ compact: true });
    expect(container.querySelector(".match-banner")).toHaveClass("compact");
    expect(container.querySelector(".match-banner-halves")).toBeNull();
    expect(container.querySelector(".team-a")).toHaveAttribute("title", "上半场 T 3，下半场 CT 9，加时 3");
    expect(container.querySelector(".team-b")).toHaveTextContent(/^Bravo$/);
    const roundCount = [...container.querySelectorAll(".match-banner-meta .fact")].find((fact) => fact.textContent === "28 回合");
    expect(roundCount).not.toHaveClass("visually-hidden");
    // The spoken sentence and the heading do not change.
    expect(screen.getByText(/^比分 /)).toHaveTextContent(/上半场 队伍 A T 3，Bravo CT 9/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("spirit-vs-mouz-m2-mirage.dem");

    const { container: full } = renderBanner();
    expect(full.querySelector(".match-banner")).not.toHaveClass("compact");
    expect(full.querySelector(".team-a")).not.toHaveAttribute("title");
  });

  it("falls back to the title and facts when the teams cannot be told apart", () => {
    const { container } = renderBanner({ teams: [] });
    expect(container.querySelector(".match-banner-board")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("spirit-vs-mouz-m2-mirage.dem");
  });
});
