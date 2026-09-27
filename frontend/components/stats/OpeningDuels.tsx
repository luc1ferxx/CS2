"use client";

import type { OpeningDuel } from "@/lib/match-stats";
import { liveRoundTimeAt } from "@/lib/replay-time";
import type { ReplayData } from "@/types/replay";

interface OpeningDuelsProps {
  replay: Pick<ReplayData, "rounds" | "tickRate">;
  duels: OpeningDuel[];
  onSelectDuel: (duel: OpeningDuel) => void;
}

export function OpeningDuels({ replay, duels, onSelectDuel }: OpeningDuelsProps) {
  const won = duels.filter((duel) => duel.won).length;
  const lost = duels.length - won;

  return (
    <section className="match-analysis-cell opening-duels" aria-labelledby="opening-duels-title">
      <div className="match-analysis-cell-bar">
        <h3 id="opening-duels-title">开局对枪</h3>
      </div>
      <p className="opening-duels-score">
        <span>首杀 <strong className="num">{won}</strong> 次</span>
        <span>首死 <strong className="num">{lost}</strong> 次</span>
      </p>
      {duels.length === 0 ? (
        <p className="match-analysis-caption">这场比赛没有参与每回合的第一次击杀。</p>
      ) : (
        <ol className="data-rows opening-duel-list" aria-label="开局对枪的回合">
          {duels.map((duel) => {
            const round = replay.rounds.find((item) => item.roundNumber === duel.roundNumber);
            const time = liveRoundTimeAt(duel.tick, round, replay.tickRate);
            const outcome = duel.won ? "胜" : "负";
            return (
              <li key={`${duel.roundNumber}-${duel.tick}`}>
                <button type="button" className="text-button"
                  aria-label={`第 ${duel.roundNumber} 回合 ${time} 开局对枪${outcome}${duel.opponentName ? `，对手 ${duel.opponentName}` : ""}`}
                  onClick={() => onSelectDuel(duel)}>
                  第 {duel.roundNumber} 回合
                </button>
                <span className={`status-text ${duel.won ? "ok" : "failed"}`}>{outcome}</span>
                <span className="opening-duel-opponent">{duel.opponentName ?? "对手未知"}</span>
                <span className="opening-duel-time num">{time}</span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
