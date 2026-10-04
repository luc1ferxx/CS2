"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ThrowAnalysis } from "@/lib/throw-analysis";

export type ThrowPlaybackSpeed = 0.25 | 0.5 | 1;

export interface ThrowPlayback {
  /** Fractional while playing; floor it for inputMaskAt. */
  tick: number;
  playing: boolean;
  speed: ThrowPlaybackSpeed;
  togglePlay(): void;
  setSpeed(speed: ThrowPlaybackSpeed): void;
  seek(tick: number): void;
}

const DEFAULT_SPEED: ThrowPlaybackSpeed = 0.25;
// A frame longer than this (a background tab, a debugger pause) advances only this much.
const MAX_FRAME_SECONDS = 0.1;

// Opens paused one tick before the release: the keys at release with the mouse button still down.
function startTick(analysis: ThrowAnalysis | null): number {
  if (!analysis) return 0;
  return Math.min(analysis.windowEnd, Math.max(analysis.windowStart, analysis.releaseTick - 1));
}

/**
 * The 道具投掷分析 slow-motion clock: local to the finder (never page state, so the page does not
 * re-render per frame). Resets to its opening state whenever another throw is analysed; plays at
 * 0.25x by default, stops at the window's end, and plays again from the window's start.
 */
export function useThrowPlayback(analysis: ThrowAnalysis | null, tickRate: number): ThrowPlayback {
  const rate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  const utilityId = analysis?.utilityId ?? null;
  const windowStart = analysis?.windowStart ?? 0;
  const windowEnd = analysis?.windowEnd ?? 0;
  const hasAnalysis = analysis !== null;

  const [shownId, setShownId] = useState<string | null>(utilityId);
  const [tick, setTick] = useState(() => startTick(analysis));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<ThrowPlaybackSpeed>(DEFAULT_SPEED);
  // The loop and the handlers read the current tick from here, so they never restart per frame.
  const tickRef = useRef(tick);

  // Another throw: reset during render, so its first paint already shows its own opening tick.
  let currentTick = tick;
  let currentPlaying = playing;
  let currentSpeed = speed;
  if (shownId !== utilityId) {
    currentTick = startTick(analysis);
    currentPlaying = false;
    currentSpeed = DEFAULT_SPEED;
    setShownId(utilityId);
    setTick(currentTick);
    setPlaying(false);
    setSpeedState(DEFAULT_SPEED);
  }

  useEffect(() => {
    tickRef.current = tick;
  }, [tick]);

  useEffect(() => {
    if (!playing || !hasAnalysis || typeof requestAnimationFrame !== "function") return;
    let frame = 0;
    let last: number | null = null;
    const step = (now: number) => {
      const seconds = last === null ? 0 : Math.min(MAX_FRAME_SECONDS, Math.max(0, (now - last) / 1000));
      last = now;
      const next = Math.min(windowEnd, tickRef.current + seconds * rate * speed);
      tickRef.current = next;
      setTick(next);
      if (next >= windowEnd) {
        setPlaying(false);
        return;
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [hasAnalysis, playing, rate, speed, windowEnd]);

  const togglePlay = useCallback(() => {
    if (!hasAnalysis) return;
    if (playing) {
      setPlaying(false);
      return;
    }
    if (tickRef.current >= windowEnd) {
      tickRef.current = windowStart;
      setTick(windowStart);
    }
    setPlaying(true);
  }, [hasAnalysis, playing, windowEnd, windowStart]);

  const seek = useCallback((value: number) => {
    if (!hasAnalysis || !Number.isFinite(value)) return;
    const next = Math.min(windowEnd, Math.max(windowStart, value));
    tickRef.current = next;
    setTick(next);
  }, [hasAnalysis, windowEnd, windowStart]);

  const setSpeed = useCallback((value: ThrowPlaybackSpeed) => setSpeedState(value), []);

  return { tick: currentTick, playing: currentPlaying, speed: currentSpeed, togglePlay, setSpeed, seek };
}
