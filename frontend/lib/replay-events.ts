import type { PlayerSide, ReplayEvent, ReplayEventType } from "@/types/replay";

export type ParserEventTone = "combat" | "damage" | "objective" | "utility" | "own-kill" | "own-death";

export interface ParserEventPresentation {
  label: string;
  tone: ParserEventTone;
  shortLabel: string;
}

export interface ParserEventTimelineMarker {
  event: ReplayEvent;
  leftPercent: number;
  seekTick: number;
  presentation: ParserEventPresentation;
  description: string;
  // The side a kill counts for (the killer's), shown in the team colour; null for everything else.
  side: PlayerSide | null;
}

// One Chinese label and one distinct glyph per event type, shared by the map and the timeline.
const EVENT_PRESENTATION: Record<ReplayEventType, ParserEventPresentation> = {
  kill: { label: "击杀", tone: "combat", shortLabel: "击" },
  death: { label: "阵亡", tone: "combat", shortLabel: "亡" },
  damage: { label: "伤害", tone: "damage", shortLabel: "伤" },
  bomb_pickup: { label: "拾取炸弹", tone: "objective", shortLabel: "拾" },
  bomb_dropped: { label: "丢下炸弹", tone: "objective", shortLabel: "丢" },
  bomb_planted: { label: "安装炸弹", tone: "objective", shortLabel: "包" },
  bomb_defused: { label: "拆除炸弹", tone: "objective", shortLabel: "拆" },
  bomb_exploded: { label: "炸弹爆炸", tone: "objective", shortLabel: "爆" },
  smoke: { label: "烟雾弹", tone: "utility", shortLabel: "烟" },
  flash: { label: "闪光弹", tone: "utility", shortLabel: "闪" },
  molotov: { label: "燃烧弹", tone: "damage", shortLabel: "火" },
  he: { label: "手雷", tone: "damage", shortLabel: "雷" },
  round_start: { label: "回合开始", tone: "objective", shortLabel: "始" },
  round_end: { label: "回合结束", tone: "objective", shortLabel: "终" }
};

const FALLBACK_PRESENTATION: ParserEventPresentation = {
  label: "事件",
  tone: "objective",
  shortLabel: "事"
};

// Damage floods the lane (hundreds per match); it stays on the map instead. The round lane
// above already marks the round's start and end.
const TIMELINE_HIDDEN_TYPES = new Set<string>(["damage", "round_start", "round_end"]);

const WEAPON_NAMES: Record<string, string> = {
  ak47: "AK-47", m4a1: "M4A4", m4a1_silencer: "M4A1-S", m4a1_silencer_off: "M4A1-S", awp: "AWP",
  ssg08: "SSG 08", aug: "AUG", sg556: "SG 553", famas: "法玛斯", galilar: "加利尔",
  usp_silencer: "USP-S", usp_silencer_off: "USP-S", hkp2000: "P2000", glock: "格洛克", p250: "P250",
  deagle: "沙漠之鹰", revolver: "R8 左轮", fiveseven: "FN57", tec9: "Tec-9", cz75a: "CZ75",
  elite: "双持贝瑞塔", mp9: "MP9", mac10: "MAC-10", mp7: "MP7", mp5sd: "MP5-SD", ump45: "UMP-45",
  p90: "P90", bizon: "PP-野牛", nova: "新星", xm1014: "XM1014", mag7: "MAG-7", sawedoff: "截短霰弹枪",
  negev: "内格夫", m249: "M249", scar20: "SCAR-20", g3sg1: "G3SG1", knife: "刀", knife_t: "刀",
  hegrenade: "手雷", inferno: "燃烧弹", molotov: "燃烧弹", incgrenade: "燃烧弹", taser: "电击枪",
  world: "环境"
};

export function parserEventPresentationForType(
  type: ReplayEventType | string
): ParserEventPresentation {
  return EVENT_PRESENTATION[type as ReplayEventType] ?? FALLBACK_PRESENTATION;
}

/** Kills read from the reviewed player's side: their own kill and their own death look different. */
export function parserEventPresentation(event: ReplayEvent, playerId: string | null = null): ParserEventPresentation {
  const base = parserEventPresentationForType(event.type);
  if (!playerId || (event.type !== "kill" && event.type !== "death")) return base;
  const { attackerId, victimId, assisterId } = killParticipants(event);
  if (victimId === playerId) return { label: "阵亡", tone: "own-death", shortLabel: "亡" };
  if (attackerId === playerId) return { label: "击杀", tone: "own-kill", shortLabel: "杀" };
  if (assisterId === playerId) return { label: "助攻", tone: "combat", shortLabel: "助" };
  return base;
}

/** "A 用 AK-47 击杀 B（爆头）" for kills; the Chinese label plus the player otherwise. */
export function describeParserEvent(event: ReplayEvent): string {
  const presentation = parserEventPresentationForType(event.type);
  if (event.type === "kill" || event.type === "death") {
    const { attackerName, victimName } = killParticipants(event);
    const weapon = weaponName(event.metadata?.weapon);
    const headshot = event.metadata?.headshot === true ? "（爆头）" : "";
    if (attackerName && victimName) {
      return `${attackerName}${weapon ? ` 用 ${weapon}` : ""} 击杀 ${victimName}${headshot}`;
    }
    if (victimName) return `${victimName} 阵亡${headshot}`;
  }
  return event.playerName ? `${presentation.label} · ${event.playerName}` : presentation.label;
}

/** The killer's side for a kill (the victim's for a bare death); other events carry no team colour. */
export function killSide(event: ReplayEvent): PlayerSide | null {
  if (event.type !== "kill" && event.type !== "death") return null;
  const metadata = event.metadata ?? {};
  const side = event.type === "kill"
    ? stringValue(metadata.attackerSide) ?? event.side
    : stringValue(metadata.victimSide) ?? event.side;
  return side === "T" || side === "CT" ? side : null;
}

export function weaponName(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const key = value.trim().toLowerCase().replace(/^weapon_/, "");
  return WEAPON_NAMES[key] ?? key.toUpperCase();
}

export function timelineParserEventMarkersForRound(
  events: ReplayEvent[],
  roundNumber: number,
  minTick: number,
  maxTick: number,
  playerId: string | null = null
): ParserEventTimelineMarker[] {
  const durationTicks = Math.max(1, maxTick - minTick);
  return events
    .filter((event) => event.roundNumber === roundNumber && !TIMELINE_HIDDEN_TYPES.has(event.type))
    .map((event) => ({
      event,
      leftPercent: roundPercent(((event.tick - minTick) / durationTicks) * 100),
      seekTick: event.tick,
      presentation: parserEventPresentation(event, playerId),
      description: describeParserEvent(event),
      side: killSide(event)
    }))
    .sort((left, right) => left.event.tick - right.event.tick);
}

/**
 * Groups markers that would sit closer than `minGapPx` on a lane `laneWidthPx` wide, so they
 * share one chip instead of hiding each other. A group is anchored at its first marker; a lane
 * that has not been measured yet (width 0) keeps every marker on its own.
 */
export function clusterTimelineMarkers<T extends { leftPercent: number }>(
  markers: T[],
  laneWidthPx: number,
  minGapPx: number
): T[][] {
  if (!(laneWidthPx > 0)) return markers.map((marker) => [marker]);
  const groups: T[][] = [];
  let anchorPx = Number.NEGATIVE_INFINITY;
  for (const marker of markers) {
    const x = (marker.leftPercent / 100) * laneWidthPx;
    const last = groups[groups.length - 1];
    if (last && x - anchorPx < minGapPx) {
      last.push(marker);
    } else {
      groups.push([marker]);
      anchorPx = x;
    }
  }
  return groups;
}

export function recentMapParserEvents(
  events: ReplayEvent[],
  roundNumber: number,
  currentTick: number,
  tickRate: number
): ReplayEvent[] {
  const nearbyWindowTicks = Math.max(1, tickRate * 2);
  return events
    .filter((event) => event.roundNumber === roundNumber)
    .filter((event) => Math.abs(event.tick - currentTick) <= nearbyWindowTicks)
    .filter((event) => Number.isFinite(event.x) && Number.isFinite(event.y))
    .sort((left, right) => Math.abs(left.tick - currentTick) - Math.abs(right.tick - currentTick))
    .slice(0, 4);
}

function killParticipants(event: ReplayEvent) {
  const metadata = event.metadata ?? {};
  return {
    attackerId: stringValue(metadata.attackerId),
    attackerName: stringValue(metadata.attackerName),
    victimId: stringValue(metadata.victimId),
    victimName: stringValue(metadata.victimName),
    assisterId: stringValue(metadata.assisterId)
  };
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function roundPercent(value: number) {
  return Math.round(value * 100) / 100;
}
