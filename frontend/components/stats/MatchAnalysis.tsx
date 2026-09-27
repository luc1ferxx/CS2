"use client";

import { memo, useCallback, useMemo } from "react";

import { DeathMap } from "@/components/stats/DeathMap";
import { KillsPerRoundChart } from "@/components/stats/KillsPerRoundChart";
import { OpeningDuels } from "@/components/stats/OpeningDuels";
import { UtilitySummary } from "@/components/stats/UtilitySummary";
import {
  openingDuels,
  playerDeaths,
  playerMatchStats,
  playerRoundKills,
  utilityCounts,
  type OpeningDuel,
  type PlayerDeath
} from "@/lib/match-stats";
import { findingLeadInTick } from "@/lib/replay-time";
import type { ReplayData, ReplayPlayer } from "@/types/replay";

interface MatchAnalysisProps {
  // The full replay: every event, not the reviewed player's slice.
  replay: ReplayData;
  player: ReplayPlayer | null;
  // The page's shared seek (tick) and round change; both bring the stage into view.
  onSeekTick: (tick: number) => void;
  onSelectRound: (roundNumber: number) => void;
  onChoosePlayer: () => void;
}

// Memoized: the page re-renders every playback frame, this section only when the replay or player changes.
export const MatchAnalysis = memo(function MatchAnalysis({
  replay,
  player,
  onSeekTick,
  onSelectRound,
  onChoosePlayer
}: MatchAnalysisProps) {
  const playerId = player?.id ?? null;
  const hasEvents = (replay.events ?? []).length > 0;
  const roundKills = useMemo(() => (playerId ? playerRoundKills(replay, playerId) : []), [playerId, replay]);
  // match-stats files a kill after the round ended under that round and drops team-switch artefacts.
  const deaths = useMemo(() => (playerId ? playerDeaths(replay, playerId) : []), [playerId, replay]);
  const duels = useMemo(() => (playerId ? openingDuels(replay, playerId) : []), [playerId, replay]);
  const utility = useMemo(
    () => (playerId ? utilityCounts(replay, playerId) : { smoke: 0, flash: 0, molotov: 0, he: 0 }),
    [playerId, replay]
  );
  // Same count the scoreboard divides by, so the per-round columns agree.
  const roundsPlayed = useMemo(
    () => (playerId ? playerMatchStats(replay).find((stats) => stats.playerId === playerId)?.roundsPlayed ?? 0 : 0),
    [playerId, replay]
  );

  // Deaths and opening duels open a few seconds early, like "查看这一刻" on a suggestion. A death
  // after the round ended lands on the round's last tick, so the stage stays on that round.
  const seekBefore = useCallback((roundNumber: number, tick: number) => {
    const round = replay.rounds.find((item) => item.roundNumber === roundNumber);
    const leadIn = findingLeadInTick(tick, round, replay.tickRate);
    onSeekTick(round && Number.isFinite(round.endTick) ? Math.min(leadIn, round.endTick) : leadIn);
  }, [onSeekTick, replay.rounds, replay.tickRate]);
  const selectDeath = useCallback((death: PlayerDeath) => seekBefore(death.roundNumber, death.tick), [seekBefore]);
  const selectDuel = useCallback((duel: OpeningDuel) => seekBefore(duel.roundNumber, duel.tick), [seekBefore]);

  return (
    <section className="panel match-analysis" aria-labelledby="match-analysis-title">
      <div className="panel-bar">
        <h2 className="panel-bar-title" id="match-analysis-title">{player ? `数据：${player.name}` : "数据"}</h2>
        {player && hasEvents ? <span className="panel-bar-meta">点图上的点、柱子或回合，回放会跳到那一刻</span> : null}
      </div>
      {!player ? (
        <p className="match-analysis-empty">
          选择要复盘的玩家后，这里显示他的阵亡位置、每回合击杀、开局对枪和道具。
          <button type="button" className="text-button" onClick={onChoosePlayer}>选择玩家</button>
        </p>
      ) : !hasEvents ? (
        <p className="match-analysis-empty">这场比赛的回放没有击杀和道具记录，暂时无法统计。</p>
      ) : (
        // Keyed by player: the floor choice and the chart's keyboard position belong to one player.
        <div className="match-analysis-grid" key={player.id}>
          <DeathMap replay={replay} playerId={player.id} deaths={deaths} onSelectDeath={selectDeath} />
          <KillsPerRoundChart rows={roundKills} onSelectRound={onSelectRound} />
          <OpeningDuels replay={replay} duels={duels} onSelectDuel={selectDuel} />
          <UtilitySummary counts={utility} roundsPlayed={roundsPlayed} />
        </div>
      )}
    </section>
  );
});
