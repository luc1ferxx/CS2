"use client";

import { useMemo, useRef } from "react";

import { activeCoachingEventIds, sameIdSet } from "@/lib/coaching-review";
import type { CoachingEvent } from "@/types/coaching";

/**
 * The suggestions "at" the current tick, as a Set whose identity only changes
 * when its membership does, so a memoized CoachingPanel skips playback frames.
 */
export function useActiveCoachingEventIds(events: CoachingEvent[], currentTick: number): ReadonlySet<string> {
  const previous = useRef<ReadonlySet<string>>(new Set());
  return useMemo(() => {
    const next = activeCoachingEventIds(events, currentTick);
    if (sameIdSet(previous.current, next)) return previous.current;
    previous.current = next;
    return next;
  }, [currentTick, events]);
}
