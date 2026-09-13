import type { ReplayFrame } from "@/types/replay";

// Frame state changes at its recorded tick. Only positions are interpolated.
export function getFrameForTick(frames: ReplayFrame[], tick: number, tickRate = 64): ReplayFrame | null {
  if (!frames.length) return null;
  if (tick <= frames[0].tick) return frames[0];
  let low = 0;
  let high = frames.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (frames[middle].tick <= tick) low = middle + 1;
    else high = middle;
  }
  const previous = frames[low - 1];
  const next = frames[low];
  if (!next || previous.tick === tick || previous.roundNumber !== next.roundNumber ||
      next.tick - previous.tick > tickRate) return previous;
  const progress = (tick - previous.tick) / (next.tick - previous.tick);
  const nextPlayers = new Map(next.players.map((player) => [player.id, player]));
  return {
    ...previous,
    tick,
    timeSeconds: previous.timeSeconds + (next.timeSeconds - previous.timeSeconds) * progress,
    players: previous.players.map((player) => {
      const following = nextPlayers.get(player.id);
      if (!following || !player.alive || player.alive !== following.alive || player.side !== following.side) {
        return player;
      }
      return {
        ...player,
        x: player.x + (following.x - player.x) * progress,
        y: player.y + (following.y - player.y) * progress,
        ...(typeof player.z === "number" && typeof following.z === "number"
          ? { z: player.z + (following.z - player.z) * progress } : {})
      };
    })
  };
}
