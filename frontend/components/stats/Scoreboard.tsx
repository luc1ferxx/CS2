import { memo } from "react";

import { teamDisplayName } from "@/components/replay/MatchScoreBanner";
import type { MatchTeam, PlayerMatchStats } from "@/lib/match-stats";

interface ScoreboardProps {
  teams: MatchTeam[];
  // Sorted by +/- then kills (playerMatchStats already returns that order).
  stats: PlayerMatchStats[];
  reviewedPlayerId: string | null;
}

/**
 * 计分板: one section per team, its bar tinted by the side the team started on, and a dense
 * table of the match numbers. The damage bar is scaled to the best ADR of the match.
 */
export const Scoreboard = memo(function Scoreboard({ teams, stats, reviewedPlayerId }: ScoreboardProps) {
  if (teams.length === 0 || stats.length === 0) return null;
  const maxAdr = Math.max(1, ...stats.map((player) => player.adr ?? 0));
  return (
    <section className="panel scoreboard" aria-labelledby="scoreboard-title">
      <div className="panel-bar">
        <h2 className="panel-bar-title" id="scoreboard-title">计分板</h2>
        <span className="panel-bar-meta">按 +/- 排序</span>
      </div>
      {teams.map((team) => (
        <TeamScoreboard key={team.key} team={team} maxAdr={maxAdr} reviewedPlayerId={reviewedPlayerId}
          players={stats.filter((player) => player.teamKey === team.key)} />
      ))}
    </section>
  );
});

function TeamScoreboard({ team, players, maxAdr, reviewedPlayerId }: {
  team: MatchTeam;
  players: PlayerMatchStats[];
  maxAdr: number;
  reviewedPlayerId: string | null;
}) {
  const name = teamDisplayName(team);
  const side = team.startSide.toLowerCase();
  const titleId = `scoreboard-team-${team.key.toLowerCase()}`;
  return (
    <section className={`scoreboard-team start-${side}`} aria-labelledby={titleId}>
      <div className={`panel-bar panel-bar-${side} scoreboard-team-bar`}>
        <h3 className="panel-bar-title" id={titleId}>{name}</h3>
        <span className="panel-bar-meta">{team.startSide} 开局</span>
        <span className="scoreboard-team-score" aria-label={`${team.score} 回合`}>{team.score}</span>
      </div>
      {/* Phones scroll the table sideways inside the panel; the player column stays put. */}
      <div className="scoreboard-scroll" role="region" aria-label={`${name} 的数据（可左右滚动）`} tabIndex={0}>
        <table className="data-table scoreboard-table" aria-labelledby={titleId}>
          <thead>
            <tr>
              <th scope="col">玩家</th>
              <th scope="col" className="num">击杀-阵亡</th>
              <th scope="col" className="num" title="击杀减阵亡">+/-</th>
              <th scope="col" className="num">助攻</th>
              <th scope="col" className="num scoreboard-adr-head" title="对敌人造成的伤害 ÷ 参与的回合">伤害/回合</th>
              <th scope="col" className="num" title="爆头击杀 ÷ 击杀">爆头率</th>
              <th scope="col" className="num" title="有击杀、助攻、存活或阵亡后 5 秒内被补枪的回合占比">KAST</th>
              <th scope="col" className="num" title="回合第一个击杀">首杀</th>
              <th scope="col" className="num" title="回合第一个阵亡">首死</th>
            </tr>
          </thead>
          <tbody>
            {players.map((player) => {
              const reviewed = player.playerId === reviewedPlayerId;
              return (
                <tr key={player.playerId} className={reviewed ? "selected" : undefined} aria-current={reviewed ? "true" : undefined}>
                  <th scope="row" className="scoreboard-player">
                    <span className="scoreboard-player-name">{player.name}</span>
                    {reviewed ? <span className="scoreboard-reviewing">复盘中</span> : null}
                  </th>
                  <td className="num">{player.kills}-{player.deaths}</td>
                  <td className={`num scoreboard-diff${player.diff > 0 ? " plus" : player.diff < 0 ? " minus" : ""}`}>
                    {player.diff > 0 ? `+${player.diff}` : player.diff}
                  </td>
                  <td className="num">{player.assists}</td>
                  {/* Right-aligned like every number column: the bar sits before the figure. */}
                  <td className="num scoreboard-adr">
                    <span className="scoreboard-adr-bar" aria-hidden="true">
                      {player.adr ? <i style={{ width: `${Math.round((player.adr / maxAdr) * 100)}%` }} /> : null}
                    </span>
                    <span className="num">{player.adr === null ? "—" : player.adr.toFixed(1)}</span>
                  </td>
                  <td className="num">{percent(player.hsPercent)}</td>
                  <td className="num">{percent(player.kastPercent)}</td>
                  <td className="num">{player.openingKills}</td>
                  <td className="num">{player.openingDeaths}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)}%`;
}
