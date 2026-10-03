import { useCallback, useRef } from "react";

/**
 * A ref callback that adds `className` to the element the first time it mounts and takes it off
 * `durationMs` later: a one-shot entry animation (product.css "S14 MOTION") that never replays.
 * The class goes on in the commit, before the first paint, so nothing flashes in its final state
 * first; it is set on the DOM node, not through state, so the page does not re-render for it.
 * The guard lives with the component that calls the hook, so a remount of the element (a replay
 * reload, a child keyed on the player) does not play it again.
 */
export function useFirstEntry<T extends HTMLElement>(className = "is-entering", durationMs = 700) {
  const played = useRef(false);
  return useCallback(
    (node: T | null) => {
      if (!node || played.current) return;
      played.current = true;
      node.classList.add(className);
      window.setTimeout(() => node.classList.remove(className), durationMs);
    },
    [className, durationMs]
  );
}
