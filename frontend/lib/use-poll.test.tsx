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
