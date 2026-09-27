import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePoll } from "@/lib/use-poll";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("usePoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("runs the task on every interval and stops when the delay is cleared", async () => {
    const task = vi.fn(async () => {});
    const { rerender } = renderHook(({ delay }) => usePoll(task, delay), { initialProps: { delay: 1000 as number | null } });

    expect(task).not.toHaveBeenCalled();
    await advance(1000);
    expect(task).toHaveBeenCalledTimes(1);
    await advance(1000);
    expect(task).toHaveBeenCalledTimes(2);

    rerender({ delay: null });
    await advance(5000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("waits for a slow run to settle before scheduling the next", async () => {
    let finish!: () => void;
    const task = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    renderHook(() => usePoll(task, 1000));

    await advance(1000);
    await advance(5000);
    expect(task).toHaveBeenCalledTimes(1);

    await act(async () => finish());
    await advance(1000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("pauses while the tab is hidden and refreshes as soon as it is visible again", async () => {
    const task = vi.fn(async () => {});
    renderHook(() => usePoll(task, 1000));

    await act(async () => setHidden(true));
    await advance(10_000);
    expect(task).not.toHaveBeenCalled();

    await act(async () => setHidden(false));
    expect(task).toHaveBeenCalledTimes(1);
    await advance(1000);
    expect(task).toHaveBeenCalledTimes(2);

    // Hiding again pauses again: the pending visible tick is dropped.
    await act(async () => setHidden(true));
    await advance(10_000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("keeps polling at the slower delay while hidden when asked, and refreshes at once on return", async () => {
    const task = vi.fn(async () => {});
    renderHook(() => usePoll(task, 1000, { hiddenDelayMs: 5000 }));

    await advance(1000);
    expect(task).toHaveBeenCalledTimes(1);

    // The hidden cadence starts from the moment the tab is hidden, not after one more visible tick.
    await act(async () => setHidden(true));
    await advance(4999);
    expect(task).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(task).toHaveBeenCalledTimes(2);
    await advance(5000);
    expect(task).toHaveBeenCalledTimes(3);

    await act(async () => setHidden(false));
    expect(task).toHaveBeenCalledTimes(4);
    await advance(1000);
    expect(task).toHaveBeenCalledTimes(5);
  });

  it("starts at the hidden delay when mounted in a hidden tab", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    const paused = vi.fn(async () => {});
    const slow = vi.fn(async () => {});
    renderHook(() => usePoll(paused, 1000));
    renderHook(() => usePoll(slow, 1000, { hiddenDelayMs: 5000 }));

    await advance(4999);
    expect(slow).not.toHaveBeenCalled();
    await advance(1);
    expect(slow).toHaveBeenCalledTimes(1);
    await advance(20_000);
    expect(paused).not.toHaveBeenCalled();
  });

  it("never overlaps a slow run while hidden or on return", async () => {
    let finish!: () => void;
    const task = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    renderHook(() => usePoll(task, 1000, { hiddenDelayMs: 5000 }));

    await act(async () => setHidden(true));
    await advance(5000);
    expect(task).toHaveBeenCalledTimes(1);
    await advance(20_000);
    await act(async () => setHidden(false));
    expect(task).toHaveBeenCalledTimes(1);

    // Settling back in view schedules the next run at the visible delay.
    await act(async () => finish());
    await advance(999);
    expect(task).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("always calls the latest task", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ task }) => usePoll(task, 1000), { initialProps: { task: first } });

    rerender({ task: second });
    await advance(1000);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
