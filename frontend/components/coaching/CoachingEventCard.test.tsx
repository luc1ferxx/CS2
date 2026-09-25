import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CoachingEventCard } from "@/components/coaching/CoachingEventCard";
import { reviewEventForEvent } from "@/lib/coaching-review";
import { coachingEvent } from "@/lib/test-fixtures/review";
import type { CoachingEvent } from "@/types/coaching";

type CardProps = Parameters<typeof CoachingEventCard>[0];

function renderCard(event: CoachingEvent, overrides: Partial<CardProps> = {}) {
  const props: CardProps = {
    reviewEvent: reviewEventForEvent(event, new Map([[event.player_id, event.player_name]])),
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
    expect(props.onSeek).toHaveBeenCalledWith(640);

    await user.click(screen.getByRole("button", { name: /^查看依据：/ }));
    expect(props.onToggleInspect).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "生成视频" }));
    expect(props.onGenerateClip).toHaveBeenCalledWith(event);
  });

  it("drops the clip button when no clip handler is given, keeping locate and verdicts", () => {
    renderCard(coachingEvent(), { onGenerateClip: undefined });

    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^查看这一刻：/ })).toBeInTheDocument();
    expect(within(verdictGroup()).getAllByRole("button")).toHaveLength(3);
  });
});
