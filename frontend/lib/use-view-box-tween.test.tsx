import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FULL_VIEW_BOX, type ThrowViewBox } from "@/lib/throw-analysis";
import { useViewBoxTween } from "@/lib/use-view-box-tween";

const ZOOMED: ThrowViewBox = { x: 52.69, y: 23.11, size: 46.4 };

function frames(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function parts(viewBox: string): number[] {
  return viewBox.split(" ").map(Number);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "setTimeout", "clearTimeout", "Date"] });
});

const setupMatchMedia = window.matchMedia;

afterEach(() => {
  vi.useRealTimers();
  window.matchMedia = setupMatchMedia;
});

describe("useViewBoxTween", () => {
  it("applies the first box at once", () => {
    const { result } = renderHook(() => useViewBoxTween(ZOOMED));
    expect(result.current).toBe("52.69 23.11 46.4 46.4");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("eases to a new box in about 200 ms, out of the gate fastest", () => {
    const { result, rerender } = renderHook(({ box }) => useViewBoxTween(box), { initialProps: { box: FULL_VIEW_BOX } });
    expect(result.current).toBe("0 0 100 100");
    rerender({ box: ZOOMED });
    frames(16 + 100);
    const [x, , size] = parts(result.current);
    // Half the time, well past half the way (ease-out).
    expect(x).toBeGreaterThan(52.69 * 0.6);
    expect(x).toBeLessThan(52.69);
    expect(size).toBeLessThan(100);
    expect(size).toBeGreaterThan(46.4);
    frames(150);
    expect(result.current).toBe("52.69 23.11 46.4 46.4");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("turns back from where it is when the box changes mid-way", () => {
    const { result, rerender } = renderHook(({ box }) => useViewBoxTween(box), { initialProps: { box: FULL_VIEW_BOX } });
    rerender({ box: ZOOMED });
    frames(16 + 64);
    const midway = parts(result.current);
    rerender({ box: FULL_VIEW_BOX });
    frames(16);
    // The first frame of the new tween starts at the box on screen, not at the old target.
    expect(parts(result.current)).toEqual(midway);
    frames(250);
    expect(result.current).toBe("0 0 100 100");
  });

  it("ignores a new object with the same values", () => {
    const { result, rerender } = renderHook(({ box }) => useViewBoxTween(box), { initialProps: { box: ZOOMED } });
    rerender({ box: { ...ZOOMED } });
    expect(vi.getTimerCount()).toBe(0);
    expect(result.current).toBe("52.69 23.11 46.4 46.4");
  });

  it("jumps straight to the new box under prefers-reduced-motion", () => {
    window.matchMedia = (query: string): MediaQueryList => ({
      matches: query === "(prefers-reduced-motion: reduce)", media: query, onchange: null,
      addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false
    });
    const { result, rerender } = renderHook(({ box }) => useViewBoxTween(box), { initialProps: { box: FULL_VIEW_BOX } });
    rerender({ box: ZOOMED });
    expect(result.current).toBe("52.69 23.11 46.4 46.4");
    expect(vi.getTimerCount()).toBe(0);
  });
});
