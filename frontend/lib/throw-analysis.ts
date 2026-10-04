import { INPUT_BITS, inputMaskAt } from "@/lib/player-inputs";
import { landingPoint, utilityRadiusPercent, type MapScaleSource } from "@/lib/utility";
import type { ReplayData, ReplayThrowOrigin, ReplayUtility } from "@/types/replay";

/*
 * 道具投掷分析: how one grenade left the thrower's hand, read from the replay's key track (contract
 * v3 `inputs`) and the release pose (contract v4 `throwOrigin`). Pure and best effort: without keys
 * the style and button are unknown (null), without a pose there is no copy-position command.
 * Throw-style rules adapted from bugkingZHT/cs2-sandbox useGrenadeAnalyzer.ts (MIT, Copyright (c)
 * 2026 huN7er): jump and duck first, then moving, else standing.
 */

export type ThrowStyle = "跳蹲投" | "跳投" | "蹲投" | "走投" | "站投";
export type ThrowButton = "left" | "right" | "both";

export const THROW_BUTTON_LABELS: Record<ThrowButton, string> = {
  left: "左键扔",
  right: "右键扔",
  both: "左右键一起扔"
};

/** The analysis window: this long before and after the release. */
export const THROW_WINDOW_SECONDS = 1;
/** Horizontal speed (units per second) from which a throw counts as moving. */
export const MOVING_SPEED = 40;

/** A square box in radar percent (the SVG viewBox the finder zooms to). */
export interface ThrowViewBox {
  x: number;
  y: number;
  size: number;
}

export const FULL_VIEW_BOX: ThrowViewBox = { x: 0, y: 0, size: 100 };

export interface ThrowAnalysis {
  utilityId: string;
  throwerId: string | null;
  releaseTick: number;
  /** releaseTick - 1 s, not before the throw's round start, never below 0. */
  windowStart: number;
  /** releaseTick + 1 s. */
  windowEnd: number;
  /** The keys just before release; null = no key data for the thrower. */
  releaseMask: number | null;
  /** Null when releaseMask is null. */
  style: ThrowStyle | null;
  button: ThrowButton | null;
  moving: boolean | null;
  /** Horizontal speed at release (u/s), when the pose carries it. */
  speed: number | null;
  /** Movement keys held at release, in W A S D order. */
  heldMoveKeys: string[];
  /** The console command that puts a player at the release pose; null without a pose. */
  command: string | null;
  hint: string | null;
  /** The thrower's positions (radar percent) in the frames inside the window, alive only. */
  throwerPath: { tick: number; x: number; y: number }[];
  /** The zoom box around the flight, the landing effect and the thrower's path. */
  bounds: ThrowViewBox;
}

// The grenade leaves the hand when the throw button (attack or attack2) goes up.
const THROW_BUTTONS = INPUT_BITS.attack | INPUT_BITS.attack2;
// The parser's throw tick can sit a few ticks before the button-up it belongs to.
const RELEASE_LOOKAHEAD_TICKS = 8;
const JUMP_LOOKBACK_SECONDS = 0.25;
const MOVE_KEYS: ReadonlyArray<[number, string]> = [
  [INPUT_BITS.forward, "W"],
  [INPUT_BITS.left, "A"],
  [INPUT_BITS.back, "S"],
  [INPUT_BITS.right, "D"]
];
const MIN_BOX_SIZE = 24;
const MIN_BOX_PAD = 4;
const BOX_PAD_SHARE = 0.12;

const MOVING_HINT_NO_KEYS = "出手时还在移动：站在出手点不动扔，落点会偏";
const JUMP_HINT = "跳投：练习时用跳投绑定，不然每次出手高度不一样";

type InputTrack = ReadonlyArray<readonly [number, number]>;

export function analyzeThrow(replay: ReplayData, utility: ReplayUtility, map?: MapScaleSource | null): ThrowAnalysis {
  const rate = safeRate(replay.tickRate);
  const windowTicks = Math.round(THROW_WINDOW_SECONDS * rate);
  const throwerId = utility.throwerId ?? null;
  const track = inputTrack(replay, throwerId);
  const throwTick = utility.throwTick;

  const releaseTick = track ? releaseTickOf(track, throwTick, windowTicks) : throwTick;
  const round = Array.isArray(replay.rounds) ? replay.rounds.find((item) => item.roundNumber === utility.roundNumber) : undefined;
  const roundStart = round && isFiniteNumber(round.startTick) ? round.startTick : 0;
  const windowStart = Math.max(0, roundStart, releaseTick - windowTicks);
  const windowEnd = releaseTick + windowTicks;

  const releaseMask = inputMaskAt(replay, throwerId, releaseTick - 1);
  const origin = throwOriginOf(replay, utility);
  const speed = origin && isFiniteNumber(origin.speed) && origin.speed >= 0 ? origin.speed : null;
  const has = (bit: number) => releaseMask !== null && (releaseMask & bit) !== 0;

  const attack = has(INPUT_BITS.attack);
  const attack2 = has(INPUT_BITS.attack2);
  const button: ThrowButton | null = attack && attack2 ? "both" : attack ? "left" : attack2 ? "right" : null;
  const heldMoveKeys = MOVE_KEYS.filter(([bit]) => has(bit)).map(([, key]) => key);
  const jump = has(INPUT_BITS.jump)
    || (track !== null && jumpPressedIn(track, releaseTick - Math.round(JUMP_LOOKBACK_SECONDS * rate), releaseTick))
    || origin?.airborne === true;
  const duck = has(INPUT_BITS.duck);
  const moving = speed !== null ? speed >= MOVING_SPEED : releaseMask !== null ? heldMoveKeys.length > 0 : null;
  const style: ThrowStyle | null = releaseMask === null ? null
    : jump && duck ? "跳蹲投"
      : jump ? "跳投"
        : duck ? "蹲投"
          : moving ? "走投" : "站投";

  let hint: string | null = null;
  if (style === "走投") {
    hint = heldMoveKeys.length > 0
      ? `边走边扔（按着 ${heldMoveKeys.join("、")}）：站在出手点不动扔，落点会偏`
      : MOVING_HINT_NO_KEYS;
  } else if (style === "跳投" || style === "跳蹲投") {
    // A running jump throw: a jumpthrow bind from the standing pose lands elsewhere.
    hint = moving !== true ? JUMP_HINT
      : heldMoveKeys.length > 0
        ? `跑跳投（按着 ${heldMoveKeys.join("、")}）：出手时还在移动，原地跳投落点会偏`
        : "跑跳投：出手时还在移动，原地跳投落点会偏";
  } else if (style === null && speed !== null && speed >= MOVING_SPEED) {
    hint = MOVING_HINT_NO_KEYS;
  }

  const throwerPath = pathInWindow(replay, throwerId, windowStart, windowEnd);
  const scale: MapScaleSource = map ?? {
    transform: replay.mapMetadata?.transform ?? null,
    worldUnitsPerPercent: replay.mapMetadata?.worldUnitsPerPercent ?? null
  };
  const bounds = boundsOf(utility, throwerPath, utilityRadiusPercent(utility.type, scale));

  return {
    utilityId: utility.id,
    throwerId,
    releaseTick,
    windowStart,
    windowEnd,
    releaseMask,
    style,
    button,
    moving,
    speed,
    heldMoveKeys,
    command: origin ? setposCommand(origin) : null,
    hint,
    throwerPath,
    bounds
  };
}

/** The thrower's position at a tick, interpolated along the path; null outside it. */
export function throwerPositionAt(analysis: ThrowAnalysis, tick: number): { x: number; y: number } | null {
  const path = analysis.throwerPath;
  if (path.length === 0 || !Number.isFinite(tick)) return null;
  const first = path[0];
  const last = path[path.length - 1];
  if (tick < first.tick || tick > last.tick) return null;
  let low = 0;
  let high = path.length - 1;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (path[middle].tick <= tick) low = middle;
    else high = middle - 1;
  }
  const from = path[low];
  const to = path[low + 1];
  if (!to || to.tick <= from.tick) return { x: from.x, y: from.y };
  const share = (tick - from.tick) / (to.tick - from.tick);
  return { x: round2(from.x + (to.x - from.x) * share), y: round2(from.y + (to.y - from.y) * share) };
}

/** `setpos x y z; setang pitch yaw 0`, the console command that recreates the release pose. */
export function setposCommand(origin: ReplayThrowOrigin): string {
  return `setpos ${fixed(origin.x, 1)} ${fixed(origin.y, 1)} ${fixed(origin.z, 1)}; `
    + `setang ${fixed(origin.pitch, 2)} ${fixed(origin.yaw, 2)} 0`;
}

/** Ticks from the release as signed seconds: -1 → "-0.02s" at 64 tick, 0 → "0.00s", 64 → "+1.00s". */
export function formatRelativeSeconds(ticks: number, tickRate: number, digits = 2): string {
  const seconds = ticks / safeRate(tickRate);
  const zero = (0).toFixed(digits);
  if (!Number.isFinite(seconds)) return `${zero}s`;
  const text = Math.abs(seconds).toFixed(digits);
  if (text === zero) return `${zero}s`;
  return `${seconds < 0 ? "-" : "+"}${text}s`;
}

/**
 * The release pose, validated (x, y, z, pitch and yaw finite, else none). Read from the throw, or
 * from the replay's raw entry with the same id: `replayUtility()` rebuilds throws field by field.
 */
export function throwOriginOf(
  replay: Pick<ReplayData, "utility">,
  utility: Pick<ReplayUtility, "id"> & { throwOrigin?: unknown }
): ReplayThrowOrigin | null {
  const own = validOrigin(utility.throwOrigin);
  if (own) return own;
  const raw = Array.isArray(replay.utility)
    ? replay.utility.find((item) => Boolean(item) && typeof item === "object" && item.id === utility.id)
    : undefined;
  return raw ? validOrigin((raw as { throwOrigin?: unknown }).throwOrigin) : null;
}

function validOrigin(value: unknown): ReplayThrowOrigin | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<Record<keyof ReplayThrowOrigin, unknown>>;
  if (![item.x, item.y, item.z, item.pitch, item.yaw].every(isFiniteNumber)) return null;
  const origin: ReplayThrowOrigin = {
    x: item.x as number, y: item.y as number, z: item.z as number, pitch: item.pitch as number, yaw: item.yaw as number
  };
  if (isFiniteNumber(item.speed) && item.speed >= 0) origin.speed = item.speed;
  if (typeof item.airborne === "boolean") origin.airborne = item.airborne;
  return origin;
}

function inputTrack(replay: Pick<ReplayData, "inputs">, playerId: string | null): InputTrack | null {
  const inputs = replay.inputs;
  if (!playerId || !inputs || typeof inputs !== "object" || !Object.hasOwn(inputs, playerId)) return null;
  const track = inputs[playerId];
  return Array.isArray(track) && track.length > 0 ? track : null;
}

function tickOf(entry: readonly [number, number] | undefined): number {
  const tick = Number(entry?.[0]);
  return Number.isFinite(tick) ? tick : Number.NaN;
}

function maskOf(entry: readonly [number, number] | undefined): number {
  const mask = Number(entry?.[1]);
  return Number.isFinite(mask) ? mask : 0;
}

// First change point at or after `tick`.
function firstIndexFrom(track: InputTrack, tick: number): number {
  let low = 0;
  let high = track.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (tickOf(track[middle]) < tick) low = middle + 1;
    else high = middle;
  }
  return low;
}

// The throw button's falling edge in [throwTick - 1 s, throwTick + 8 ticks] closest to the throw
// tick (the earlier one on a tie); the throw tick itself when there is none.
function releaseTickOf(track: InputTrack, throwTick: number, windowTicks: number): number {
  const from = throwTick - windowTicks;
  const to = throwTick + RELEASE_LOOKAHEAD_TICKS;
  let best: number | null = null;
  for (let index = Math.max(1, firstIndexFrom(track, from)); index < track.length; index += 1) {
    const tick = tickOf(track[index]);
    if (!(tick <= to)) break;
    const wasHeld = (maskOf(track[index - 1]) & THROW_BUTTONS) !== 0;
    const isHeld = (maskOf(track[index]) & THROW_BUTTONS) !== 0;
    if (wasHeld && !isHeld && (best === null || Math.abs(tick - throwTick) < Math.abs(best - throwTick))) best = tick;
  }
  return best ?? throwTick;
}

// Whether any change point in [from, to] has the jump bit.
function jumpPressedIn(track: InputTrack, from: number, to: number): boolean {
  for (let index = firstIndexFrom(track, from); index < track.length; index += 1) {
    if (!(tickOf(track[index]) <= to)) break;
    if ((maskOf(track[index]) & INPUT_BITS.jump) !== 0) return true;
  }
  return false;
}

function pathInWindow(
  replay: Pick<ReplayData, "frames">,
  playerId: string | null,
  start: number,
  end: number
): ThrowAnalysis["throwerPath"] {
  const frames = Array.isArray(replay.frames) ? replay.frames : [];
  if (!playerId || frames.length === 0) return [];
  let low = 0;
  let high = frames.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (frames[middle].tick < start) low = middle + 1;
    else high = middle;
  }
  // Frames are sampled every 0.25 s: take the one on each side of the window too and cut the path at
  // the window's ends, so the thrower is drawn across the whole slow-motion window.
  const path: ThrowAnalysis["throwerPath"] = [];
  const first = low < frames.length && frames[low].tick === start ? low : Math.max(0, low - 1);
  for (let index = first; index < frames.length; index += 1) {
    const frame = frames[index];
    const player = Array.isArray(frame.players) ? frame.players.find((item) => item.id === playerId) : undefined;
    if (player && player.alive && isFiniteNumber(player.x) && isFiniteNumber(player.y)) {
      path.push({ tick: frame.tick, x: player.x, y: player.y });
    }
    if (frame.tick >= end) break;
  }
  if (path.length >= 2 && path[0].tick < start) path[0] = pointAt(path[0], path[1], start);
  const lastIndex = path.length - 1;
  if (path.length >= 2 && path[lastIndex].tick > end) path[lastIndex] = pointAt(path[lastIndex - 1], path[lastIndex], end);
  return path.filter((point) => point.tick >= start && point.tick <= end);
}

function pointAt(
  from: ThrowAnalysis["throwerPath"][number],
  to: ThrowAnalysis["throwerPath"][number],
  tick: number
): ThrowAnalysis["throwerPath"][number] {
  if (to.tick <= from.tick) return { tick, x: from.x, y: from.y };
  const share = Math.min(1, Math.max(0, (tick - from.tick) / (to.tick - from.tick)));
  return { tick, x: round2(from.x + (to.x - from.x) * share), y: round2(from.y + (to.y - from.y) * share) };
}

// A square around the flight, the landing effect and the thrower's path, padded, at least 24 and at
// most 100 wide, kept inside the map.
function boundsOf(utility: ReplayUtility, path: ThrowAnalysis["throwerPath"], radius: number): ThrowViewBox {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const point of [...(Array.isArray(utility.points) ? utility.points : []), ...path]) {
    if (isFiniteNumber(point?.x) && isFiniteNumber(point?.y)) {
      xs.push(point.x);
      ys.push(point.y);
    }
  }
  if (xs.length === 0) return { ...FULL_VIEW_BOX };
  const landing = landingPoint(utility);
  if (landing && isFiniteNumber(landing.x) && isFiniteNumber(landing.y) && radius > 0) {
    xs.push(landing.x - radius, landing.x + radius);
    ys.push(landing.y - radius, landing.y + radius);
  }
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY);
  const pad = Math.max(MIN_BOX_PAD, span * BOX_PAD_SHARE);
  const size = Math.min(100, Math.max(MIN_BOX_SIZE, span + pad * 2));
  const x = clamp((minX + maxX) / 2 - size / 2, 0, 100 - size);
  const y = clamp((minY + maxY) / 2 - size / 2, 0, 100 - size);
  return { x: round2(x), y: round2(y), size: round2(size) };
}

function fixed(value: number, digits: number): string {
  const text = value.toFixed(digits);
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function safeRate(tickRate: number): number {
  return Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
