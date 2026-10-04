"use client";

import { useEffect, useRef, useState } from "react";

import type { ThrowViewBox } from "@/lib/throw-analysis";

export const VIEW_BOX_TWEEN_MS = 200;

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function viewBoxOf(box: ThrowViewBox): string {
  return `${round3(box.x)} ${round3(box.y)} ${round3(box.size)} ${round3(box.size)}`;
}

/**
 * An SVG viewBox ("x y size size") that eases (≈200 ms, ease-out) from the box on screen to each new
 * box. Boxes are compared by value, an interrupted tween starts from where it is, and the first box
 * (or every box under prefers-reduced-motion) is applied at once.
 */
export function useViewBoxTween(box: ThrowViewBox): string {
  const { x, y, size } = box;
  const [shown, setShown] = useState<ThrowViewBox>(() => ({ x, y, size }));
  const shownRef = useRef(shown);

  useEffect(() => {
    const from = shownRef.current;
    const to = { x, y, size };
    if (from.x === to.x && from.y === to.y && from.size === to.size) return;
    if (prefersReducedMotion() || typeof requestAnimationFrame !== "function") {
      shownRef.current = to;
      setShown(to);
      return;
    }
    let frame = 0;
    let start: number | null = null;
    const step = (now: number) => {
      if (start === null) start = now;
      const progress = Math.min(1, Math.max(0, (now - start) / VIEW_BOX_TWEEN_MS));
      const eased = 1 - (1 - progress) ** 3;
      const next = progress >= 1 ? to : {
        x: from.x + (to.x - from.x) * eased,
        y: from.y + (to.y - from.y) * eased,
        size: from.size + (to.size - from.size) * eased
      };
      shownRef.current = next;
      setShown(next);
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [x, y, size]);

  return viewBoxOf(shown);
}
