import type { ReplayData } from "@/types/replay";

// Replay contract v3 `inputs`: per player, sorted [tick, mask] change points; a mask holds from its
// tick until the next entry. Bits are the raw Source 2 usercmd button mask (only these nine are kept).
export const INPUT_BITS = {
  attack: 1,
  jump: 2,
  duck: 4,
  forward: 8,
  back: 16,
  left: 512,
  right: 1024,
  attack2: 2048,
  speed: 0x10000
} as const;

export interface PlayerButtons {
  forward: boolean;
  back: boolean;
  left: boolean;
  right: boolean;
  attack: boolean;
  attack2: boolean;
  jump: boolean;
  duck: boolean;
  speed: boolean;
}

type InputsReplay = Pick<ReplayData, "inputs"> | null | undefined;

function trackOf(replay: InputsReplay, playerId: string | null | undefined): Array<[number, number]> | null {
  if (!playerId) return null;
  const inputs = replay?.inputs;
  if (!inputs || typeof inputs !== "object" || !Object.hasOwn(inputs, playerId)) return null;
  const track = inputs[playerId];
  return Array.isArray(track) && track.length > 0 ? track : null;
}

// Whether any player has a key track (the replay is v3 and the demo carried usercmd data).
export function hasInputs(replay: InputsReplay): boolean {
  const inputs = replay?.inputs;
  if (!inputs || typeof inputs !== "object") return false;
  return Object.values(inputs).some((track) => Array.isArray(track) && track.length > 0);
}

// The player's button mask at `tick`: the last change point at or before it. Null without a track,
// or before the track's first point.
export function inputMaskAt(replay: InputsReplay, playerId: string | null | undefined, tick: number): number | null {
  const track = trackOf(replay, playerId);
  if (!track || !Number.isFinite(tick)) return null;
  let low = 0;
  let high = track.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (Number(track[middle]?.[0]) <= tick) low = middle + 1;
    else high = middle;
  }
  if (low === 0) return null;
  const mask = track[low - 1]?.[1];
  return typeof mask === "number" && Number.isFinite(mask) ? mask : null;
}

export function decodeButtons(mask: number): PlayerButtons {
  const has = (bit: number) => (mask & bit) !== 0;
  return {
    forward: has(INPUT_BITS.forward),
    back: has(INPUT_BITS.back),
    left: has(INPUT_BITS.left),
    right: has(INPUT_BITS.right),
    attack: has(INPUT_BITS.attack),
    attack2: has(INPUT_BITS.attack2),
    jump: has(INPUT_BITS.jump),
    duck: has(INPUT_BITS.duck),
    speed: has(INPUT_BITS.speed)
  };
}

// Spoken order of the key panel: the movement keys as on the keyboard, then the modifiers, then the mouse.
const KEY_LABELS: ReadonlyArray<[keyof PlayerButtons, string]> = [
  ["forward", "W"],
  ["left", "A"],
  ["back", "S"],
  ["right", "D"],
  ["speed", "静步"],
  ["duck", "蹲"],
  ["jump", "跳"],
  ["attack", "左键"],
  ["attack2", "右键"]
];

export function pressedKeyLabels(buttons: PlayerButtons): string[] {
  return KEY_LABELS.filter(([key]) => buttons[key]).map(([, label]) => label);
}

// "xelex 正在按：D、蹲、左键", or "xelex 没有按键".
export function describePressedKeys(buttons: PlayerButtons, playerName?: string | null): string {
  const name = typeof playerName === "string" && playerName.trim() ? `${playerName.trim()} ` : "";
  const pressed = pressedKeyLabels(buttons);
  return pressed.length > 0 ? `${name}正在按：${pressed.join("、")}` : `${name}没有按键`;
}
