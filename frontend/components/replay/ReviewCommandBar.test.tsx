import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewCommandBar } from "@/components/replay/ReviewCommandBar";
import { coachingEvent } from "@/lib/test-fixtures/review";

type BarProps = Parameters<typeof ReviewCommandBar>[0];

function renderBar(overrides: Partial<BarProps> = {}) {
  const props: BarProps = {
    selectedRound: 2,
    roundTime: "1:15",
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
    const bar = screen.getByRole("region", { name: "播放控制" });
    expect(bar).toHaveTextContent("第 2 回合");
    expect(bar).toHaveTextContent("1:15");
    expect(bar).toHaveTextContent("xelex");
    expect(bar).not.toHaveAttribute("title");
  });

  it("names the play button by its visible label", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderBar();
    await user.click(screen.getByRole("button", { name: "播放" }));
    expect(props.onTogglePlay).toHaveBeenCalledTimes(1);

    rerender(<ReviewCommandBar {...props} playing />);
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
  });

  it("reports speed changes as numbers", async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await user.selectOptions(screen.getByRole("combobox", { name: "播放倍速" }), "2");
    expect(props.onSpeedChange).toHaveBeenCalledWith(2);
  });

  it("disables finding navigation at either end and wires it otherwise", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderBar();
    expect(screen.getByRole("group", { name: "建议导航" })).toBeInTheDocument();
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

  it("offers the next round in place of play at the end of a round", async () => {
    const user = userEvent.setup();
    const onNextRound = vi.fn();
    renderBar({ nextRoundNumber: 3, onNextRound });
    expect(screen.queryByRole("button", { name: "播放" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一回合" }));
    expect(onNextRound).toHaveBeenCalledTimes(1);
  });

  it("rewinds and skips five seconds, and lists the keyboard shortcuts on request", async () => {
    const user = userEvent.setup();
    const onSeekBy = vi.fn();
    const onToggleShortcuts = vi.fn();
    const { props, rerender } = renderBar({ onSeekBy, onToggleShortcuts });
    await user.click(screen.getByRole("button", { name: "后退 5 秒" }));
    await user.click(screen.getByRole("button", { name: "前进 5 秒" }));
    expect(onSeekBy.mock.calls).toEqual([[-5], [5]]);

    const toggle = screen.getByRole("button", { name: "快捷键" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(onToggleShortcuts).toHaveBeenCalledTimes(1);
    rerender(<ReviewCommandBar {...props} onSeekBy={onSeekBy} onToggleShortcuts={onToggleShortcuts} shortcutsOpen />);
    expect(screen.getByRole("button", { name: "快捷键" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("空格 / K")).toBeInTheDocument();
    expect(screen.getByText("上一回合 / 下一回合")).toBeInTheDocument();
  });
});
