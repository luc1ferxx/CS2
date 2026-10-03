"use client";

import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

// The page component is also rendered on the server (its skeleton), where a layout effect only warns.
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * One selection marker for a row of toggle buttons that slides to the pressed one
 * (`aria-pressed="true"`) instead of the old one switching off. The marker is an
 * element inside the group (the group is its positioned parent); its `transform`
 * and `width` are written straight onto its style, so a move never re-renders.
 *
 * `selectedKey` changing moves it with the CSS transition. Everything else places it
 * with no animation: the first paint, `layoutKey` changing (a label whose width
 * changed, a button added or removed), window resizes and the web font arriving.
 * Measures only when one of those happens, never on every render: the demo page
 * re-renders once per playback frame.
 */
export function useSlidingIndicator<Group extends HTMLElement = HTMLDivElement>(selectedKey: string, layoutKey = "") {
  const groupRef = useRef<Group | null>(null);
  const indicatorRef = useRef<HTMLSpanElement | null>(null);
  const placedKey = useRef<string | null>(null);

  const place = useCallback((wantAnimation: boolean) => {
    const group = groupRef.current;
    const indicator = indicatorRef.current;
    if (!group || !indicator) return;
    const pressed = group.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!pressed) {
      indicator.style.visibility = "hidden";
      return;
    }
    // A marker coming back from hidden appears in place; it never grows out of nothing.
    const animate = wantAnimation && indicator.style.visibility !== "hidden" && indicator.style.width !== "";
    if (!animate) indicator.style.transition = "none";
    indicator.style.visibility = "";
    indicator.style.width = `${pressed.offsetWidth}px`;
    indicator.style.transform = `translateX(${pressed.offsetLeft}px)`;
    if (!animate) {
      // Commit the jump before the transition comes back, so it does not run from the old place.
      void indicator.offsetWidth;
      indicator.style.transition = "";
    }
  }, []);

  useIsomorphicLayoutEffect(() => {
    const animate = placedKey.current !== null && placedKey.current !== selectedKey;
    placedKey.current = selectedKey;
    place(animate);
  }, [layoutKey, place, selectedKey]);

  // A group that mounts later than the hook (the demo page shows its tabs once the replay is in)
  // is placed as it attaches. Its children's refs are attached first, so the marker is there.
  const attachGroup = useCallback((element: Group | null) => {
    groupRef.current = element;
    if (element) place(false);
  }, [place]);

  useEffect(() => {
    let active = true;
    const placeNow = () => place(false);
    window.addEventListener("resize", placeNow);
    // The techno face swaps in after the first paint and changes every label's width.
    document.fonts?.ready.then(() => { if (active) placeNow(); }, () => undefined);
    return () => {
      active = false;
      window.removeEventListener("resize", placeNow);
    };
  }, [place]);

  return { groupRef: attachGroup, indicatorRef };
}
