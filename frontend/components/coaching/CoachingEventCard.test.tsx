import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { CoachingEventCard, coachingCardId } from "@/components/coaching/CoachingEventCard";
import { reviewEventForEvent } from "@/lib/coaching-review";
import { weaponName } from "@/lib/replay-events";
import { coachingEvent, deathCoachingEvent, renderJob, stackedSpacingEvent } from "@/lib/test-fixtures/review";
import type { CoachingEvent } from "@/types/coaching";

type CardProps = ComponentProps<typeof CoachingEventCard>;

function renderCard(event: CoachingEvent, overrides: Partial<CardProps> = {}) {
  const props: CardProps = {
    // The review panel maps weapon strings with replay-events' weaponName; so does this helper.
    reviewEvent: reviewEventForEvent(event, new Map([[event.player_id, event.player_name]]), { weaponLabel: weaponName }),
    active: false,
    inspected: false,
    clipRequesting: false,
    onToggleInspect: vi.fn(),
    onSeek: vi.fn(),
    onGenerateClip: vi.fn(),
    onFeedback: vi.fn(),
    ...overrides
  };
  render(<CoachingEventCard {...props} />);
  return props;
}

function verdictGroup() {
  return screen.getByRole("group", { name: /这条建议是否有帮助/ });
}

const untradedDeath = coachingEvent({
  id: "untraded-1",
  title: "Review an untraded death",
  message: "T Entry died; the recorded killer was not killed by a teammate within 5 seconds.",
  structured_context_json: {
    ruleId: "untraded_death",
    attackerName: "CT Anchor",
    windowSeconds: 5,
    distance: 1075.93,
    evidenceTicks: [400],
    relatedEventIds: ["kill-400-76561198000000002-76561198000000001"],
    action: "Check who can trade before retaking this duel.",
    limitation: "Positions cannot establish line of sight."
  }
});

describe("CoachingEventCard", () => {
  it("offers the three verdicts with none pressed for an unrated suggestion", () => {
    renderCard(coachingEvent());
    for (const label of ["有帮助", "无关", "判断不足"]) {
      expect(within(verdictGroup()).getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("shows the stored verdict, emits a new one, and clears the pressed one on a second click", async () => {
    const user = userEvent.setup();
    const event = coachingEvent({
      feedback: { verdict: "helpful", note: null, updated_at: "2026-09-18T12:00:00Z" }
    });
    const props = renderCard(event);

    expect(within(verdictGroup()).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "true");
    expect(within(verdictGroup()).getByRole("button", { name: "无关" })).toHaveAttribute("aria-pressed", "false");

    await user.click(within(verdictGroup()).getByRole("button", { name: "无关" }));
    expect(props.onFeedback).toHaveBeenLastCalledWith(event, "irrelevant");

    await user.click(within(verdictGroup()).getByRole("button", { name: "有帮助" }));
    expect(props.onFeedback).toHaveBeenLastCalledWith(event, null);
  });

  it("keeps the review actions next to the verdict", async () => {
    const user = userEvent.setup();
    const event = coachingEvent({ tick_start: 640, tick_end: 640 });
    const props = renderCard(event);

    await user.click(screen.getByRole("button", { name: /^查看这一刻：/ }));
    expect(props.onSeek).toHaveBeenCalledWith(640, event.id);

    await user.click(screen.getByRole("button", { name: /^查看依据：/ }));
    expect(props.onToggleInspect).toHaveBeenCalledWith(event.id);

    await user.click(screen.getByRole("button", { name: "生成视频" }));
    expect(props.onGenerateClip).toHaveBeenCalledWith(event);
  });

  it("carries the coaching-event-<id> anchor the review page returns to", () => {
    renderCard(untradedDeath);
    const card = screen.getByRole("article");
    expect(card).toHaveAttribute("id", "coaching-event-untraded-1");
    expect(coachingCardId("untraded-1")).toBe("coaching-event-untraded-1");
    // Focusable by script only, so returning to the card does not add a Tab stop.
    expect(card).toHaveAttribute("tabindex", "-1");
  });

  it("reads like a kill-feed row: clock, killer ✕ victim in side colors, then the finding", () => {
    renderCard(untradedDeath, { clock: "1:12", locationLabel: "第 1 回合 1:12", side: "T" });
    const row = screen.getByRole("article");
    const line = within(row).getByRole("heading", { level: 3 });

    expect(within(row).getByText("1:12")).toHaveAttribute("title", "第 1 回合 1:12");
    expect(within(line).getByText("CT Anchor")).toHaveClass("side-ct");
    expect(within(line).getByText("T Entry")).toHaveClass("reviewed", "side-t");
    expect(within(line).getByText("5 秒内没有队友补枪")).toBeInTheDocument();
    // Read aloud as a sentence with the severity; the ✕ glyph itself is decoration.
    expect(line).toHaveTextContent("CT Anchor 击杀 ✕T Entry5 秒内没有队友补枪，值得留意");
    expect(within(line).getByText("✕")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByText(/untraded_death|kill-400|tick/)).not.toBeInTheDocument();
  });

  it("keeps names neutral when the round's side is not known, and leads with the finding for other rules", () => {
    renderCard(untradedDeath);
    expect(screen.getByText("CT Anchor")).toHaveClass("side-unknown");

    const spacing = coachingEvent({ id: "spacing-1", structured_context_json: { ruleId: "poor_spacing", spacingType: "too_far", maxNearestDistance: 1326 } });
    renderCard(spacing, { clock: "2:05" });
    const card = document.getElementById("coaching-event-spacing-1") as HTMLElement;
    expect(within(card).getByRole("heading", { level: 3 })).toHaveTextContent("最近的队友约 1326 单位外");
    expect(card.querySelector(".coaching-feed-kill")).toBeNull();
  });

  it("opens the evidence in Chinese and keeps raw ids and the analyzer's English in 技术详情", () => {
    renderCard(untradedDeath, { inspected: true });
    const inspector = document.getElementById("coaching-event-untraded-1-evidence") as HTMLElement;
    const technical = inspector.querySelector("details.coaching-technical-details") as HTMLElement;

    expect(within(inspector).getByText("判断边界")).toBeInTheDocument();
    expect(within(inspector).getByText("被 CT Anchor 击杀，5 秒内没有队友补枪")).toBeInTheDocument();
    expect(within(inspector).getByText("规则").nextElementSibling).toHaveTextContent("无人补枪的阵亡");
    expect(within(inspector).getByText("玩家").nextElementSibling).toHaveTextContent("T Entry");
    expect(within(inspector).getByText("相关玩家")).toBeInTheDocument();
    expect(within(inspector).getByText("直线距离（世界坐标单位）")).toBeInTheDocument();

    expect(technical).not.toHaveAttribute("open");
    expect(within(technical).getByText("技术详情")).toBeInTheDocument();
    expect(within(technical).getByText(untradedDeath.message)).toBeInTheDocument();
    expect(within(technical).getByText("untraded_death")).toBeInTheDocument();
    expect(within(technical).getByText("kill-400-76561198000000002-76561198000000001")).toBeInTheDocument();
    // Everything outside 技术详情 is free of slugs, event ids, ticks and the English message.
    const outside = inspector.cloneNode(true) as HTMLElement;
    outside.querySelector("details")?.remove();
    expect(outside.textContent).not.toMatch(/untraded_death|kill-400|tick|died|选手/);
  });

  it("says in place when a verdict was not saved and re-sends that verdict", async () => {
    const user = userEvent.setup();
    const props = renderCard(untradedDeath, { feedbackState: { status: "failed", verdict: "helpful" } });

    expect(screen.getByRole("status")).toHaveTextContent("评价没有保存。");
    await user.click(screen.getByRole("button", { name: "重新保存" }));
    expect(props.onFeedback).toHaveBeenCalledWith(untradedDeath, "helpful");
  });

  it("asks for a reload without a re-send when the suggestion no longer exists", () => {
    renderCard(untradedDeath, { feedbackState: { status: "stale", verdict: "helpful" } });

    expect(screen.getByRole("status")).toHaveTextContent("建议已按新规则更新，请刷新页面");
    expect(screen.queryByRole("button", { name: "重新保存" })).not.toBeInTheDocument();
    expect(screen.queryByText("评价没有保存。")).not.toBeInTheDocument();
  });

  it("follows a death card's finding with the weapon, the player counts and a lost round as small chips", () => {
    renderCard(deathCoachingEvent(), { side: "T" });
    const line = screen.getByRole("heading", { level: 3 });
    const chips = Array.from(line.querySelectorAll(".coaching-feed-chip"), (chip) => chip.textContent);

    expect(chips).toEqual(["AK-47", "4v4→3v4", "回合输了"]);
    // The chips come after the finding, before the severity read aloud.
    expect(line).toHaveTextContent("CT Anchor 击杀 ✕T Entry5 秒内没有队友补枪，AK-47，4v4→3v4，回合输了，值得留意");
  });

  it("shows only the chips the analyzer recorded, and the stored weapon string when it has no name", () => {
    renderCard(deathCoachingEvent({}, {
      weapon: "mystery_gun", impact: { roundLost: false, aliveBefore: { own: 2, enemy: 3 } }, extraReasons: []
    }));
    const line = screen.getByRole("heading", { level: 3 });
    expect(Array.from(line.querySelectorAll(".coaching-feed-chip"), (chip) => chip.textContent)).toEqual(["MYSTERY_GUN"]);
    expect(document.querySelector(".coaching-card-reason")).toBeNull();
  });

  it("keeps older cards without the new context free of chips and extra lines", () => {
    renderCard(untradedDeath);
    expect(document.querySelector(".coaching-feed-chip")).toBeNull();
    expect(document.querySelector(".coaching-card-reason")).toBeNull();
  });

  it("adds one 另外 line under the finding for each reason folded into the death", () => {
    renderCard(deathCoachingEvent({}, {
      extraReasons: [
        { ruleId: "poor_spacing", spacingType: "too_far", distance: 1240, durationSeconds: 4.5, tick: 300 },
        { ruleId: "isolated_entry", distance: 980, tick: 400 }
      ]
    }));
    const lines = Array.from(document.querySelectorAll(".coaching-card-reason"), (line) => line.textContent);
    expect(lines).toEqual([
      "另外：阵亡前已经离最近的队友 1240 单位，持续 4.5 秒",
      "另外：这是本回合 T 方第一个阵亡，最近的队友约 980 单位外"
    ]);
  });

  it("names the killer on an isolated entry card when the analyzer recorded one", () => {
    renderCard(deathCoachingEvent({ id: "isolated-1" }, {
      ruleId: "isolated_entry", attackerName: "donk", distance: 980, isolatedTeammateDistance: 900, extraReasons: []
    }), { side: "T" });
    const line = screen.getByRole("heading", { level: 3 });
    expect(within(line).getByText("donk")).toHaveClass("side-ct");
    expect(within(line).getByText("T 方首个阵亡，最近的队友约 980 单位外")).toBeInTheDocument();
  });

  it("reads a shooting card as the shot's speed against the weapon's stable line, with its Chinese copy", async () => {
    const user = userEvent.setup();
    const event = coachingEvent({
      id: "ncs-1", category: "mechanics", severity: "low", title: "Review the first shot of a fight",
      message: "T Entry took the first AK-47 shot at 150 units/s (accurate at or below 73); no shot of the burst hit.",
      structured_context_json: {
        ruleId: "no_counter_strafe", weapon: "ak47", weaponLabel: "AK-47", accurateSpeed: 73, speed: 150, shotCount: 2,
        movingShotCount: 1, airborne: false, hit: false, died: true, attackerName: "CT Anchor", side: "T",
        keysAtShot: ["A"], counterStrafe: false, occurrencesInRound: 1, evidenceTicks: [400]
      }
    });
    const props = renderCard(event, { side: "T" });

    const line = screen.getByRole("heading", { level: 3 });
    expect(within(line).getByText("CT Anchor")).toHaveClass("side-ct");
    expect(within(line).getByText("第一枪时速度约 150（AK-47 稳定线 73），按着 A 没有反向急停，没打中")).toBeInTheDocument();
    expect(screen.getByText("练习急停：松开移动键并反向点一下，再开第一枪。")).toBeInTheDocument();
    expect(document.querySelector(".coaching-feed-chip")).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看依据：第一枪没有急停" }));
    expect(props.onToggleInspect).toHaveBeenCalledWith("ncs-1");
  });

  it("shows a shooting card's evidence and limits in Chinese when inspected", () => {
    renderCard(coachingEvent({
      id: "moving-1", category: "mechanics", severity: "low",
      structured_context_json: {
        ruleId: "moving_shots", weapon: "m4a1_silencer", weaponLabel: "M4A1-S", accurateSpeed: 76, speed: 201, shotCount: 4,
        movingShotCount: 3, hit: false, died: false, side: "CT", occurrencesInRound: 2
      }
    }), { inspected: true });

    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("边移动边开了 3 枪（最高速度约 201，M4A1-S 稳定线 76），一枪没中，这回合共 2 次");
    expect(screen.getByText("步枪、狙击枪和沙鹰要先停下再开枪：反向点一下移动键，速度降下来再打。")).toBeInTheDocument();
    expect(screen.getByText("速度取自每一枪记录的移动速度；没有计算弹道恢复、蹲下、开镜和对手的移动，没打中也可能有别的原因。")).toBeInTheDocument();
    const facts = document.querySelector(".coaching-inspector-facts") as HTMLElement;
    expect(within(facts).getByText("移动射击")).toBeInTheDocument();
    expect(within(facts).getByText("武器")).toBeInTheDocument();
    expect(within(facts).getByText("M4A1-S")).toBeInTheDocument();
    expect(within(facts).getByText("开枪时速度（单位/秒）")).toBeInTheDocument();
    expect(within(facts).getByText("移动中开的枪数")).toBeInTheDocument();
    expect(within(facts).queryByText("m4a1_silencer")).not.toBeInTheDocument();
  });

  it("says how long a stacked pair stayed together", () => {
    renderCard(stackedSpacingEvent());
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("两名队友相距约 88 单位，持续 3.5 秒");
    expect(document.querySelector(".coaching-feed-chip")).toBeNull();
  });

  it("marks the verdicts busy while a save is in flight and keeps the status line empty", () => {
    renderCard(untradedDeath, { feedbackState: { status: "saving", verdict: "unsure" } });
    expect(verdictGroup()).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("drops the clip button when no clip handler is given, keeping locate and verdicts", () => {
    renderCard(coachingEvent(), { onGenerateClip: undefined });

    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^查看这一刻：/ })).toBeInTheDocument();
    expect(within(verdictGroup()).getAllByRole("button")).toHaveLength(3);
  });

  it("offers a retry for a failed clip only when a clip handler is given", () => {
    const failed = renderJob({ status: "failed", video_status: "failed" });
    renderCard(coachingEvent(), { inspected: true, renderJob: failed });
    expect(screen.getByText("视频生成失败，可以点击重试。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("reports a failed clip without retry wording when clips cannot be generated", () => {
    const failed = renderJob({ status: "failed", video_status: "failed" });
    renderCard(coachingEvent(), { inspected: true, renderJob: failed, onGenerateClip: undefined });
    expect(screen.getByText("视频生成失败。")).toBeInTheDocument();
    expect(screen.queryByText(/重试/)).not.toBeInTheDocument();
  });
});
