import { useEffect, useRef } from "react";

function pageHidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

export interface PollOptions {
  /**
   * Keep polling while the tab is hidden, this often. Unset, a hidden tab pauses the poll.
   * Best-effort: browsers clamp hidden-tab timers and may suspend a background tab outright.
   */
  hiddenDelayMs?: number;
}

/**
 * Runs `task` every `delayMs` (null stops it), waiting for each run to settle
 * before scheduling the next so slow responses never overlap. Ticks are skipped
 * while the tab is hidden unless `hiddenDelayMs` asks for a slower poll there;
 * coming back runs the task at once instead of waiting out the interval, so
 * anything that finished in the background shows up on return.
 * The latest `task` is always the one called, so callers need not memoize it.
 */
export function usePoll(task: () => unknown, delayMs: number | null, options?: PollOptions): void {
  const hiddenDelayMs = options?.hiddenDelayMs ?? null;
  const taskRef = useRef(task);
  useEffect(() => {
    taskRef.current = task;
  });

  useEffect(() => {
    if (delayMs === null) {
      return;
    }
    let cancelled = false;
    let running = false;
    let timeoutId: number | undefined;

    const schedule = () => {
      window.clearTimeout(timeoutId);
      const delay = pageHidden() ? hiddenDelayMs : delayMs;
      if (!cancelled && delay !== null) {
        timeoutId = window.setTimeout(run, delay);
      }
    };
    async function run() {
      if (cancelled || running || (pageHidden() && hiddenDelayMs === null)) {
        return;
      }
      running = true;
      try {
        await taskRef.current();
      } finally {
        running = false;
        schedule();
      }
    }
    const onVisibilityChange = () => {
      if (!pageHidden()) {
        window.clearTimeout(timeoutId);
        void run();
      } else if (!running) {
        // Switch to the hidden cadence (or stop) now rather than after one more visible tick.
        schedule();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [delayMs, hiddenDelayMs]);
}
