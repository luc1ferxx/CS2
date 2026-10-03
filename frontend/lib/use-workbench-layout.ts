import { useEffect, useState } from "react";

// The demo page's one-screen review workbench (S15): wide AND tall enough for the stage, the dock and
// the right column to fit the screen. The same query gates the "S15 WORKBENCH" rules in product.css.
export const WORKBENCH_QUERY = "(min-width: 1280px) and (min-height: 700px)";

/**
 * True while the viewport fits the review workbench. It starts false (the server and the first client
 * render agree: the stacked layout) and follows the media query from mount on; it changes only when a
 * resize crosses the threshold, never per frame.
 */
export function useWorkbenchLayout(): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(WORKBENCH_QUERY);
    const update = () => setMatches(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return matches;
}
