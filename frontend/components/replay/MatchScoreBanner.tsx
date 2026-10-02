import { memo, type ReactNode } from "react";

import type { MatchHalf, MatchTeam, TeamKey } from "@/lib/match-stats";

interface MatchScoreBannerProps {
  // The demo's name: the page heading, kept on the meta line.
  title: string;
  // [A, B] from matchTeams(); empty when the replay has no frames to tell the teams apart.
  teams: MatchTeam[];
  mapName: string;
  date: string | null;
  roundCount: number;
  // The viewer's own team (their saved or account identity), and the reviewed player's team;
  // they differ when the viewer reviews someone else.
  yourTeamKey: TeamKey | null;
  reviewedTeamKey: TeamKey | null;
  // Further facts for the meta line (suggestion count, archived, status).
  children?: ReactNode;
}

const DATE_FORMAT = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });

/**
 * The match header: both teams with their half scores (in the colour of the side they
 * played), the final score large in the middle, and the match facts underneath.
 * Memoized: the page re-renders on every playback frame, the banner only when its facts change.
 */
export const MatchScoreBanner = memo(function MatchScoreBanner({
  title, teams, mapName, date, roundCount, yourTeamKey, reviewedTeamKey, children
}: MatchScoreBannerProps) {
  const [teamA, teamB] = teams;
  const mark = (key: TeamKey) => teamMark(key, yourTeamKey, reviewedTeamKey);
  const hasScore = teams.length === 2 && teamA && teamB;
  const dateText = formatMatchDate(date);
  return (
    <div className="match-banner">
      {hasScore ? (
        <>
          <p className="visually-hidden">{scoreSentence(teamA, teamB, mark)}</p>
          <div className="match-banner-board" aria-hidden="true">
            <BannerTeam team={teamA} mark={mark("A")} />
            <span className={`hero-num match-banner-score score-a${teamA.score >= teamB.score ? " leading" : ""}`}>{teamA.score}</span>
            <span className="match-banner-colon">:</span>
            <span className={`hero-num match-banner-score score-b${teamB.score >= teamA.score ? " leading" : ""}`}>{teamB.score}</span>
            <BannerTeam team={teamB} mark={mark("B")} />
          </div>
        </>
      ) : null}
      <div className="match-banner-meta">
        <h1 className="match-banner-title">{title}</h1>
        <span className="fact"><span className="fact-label">地图</span>{mapName}</span>
        {dateText ? <span className="fact"><span className="fact-label">上传</span>{dateText}</span> : null}
        <span className="fact">{roundCount} 回合</span>
        {children}
      </div>
    </div>
  );
});

// "你的队伍" only for the viewer's own team; reviewing someone on the other team marks that
// team the way the scoreboard marks the player.
function teamMark(key: TeamKey, yourTeamKey: TeamKey | null, reviewedTeamKey: TeamKey | null): string | null {
  if (yourTeamKey === key) return "你的队伍";
  if (reviewedTeamKey === key) return "复盘中的队伍";
  return null;
}

function BannerTeam({ team, mark }: { team: MatchTeam; mark: string | null }) {
  return (
    <div className={`match-banner-team team-${team.key.toLowerCase()}`}>
      <span className="match-banner-name-line">
        <span className="match-banner-name">{teamDisplayName(team)}</span>
        {mark ? <span className="match-banner-mine">{mark}</span> : null}
      </span>
      <span className="match-banner-halves">
        {team.halves.map((half) => (
          <span key={half.label} className="match-banner-half">
            <span className="match-banner-half-label">{half.label}</span>
            <span className={`match-banner-half-score${half.side ? ` side-${half.side.toLowerCase()}` : ""}`}>
              {half.side ? `${half.side} ` : ""}{half.score}
            </span>
          </span>
        ))}
      </span>
    </div>
  );
}

export function teamDisplayName(team: Pick<MatchTeam, "key" | "name">): string {
  return team.name ?? `队伍 ${team.key}`;
}

function scoreSentence(teamA: MatchTeam, teamB: MatchTeam, mark: (key: TeamKey) => string | null): string {
  const label = (team: MatchTeam) => {
    const note = mark(team.key);
    return `${teamDisplayName(team)}${note ? `（${note}）` : ""}`;
  };
  const halves = teamA.halves.map((half, index) => {
    const other: MatchHalf | undefined = teamB.halves[index];
    return `${half.label} ${halfText(teamDisplayName(teamA), half)}，${halfText(teamDisplayName(teamB), other)}`;
  });
  return `比分 ${label(teamA)} ${teamA.score} 比 ${teamB.score} ${label(teamB)}。${halves.join("；")}。`;
}

function halfText(name: string, half: MatchHalf | undefined): string {
  if (!half) return `${name} 0`;
  return half.side ? `${name} ${half.side} ${half.score}` : `${name} ${half.score}`;
}

function formatMatchDate(value: string | null): string | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return DATE_FORMAT.format(timestamp);
}
