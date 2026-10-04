import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ThrowAnalysisPanel, type ThrowAnalysisPanelProps } from "@/components/replay/ThrowAnalysisPanel";
import type { ThrowAnalysis } from "@/lib/throw-analysis";
import type { ReplayUtility } from "@/types/replay";

// xertioN's round-10 smoke (the spec's fixture): released at 77301 holding A, window 77237..77365.
const SMOKE: ReplayUtility = {
  id: "utility-smoke-412-77300", type: "smoke", throwerId: "76561198000000101", throwerName: "xertioN", throwerSide: "T",
  roundNumber: 10, throwTick: 77300, detonateTick: 77420, endTick: 78572,
  points: [{ tick: 77300, x: 93.3, y: 39.2 }, { tick: 77420, x: 60, y: 52 }]
};
const COMMAND = "setpos 1376.2 -119.3 -129.3; setang -20.33 163.28 0";

function analysis(overrides: Partial<ThrowAnalysis> = {}): ThrowAnalysis {
  return {
    utilityId: SMOKE.id, throwerId: SMOKE.throwerId, releaseTick: 77301, windowStart: 77237, windowEnd: 77365,
    releaseMask: 513, style: "走投", button: "left", moving: true, speed: 120, heldMoveKeys: ["A"],
    command: COMMAND, hint: "边走边扔（按着 A）：站在出手点不动扔，落点会偏",
    throwerPath: [], bounds: { x: 52.69, y: 23.11, size: 46.4 },
    ...overrides
  };
}

function props(overrides: Partial<ThrowAnalysisPanelProps> = {}): ThrowAnalysisPanelProps {
  return {
    utility: SMOKE, analysis: analysis(), throwerName: "xertioN", tickRate: 64, tick: 77300, playing: false, speed: 0.25,
    zoomed: true, keyMask: 513,
    onTogglePlay: vi.fn(), onSpeedChange: vi.fn(), onSeek: vi.fn(), onZoomChange: vi.fn(), onClose: vi.fn(), onWatchInReplay: vi.fn(),
    ...overrides
  };
}

function slider(): HTMLElement {
  return screen.getByRole("slider", { name: "出手前后时间" });
}

function setClipboard(writeText: ((text: string) => Promise<void>) | undefined) {
  Object.defineProperty(navigator, "clipboard", {
    value: writeText ? { writeText } : undefined,
    configurable: true
  });
}

// jsdom has no PointerEvent; without one the pointer coordinates never reach the handlers.
beforeAll(() => {
  if (typeof window.PointerEvent === "function") return;
  class PointerEventStub extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  Object.defineProperty(window, "PointerEvent", { configurable: true, writable: true, value: PointerEventStub });
});

afterEach(() => {
  vi.useRealTimers();
  setClipboard(undefined);
});

describe("ThrowAnalysisPanel", () => {
  it("shows the thrower, the grenade, the zoom toggle and the throw tags", () => {
    const onClose = vi.fn();
    const onZoomChange = vi.fn();
    render(<ThrowAnalysisPanel {...props({ onClose, onZoomChange })} />);
    const panel = screen.getByRole("complementary", { name: "道具投掷分析" });
    expect(panel).toHaveClass("throw-analysis-panel");
    expect(panel.querySelector(".throw-analysis-avatar")).toHaveTextContent("X");
    expect(panel.querySelector(".throw-analysis-avatar")).toHaveClass("side-t");
    expect(screen.getByText("xertioN", { selector: ".throw-analysis-name" })).toBeInTheDocument();
    expect(screen.getByText("烟雾弹")).toBeInTheDocument();
    expect([...panel.querySelectorAll(".throw-analysis-tag")].map((tag) => tag.textContent)).toEqual(["走投", "左键扔"]);

    expect(screen.getByRole("button", { name: "放大" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "全图" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "全图" }));
    expect(onZoomChange).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(onZoomChange).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "关闭分析" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("hides the tags it cannot know and puts a CT thrower's initial on the CT colour", () => {
    render(<ThrowAnalysisPanel {...props({
      utility: { ...SMOKE, type: "flash", throwerSide: "CT" },
      throwerName: "  ñiko",
      analysis: analysis({ style: null, button: null, releaseMask: null })
    })} />);
    expect(document.querySelectorAll(".throw-analysis-tag")).toHaveLength(0);
    expect(document.querySelector(".throw-analysis-avatar")).toHaveTextContent("Ñ");
    expect(document.querySelector(".throw-analysis-avatar")).toHaveClass("side-ct");
    expect(screen.getByText("闪光弹")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放大" })).toHaveAttribute("aria-pressed", "true");
  });

  it("embeds the key panel at the playback tick, or says the match has no key data", () => {
    const { rerender } = render(<ThrowAnalysisPanel {...props()} />);
    // One key panel, without a second card.
    const overlays = document.querySelectorAll(".keyboard-overlay");
    expect(overlays).toHaveLength(1);
    expect(overlays[0]).toHaveClass("embedded");
    expect([...document.querySelectorAll(".keyboard-overlay .key.active")].map((key) => key.textContent)).toEqual(["A"]);
    expect(document.querySelector(".keyboard-overlay .mouse-button.left")).toHaveClass("active");
    expect(overlays[0]).toHaveAttribute("aria-label", "xertioN 正在按：A、左键");

    rerender(<ThrowAnalysisPanel {...props({ keyMask: 512 })} />);
    expect(document.querySelector(".keyboard-overlay .mouse-button.left")).not.toHaveClass("active");

    rerender(<ThrowAnalysisPanel {...props({ keyMask: null })} />);
    expect(document.querySelector(".keyboard-overlay")).toBeNull();
    expect(screen.getByText("这场比赛没有按键记录")).toBeInTheDocument();
  });

  it("drives the slow-motion clock: play / pause, the speeds and the time from the release", () => {
    const onTogglePlay = vi.fn();
    const onSpeedChange = vi.fn();
    const { rerender } = render(<ThrowAnalysisPanel {...props({ onTogglePlay, onSpeedChange })} />);
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(onTogglePlay).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "0.25x" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "1x" }));
    expect(onSpeedChange).toHaveBeenLastCalledWith(1);
    fireEvent.click(screen.getByRole("button", { name: "0.5x" }));
    expect(onSpeedChange).toHaveBeenLastCalledWith(0.5);
    // Opens one tick before the release: -0.02 s.
    expect(document.querySelector(".throw-analysis-time")).toHaveTextContent("-0.02s");

    rerender(<ThrowAnalysisPanel {...props({ onTogglePlay, onSpeedChange, playing: true, speed: 0.5, tick: 77333 })} />);
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0.5x" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".throw-analysis-time")).toHaveTextContent("+0.50s");
  });

  it("draws the track with the release marker, the fill and the handle, and labels the window", () => {
    const { rerender } = render(<ThrowAnalysisPanel {...props({ tick: 77269 })} />);
    const track = slider();
    expect(track).toHaveAttribute("aria-valuemin", "77237");
    expect(track).toHaveAttribute("aria-valuemax", "77365");
    expect(track).toHaveAttribute("aria-valuenow", "77269");
    expect(track).toHaveAttribute("aria-valuetext", "-0.50s");
    expect(track.querySelector<HTMLElement>(".throw-analysis-release")?.style.left).toBe("50%");
    expect(track.querySelector<HTMLElement>(".throw-analysis-fill")?.style.width).toBe("25%");
    expect(track.querySelector<HTMLElement>(".throw-analysis-handle")?.style.left).toBe("25%");
    expect([...document.querySelectorAll(".throw-analysis-scale > span")].map((span) => span.textContent))
      .toEqual(["-1.0s", "出手", "+1.0s"]);

    // A window cut short by the round start: the labels and the marker follow it.
    rerender(<ThrowAnalysisPanel {...props({ tick: 77301.6, analysis: analysis({ windowStart: 77269 }) })} />);
    expect(slider()).toHaveAttribute("aria-valuenow", "77302");
    expect(slider().querySelector<HTMLElement>(".throw-analysis-release")?.style.left).toBe("33.33%");
    expect([...document.querySelectorAll(".throw-analysis-scale > span")].map((span) => span.textContent))
      .toEqual(["-0.5s", "出手", "+1.0s"]);
  });

  it("seeks from the track by keyboard (one tick per arrow) and by pointer", () => {
    const onSeek = vi.fn();
    render(<ThrowAnalysisPanel {...props({ onSeek, tick: 77300.4 })} />);
    const track = slider();
    fireEvent.keyDown(track, { key: "ArrowRight" });
    expect(onSeek).toHaveBeenLastCalledWith(77301);
    fireEvent.keyDown(track, { key: "ArrowLeft" });
    expect(onSeek).toHaveBeenLastCalledWith(77299);
    fireEvent.keyDown(track, { key: "Home" });
    expect(onSeek).toHaveBeenLastCalledWith(77237);
    fireEvent.keyDown(track, { key: "End" });
    expect(onSeek).toHaveBeenLastCalledWith(77365);
    onSeek.mockClear();
    fireEvent.keyDown(track, { key: "a" });
    expect(onSeek).not.toHaveBeenCalled();

    track.getBoundingClientRect = () => ({ left: 100, width: 256, top: 0, height: 6, right: 356, bottom: 6, x: 100, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(track, { pointerId: 1, button: 0, clientX: 164 });
    expect(onSeek).toHaveBeenLastCalledWith(77269);
    fireEvent.pointerMove(track, { pointerId: 1, clientX: 356 });
    expect(onSeek).toHaveBeenLastCalledWith(77365);
    fireEvent.pointerMove(track, { pointerId: 1, clientX: 0 });
    expect(onSeek).toHaveBeenLastCalledWith(77237);
    fireEvent.pointerUp(track, { pointerId: 1, clientX: 0 });
    onSeek.mockClear();
    fireEvent.pointerMove(track, { pointerId: 1, clientX: 200 });
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("copies the position command and says so for 1.5 s", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    render(<ThrowAnalysisPanel {...props()} />);
    expect(screen.getByText(COMMAND, { selector: "code" })).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "复制站位指令" }));
    });
    expect(writeText).toHaveBeenCalledWith(COMMAND);
    expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByRole("button", { name: "复制站位指令" })).toBeInTheDocument();
  });

  it("says when copying failed, and without a clipboard", async () => {
    setClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    const { unmount } = render(<ThrowAnalysisPanel {...props()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "复制站位指令" }));
    });
    expect(screen.getByRole("button", { name: "复制失败" })).toBeInTheDocument();
    unmount();

    setClipboard(undefined);
    render(<ThrowAnalysisPanel {...props()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "复制站位指令" }));
    });
    expect(screen.getByRole("button", { name: "复制失败" })).toBeInTheDocument();
  });

  it("opens another throw on the plain copy label", async () => {
    setClipboard(vi.fn().mockResolvedValue(undefined));
    const { rerender } = render(<ThrowAnalysisPanel {...props()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "复制站位指令" }));
    });
    expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
    rerender(<ThrowAnalysisPanel {...props({ analysis: analysis({ utilityId: "utility-smoke-9-1" }) })} />);
    expect(screen.getByRole("button", { name: "复制站位指令" })).toBeInTheDocument();
  });

  it("without a release pose, says the position data is still coming and offers no copy", () => {
    render(<ThrowAnalysisPanel {...props({ analysis: analysis({ command: null, hint: null }) })} />);
    expect(screen.getByText("这场比赛还在补充站位数据")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制站位指令" })).toBeNull();
    expect(document.querySelector(".throw-analysis-hint")).toBeNull();
  });

  it("shows the hint and hands 在战术回放里看 to the page", () => {
    const onWatchInReplay = vi.fn();
    render(<ThrowAnalysisPanel {...props({ onWatchInReplay })} />);
    expect(screen.getByText("边走边扔（按着 A）：站在出手点不动扔，落点会偏")).toHaveClass("throw-analysis-hint");
    fireEvent.click(screen.getByRole("button", { name: "在战术回放里看" }));
    expect(onWatchInReplay).toHaveBeenCalledTimes(1);
  });
});
