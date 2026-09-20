import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewCommandBar } from "@/components/replay/ReviewCommandBar";
import { coachingEvent } from "@/lib/test-fixtures/review";

type BarProps = Parameters<typeof ReviewCommandBar>[0];

function renderBar(overrides: Partial<BarProps> = {}) {
  const props: BarProps = {
    mapName: "de_inferno",
    selectedRound: 2,
    currentTick: 1280,
    roundTime: "01:15",
    currentPovName: "xelex",
    playing: false,
    speed: 1,
    previousFinding: null,
    nextFinding: null,
    onTogglePlay: vi.fn(),
    onSpeedChange: vi.fn(),
    onPreviousFinding: vi.fn(),
    onNextFinding: vi.fn(),
    ...overrides
  };
  const view = render(<ReviewCommandBar {...props} />);
  return { props, ...view };
}

describe("ReviewCommandBar", () => {
  it("shows the round, clock and POV readouts", () => {
    renderBar();
    const bar = screen.getByRole("region", { name: "Review transport" });
    expect(bar).toHaveTextContent("第 2 回合");
    expect(bar).toHaveTextContent("01:15");
    expect(bar).toHaveTextContent("xelex");
  });

  it("offers play while paused and pause while playing", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderBar();
    await user.click(screen.getByRole("button", { name: "Play replay" }));
    expect(props.onTogglePlay).toHaveBeenCalledTimes(1);

    rerender(<ReviewCommandBar {...props} playing />);
    expect(screen.getByRole("button", { name: "Pause replay" })).toHaveTextContent("暂停");
  });

  it("reports speed changes as numbers", async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await user.selectOptions(screen.getByRole("combobox", { name: "Playback speed" }), "2");
    expect(props.onSpeedChange).toHaveBeenCalledWith(2);
  });

  it("disables finding navigation at either end and wires it otherwise", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderBar();
    expect(screen.getByRole("button", { name: "上一条" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一条" })).toBeDisabled();

    rerender(
      <ReviewCommandBar
        {...props}
        previousFinding={coachingEvent({ id: "earlier" })}
        nextFinding={coachingEvent({ id: "later" })}
      />
    );
    await user.click(screen.getByRole("button", { name: "上一条" }));
    await user.click(screen.getByRole("button", { name: "下一条" }));
    expect(props.onPreviousFinding).toHaveBeenCalledTimes(1);
    expect(props.onNextFinding).toHaveBeenCalledTimes(1);
  });
});
