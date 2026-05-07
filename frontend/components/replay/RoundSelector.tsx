"use client";

import type { ReplayRound } from "@/types/replay";

interface RoundSelectorProps {
  rounds: ReplayRound[];
  selectedRound: number;
  onSelectRound: (roundNumber: number) => void;
}

export function RoundSelector({
  rounds,
  selectedRound,
  onSelectRound
}: RoundSelectorProps) {
  return (
    <select
      className="round-select"
      value={selectedRound}
      onChange={(event) => onSelectRound(Number(event.target.value))}
      aria-label="Select round"
    >
      {rounds.map((round) => (
        <option key={round.roundNumber} value={round.roundNumber}>
          Round {round.roundNumber}
        </option>
      ))}
    </select>
  );
}
