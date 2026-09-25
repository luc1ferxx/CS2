import { useEffect, useRef } from "react";

function pageHidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

/**
 * Runs `task` every `delayMs` (null stops it), waiting for each run to settle
 * before scheduling the next so slow responses never overlap. Ticks are skipped
 * while the tab is hidden; coming back runs the task at once instead of waiting
 * out the interval, so anything that finished in the background shows up on return.
 * The latest `task` is always the one called, so callers need not memoize it.
 */
export function usePoll(task: () => unknown, delayMs: number | null): void {
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
      if (!cancelled) {
        timeoutId = window.setTimeout(run, delayMs);
      }
    };
    async function run() {
      if (cancelled || running || pageHidden()) {
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
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [delayMs]);
}
