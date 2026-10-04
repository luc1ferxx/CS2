import type { ReviewEvent } from "@/lib/coaching-review";
import type { CoachingEvent, CoachingVerdict } from "@/types/coaching";
import type { ReplayRound } from "@/types/replay";

interface CoachingCopy {
  title: string;
  guidance: string;
  limitation: string;
}

const SHOT_LIMITATION = "速度取自每一枪记录的移动速度；没有计算弹道恢复、蹲下、开镜和对手的移动，没打中也可能有别的原因。";

const RULE_COPY: Record<string, CoachingCopy> = {
  untraded_death: {
    title: "复盘这次未被补枪的交火",
    guidance: "再次尝试这个对枪前，先确认谁能补枪，并等队友准备好。",
    limitation: "没有补枪不代表这次死亡一定可以避免；视野、战术意图和语音沟通都未被记录。"
  },
  isolated_entry: {
    title: "检查首次交火的支援距离",
    guidance: "和最近的队友一起回看首次交火，确认后续补枪的路线与时机。",
    limitation: "采样位置的直线距离无法判断视野、可走路线或队友责任，也不等于实际移动距离。"
  },
  poor_spacing: {
    title: "检查与队友的站位间距",
    guidance: "交火前留出避免被一起扫射的空间，同时确保队友能够跟上补枪。",
    limitation: "单个位置采样只提示值得回看的时刻；绕后、交叉火力、高低差和集中站位都可能是战术安排。直线距离不等于实际移动距离。"
  },
  post_plant_spread: {
    title: "检查下包后的交叉火力",
    guidance: "下包后尝试覆盖不同的枪线，同时保留与队友互相补枪的机会。",
    limitation: "采样位置接近不代表枪线一定重复，也无法证明整段时间都缺少覆盖；直线距离不等于实际移动距离。"
  },
  post_plant_spacing_with_bomb_event: {
    title: "检查下包后的站位分工",
    guidance: "回看下包后的站位，确认各自覆盖不同方向，并能够互相补枪。",
    limitation: "下包事件有记录，但仅凭采样距离无法判断枪线覆盖是否合理；直线距离不等于实际移动距离。"
  },
  retake_desync: {
    title: "检查回防进场的时机",
    guidance: "对照队友到达的时间，在能够协同回防时约定一起接触或投闪的信号。",
    limitation: "先后到达可能是战术安排；与炸弹的距离不能证明回防或交火何时开始，也不等于实际移动距离。"
  },
  weak_utility_before_execute: {
    title: "回看下包前的道具配合",
    guidance: "回看队伍下包前的道具，检查暴露的进场路线是否需要闪光或烟雾掩护。",
    limitation: "这是团队上下文，不能归责于下包者；下包时间不等于进攻开始，较早或未记录的道具也可能有效。"
  },
  late_post_plant_utility: {
    title: "回看下包后的道具时机",
    guidance: "回看触发这次投掷的情况，判断把道具留到对手回防时是否更合适。",
    limitation: "间隔较长不一定代表投掷太晚；保留道具可能合理，部分道具事件也可能缺失。"
  },
  moving_shots: {
    title: "边移动边开枪",
    guidance: "步枪、狙击枪和沙鹰要先停下再开枪：反向点一下移动键，速度降下来再打。",
    limitation: SHOT_LIMITATION
  },
  no_counter_strafe: {
    title: "第一枪没有急停",
    guidance: "练习急停：松开移动键并反向点一下，再开第一枪。",
    limitation: SHOT_LIMITATION
  }
};

const EVIDENCE_LABELS: Record<string, string> = {
  verticalDistanceWorldUnits: "高度差（世界坐标单位）",
  maxStackedVerticalDistanceWorldUnits: "过近候选的最大高度差",
  relatedEventIds: "关联事件", distance: "直线距离（世界坐标单位）", windowSeconds: "观察窗口（秒）",
  evidenceTicks: "证据位置（tick）", utilityType: "道具类型", utilityLabel: "道具",
  utilityTypes: "道具类型", utilityCount: "记录的道具数", requiredUtilityCount: "规则参考数量",
  bombTick: "炸弹事件位置（tick）", bombEventType: "炸弹事件类型", bombEventLabel: "炸弹事件",
  graceWindowSeconds: "规则参考间隔（秒）", nearbyCount: "相关队友数", spacingType: "间距类型",
  side: "阵营", site: "包点", sameAreaDistance: "同区域距离阈值", isolatedTeammateDistance: "支援距离阈值",
  poorSpacingMinDistance: "最小间距阈值", poorSpacingMaxDistance: "最大间距阈值",
  maxNearestDistance: "最远的最近队友距离", minPairDistance: "最近的两人距离",
  clusterDistance: "集中站位距离阈值", retakeSiteDistance: "回防距离阈值",
  weapon: "武器代码", weaponLabel: "武器", speed: "开枪时速度（单位/秒）", accurateSpeed: "稳定线（单位/秒）",
  shotCount: "这次连射枪数", movingShotCount: "移动中开的枪数", airborne: "在空中开枪", hit: "有命中",
  died: "2 秒内阵亡", keysAtShot: "开枪时按着的移动键", counterStrafe: "有反向急停", occurrencesInRound: "本回合出现次数"
};

function ruleId(event: CoachingEvent): string {
  const value = event.structured_context_json.ruleId ?? event.structured_context_json.rule;
  return typeof value === "string" ? value : "";
}

function knownCopy(id: string): CoachingCopy | undefined {
  const key = id === "post_plant_spread_issue" ? "post_plant_spread" : id;
  return Object.prototype.hasOwnProperty.call(RULE_COPY, key) ? RULE_COPY[key] : undefined;
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function coachingCopy(event: CoachingEvent): CoachingCopy {
  const context = event.structured_context_json;
  const known = knownCopy(ruleId(event));
  if (!known) {
    return {
      title: textValue(event.title) || "值得回看的时刻",
      guidance: textValue(context.action) || textValue(event.message) || "回看这一刻，结合队友位置判断当时的选择。",
      limitation: textValue(context.limitation)
    };
  }
  let title = known.title;
  let guidance = known.guidance;
  let limitation = known.limitation;
  if (ruleId(event) === "poor_spacing") {
    if (context.spacingType === "stacked") title = "检查过近的队友站位";
    if (context.spacingType === "too_far") {
      title = "检查与队友的支援距离";
      guidance = "接触敌人前，先确认队友能否及时支援；必要时等队友靠近或准备好再一起接触。";
      limitation = "单个位置采样不能判断视野、可走路线或战术意图；绕后、交叉火力和分散控图都可能合理。直线距离不等于实际移动距离。";
    }
  }
  const approximate = textValue(context.limitation).includes("Map calibration is approximate.");
  return { title, guidance, limitation: `${limitation}${approximate ? "当前地图校准为近似值。" : ""}` };
}

export function coachingRuleLabel(id: string, fallback: string): string {
  return knownCopy(id)?.title ?? fallback;
}

export function coachingSeverityLabel(severity: string): string {
  return ({ critical: "优先回看", high: "优先回看", medium: "值得留意", low: "细节建议", info: "复盘提示" } as Record<string, string>)[severity] ?? severity;
}

export function coachingEvidenceLabel(label: string): string {
  return Object.prototype.hasOwnProperty.call(EVIDENCE_LABELS, label) ? EVIDENCE_LABELS[label] : label;
}

export function coachingLocation(event: CoachingEvent, rounds: ReplayRound[] = [], tickRate?: number): string {
  const round = rounds.find((candidate) => candidate.roundNumber === event.round_number);
  const roundLabel = Number.isFinite(event.round_number) ? `第 ${event.round_number} 回合` : "回合未知";
  if (!round || !Number.isFinite(round.startTick) || !Number.isFinite(event.tick_start) ||
    !Number.isFinite(tickRate) || (tickRate ?? 0) <= 0 || event.tick_start < round.startTick) return roundLabel;
  const seconds = Math.floor((event.tick_start - round.startTick) / (tickRate as number));
  return `${roundLabel} · ${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

export function coachingMatchesSearch(reviewEvent: ReviewEvent, rawSearch: string): boolean {
  const search = rawSearch.trim().toLocaleLowerCase();
  if (!search) return true;
  const { event } = reviewEvent;
  const copy = coachingCopy(event);
  return [copy.title, copy.guidance, event.title, event.message, event.player_id, event.player_name,
    reviewEvent.ruleId, reviewEvent.ruleLabel, ...reviewEvent.involvedPlayers,
    ...reviewEvent.evidence.flatMap((item) => [item.label, coachingEvidenceLabel(item.label), item.value])]
    .join(" ").toLocaleLowerCase().includes(search);
}

// The three verdicts a player can give a suggestion (docs/coaching_feedback_v1.md).
export const COACHING_VERDICT_LABELS: Record<CoachingVerdict, string> = {
  helpful: "有帮助",
  irrelevant: "无关",
  unsure: "判断不足"
};
