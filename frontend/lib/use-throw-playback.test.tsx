import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ThrowAnalysis } from "@/lib/throw-analysis";
import { useThrowPlayback } from "@/lib/use-throw-playback";

function analysis(overrides: Partial<ThrowAnalysis> = {}): ThrowAnalysis {
  return {
    utilityId: "utility-smoke-412-77300", throwerId: "76561198000000101", releaseTick: 77301, windowStart: 77237, windowEnd: 77365,
    releaseMask: 513, style: "走投", button: "left", moving: true, speed: 120, heldMoveKeys: ["A"],
    command: null, hint: null, throwerPath: [], bounds: { x: 0, y: 0, size: 100 },
    ...overrides
  };
}

// One animation frame per 16 ms (the fake clock's rAF cadence).
function frames(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useThrowPlayback", () => {
  it("opens paused one tick before the release at 0.25x", () => {
    const { result } = renderHook(() => useThrowPlayback(analysis(), 64));
    expect(result.current.tick).toBe(77300);
    expect(result.current.playing).toBe(false);
    expect(result.current.speed).toBe(0.25);
    frames(500);
    expect(result.current.tick).toBe(77300);
  });

  it("advances tickRate x speed ticks per second while playing", () => {
    const { result } = renderHook(() => useThrowPlayback(analysis(), 64));
    act(() => result.current.togglePlay());
    expect(result.current.playing).toBe(true);
    // The first frame only takes its timestamp; a second at 0.25x is 16 ticks (give or take the
    // one 16 ms frame the fake clock's cadence may cut off: 0.256 ticks at 0.25x).
    frames(1016);
    expect(Math.abs(result.current.tick - 77316)).toBeLessThanOrEqual(0.26);
    act(() => result.current.togglePlay());
    expect(result.current.playing).toBe(false);
    const paused = result.current.tick;
    frames(500);
    expect(result.current.tick).toBe(paused);
    act(() => result.current.setSpeed(1));
    act(() => result.current.togglePlay());
    frames(516);
    expect(Math.abs(result.current.tick - (paused + 32))).toBeLessThanOrEqual(1.03);
  });

  it("stops at the window's end, and plays again from its start", () => {
    const { result } = renderHook(() => useThrowPlayback(analysis(), 64));
    act(() => result.current.setSpeed(1));
    act(() => result.current.togglePlay());
    frames(3000);
    expect(result.current.tick).toBe(77365);
    expect(result.current.playing).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    act(() => result.current.togglePlay());
    expect(result.current.tick).toBe(77237);
    expect(result.current.playing).toBe(true);
  });

  it("seeks inside the window only", () => {
    const { result } = renderHook(() => useThrowPlayback(analysis(), 64));
    act(() => result.current.seek(77320));
    expect(result.current.tick).toBe(77320);
    act(() => result.current.seek(1));
    expect(result.current.tick).toBe(77237);
    act(() => result.current.seek(999999));
    expect(result.current.tick).toBe(77365);
    act(() => result.current.seek(Number.NaN));
    expect(result.current.tick).toBe(77365);
  });

  it("starts over, paused at 0.25x, when another throw is analysed", () => {
    const { result, rerender } = renderHook(({ value }) => useThrowPlayback(value, 64), { initialProps: { value: analysis() } });
    act(() => result.current.setSpeed(0.5));
    act(() => result.current.togglePlay());
    frames(300);
    const other = analysis({ utilityId: "utility-flash-7-80000", releaseTick: 80010, windowStart: 79946, windowEnd: 80074 });
    rerender({ value: other });
    expect(result.current.tick).toBe(80009);
    expect(result.current.playing).toBe(false);
    expect(result.current.speed).toBe(0.25);
    frames(300);
    expect(result.current.tick).toBe(80009);
    // The same throw again (a new object): nothing resets.
    act(() => result.current.seek(80020));
    rerender({ value: { ...other } });
    expect(result.current.tick).toBe(80020);
  });

  it("does nothing without an analysis and stops its frames on unmount", () => {
    const { result, rerender, unmount } = renderHook(({ value }) => useThrowPlayback(value, 64), {
      initialProps: { value: null as ThrowAnalysis | null }
    });
    expect(result.current.tick).toBe(0);
    act(() => result.current.togglePlay());
    expect(result.current.playing).toBe(false);
    rerender({ value: analysis() });
    act(() => result.current.togglePlay());
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
