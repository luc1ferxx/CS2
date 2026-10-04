import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UtilityLayer } from "@/components/replay/UtilityLayer";
import { getTacticalMapPresentation } from "@/lib/map-config";
import { V2_ALPHA, V2_CHARLIE, replayV1, replayV2 } from "@/lib/test-fixtures/replay-v2";
import { utilityRadiusPercent } from "@/lib/utility";
import type { ReplayData, ReplayUtility } from "@/types/replay";

const flash: ReplayUtility = {
  id: "utility-flash-302-480", type: "flash", throwerId: V2_CHARLIE, throwerName: "Charlie", throwerSide: "CT",
  roundNumber: 1, throwTick: 480, detonateTick: 500, endTick: 500,
  points: [{ tick: 480, x: 70, y: 70, z: -160 }, { tick: 500, x: 65, y: 66, z: -160 }]
};
const molotov: ReplayUtility = {
  id: "utility-molotov-303-600", type: "molotov", throwerId: V2_CHARLIE, throwerName: "Charlie", throwerSide: "CT",
  roundNumber: 1, throwTick: 600, detonateTick: 640, endTick: 1088,
  points: [{ tick: 600, x: 60, y: 60 }, { tick: 640, x: 50, y: 55 }]
};

function replay(extra: ReplayUtility[] = []): ReplayData {
  const base = replayV2();
  return { ...base, utility: [...(base.utility ?? []), ...extra] };
}

function draw(data: ReplayData, tick: number, options: { floor?: "upper" | "lower" | null; focus?: string | null; mapName?: string } = {}) {
  const map = getTacticalMapPresentation({ mapName: options.mapName ?? data.mapName });
  return render(
    <svg viewBox="0 0 100 100">
      <UtilityLayer replay={data} currentTick={tick} map={map} floor={options.floor ?? null}
        focusPlayerId={options.focus ?? null} />
    </svg>
  );
}

describe("UtilityLayer", () => {
  it("draws a grenade in flight with a trail in the thrower's side colour", () => {
    const { container } = draw(replay(), 360);
    const flight = container.querySelector('[data-phase="flight"]');
    expect(flight).toHaveAttribute("data-utility-id", "utility-smoke-301-300");
    expect(flight).toHaveClass("utility-flight", "utility-smoke", "side-t");
    expect(flight?.querySelectorAll(".utility-trail").length).toBeGreaterThan(0);
    // The icon sits at the grenade's position and is drawn at a fixed pixel size.
    expect(flight?.querySelector(".utility-flight-body")?.parentElement?.getAttribute("transform")).toMatch(/^translate\(35 [\d.]+\) scale\(/);
    expect(flight?.querySelector(".utility-flight-body")).toHaveAttribute("r", "6");
  });

  it("holds the smoke at its landing point with the map-scaled radius until the next round starts", () => {
    const data = replay();
    const mirage = getTacticalMapPresentation({ mapName: "de_mirage" });
    const { container, unmount } = draw(data, 700);
    const smoke = container.querySelector('[data-utility-id="utility-smoke-301-300"]');
    expect(smoke).toHaveAttribute("data-phase", "effect");
    expect(smoke).toHaveAttribute("transform", "translate(40 42)");
    expect(smoke?.querySelector(".utility-smoke-cloud")).toHaveAttribute("r", String(utilityRadiusPercent("smoke", mirage)));
    // Its kind's glyph in the middle, so it never reads as a player dot.
    expect(smoke?.querySelector(".utility-effect-glyph")).toHaveClass("lucide-cloud");
    unmount();
    // The stored expiry (1532) is in round 2; a new round clears the map.
    expect(draw(data, 1000).container.querySelector('[data-utility-id="utility-smoke-301-300"]')).toBeNull();
  });

  it("shows a flash as a brief burst and fire for its whole window", () => {
    const data = replay([flash, molotov]);
    expect(draw(data, 505).container.querySelector('[data-utility-id="utility-flash-302-480"] .utility-flash-burst')).not.toBeNull();
    // 0.3 s at 64 ticks is over by tick 520.
    expect(draw(data, 520).container.querySelector('[data-utility-id="utility-flash-302-480"]')).toBeNull();
    const fire = draw(data, 800).container.querySelector('[data-utility-id="utility-molotov-303-600"]');
    expect(fire?.querySelector(".utility-fire-area")).not.toBeNull();
    expect(fire?.querySelector(".utility-effect-glyph")).toHaveClass("lucide-flame");
    expect(draw(data, 890).container.querySelector('[data-utility-id="utility-molotov-303-600"]')).not.toBeNull();
  });

  it("dims throws on the other floor of a two-floor map and other players' throws while focused", () => {
    const lower: ReplayUtility = {
      ...molotov, id: "utility-molotov-304-600", points: molotov.points.map((point) => ({ ...point, z: -700 }))
    };
    const data = replay([lower]);
    const nuke = draw(data, 700, { mapName: "de_nuke", floor: "upper" }).container;
    expect(nuke.querySelector('[data-utility-id="utility-molotov-304-600"]')?.parentElement).toHaveClass("other-floor");
    expect(nuke.querySelector('[data-utility-id="utility-smoke-301-300"]')?.parentElement).not.toHaveClass("other-floor");

    const focused = draw(data, 700, { focus: V2_ALPHA }).container;
    expect(focused.querySelector('[data-utility-id="utility-molotov-304-600"]')?.parentElement).toHaveClass("focus-dimmed");
    expect(focused.querySelector('[data-utility-id="utility-smoke-301-300"]')?.parentElement).not.toHaveClass("focus-dimmed");
  });

  it("lets a grenade in flight be clicked for its analysis, but never a decoy or an effect", () => {
    const decoy: ReplayUtility = {
      id: "utility-decoy-305-320", type: "decoy", throwerId: V2_CHARLIE, throwerName: "Charlie", throwerSide: "CT",
      roundNumber: 1, throwTick: 320, detonateTick: 400, endTick: 900,
      points: [{ tick: 320, x: 60, y: 60 }, { tick: 400, x: 55, y: 50 }]
    };
    const data = replay([decoy, molotov]);
    const map = getTacticalMapPresentation({ mapName: data.mapName });
    const onSelect = vi.fn();
    const { container } = render(
      <svg viewBox="0 0 100 100">
        <UtilityLayer replay={data} currentTick={360} map={map} floor={null} onSelect={onSelect} unitsPerPixel={0.2} />
      </svg>
    );
    // The layer itself stays click-through; only the smoke's marker takes clicks.
    expect(container.querySelector('[data-testid="utility-layer"]')).toHaveAttribute("pointer-events", "none");
    const markers = container.querySelectorAll(".utility-flight-select");
    expect(markers).toHaveLength(1);
    const marker = markers[0];
    expect(marker.closest("[data-utility-id]")).toHaveAttribute("data-utility-id", "utility-smoke-301-300");
    expect(marker).toHaveAttribute("pointer-events", "auto");
    expect(marker.querySelector("title")).toHaveTextContent("分析这颗道具");
    // A 24 px target in screen pixels (the marker group is scaled back to radar percent).
    expect(marker.querySelector(".utility-flight-hit")).toHaveAttribute("r", "12");
    fireEvent.click(marker.querySelector(".utility-flight-hit") as Element);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "utility-smoke-301-300" }));
    expect(container.querySelector('[data-utility-id="utility-decoy-305-320"] .utility-flight-hit')).toBeNull();

    // Once it has burst the smoke is an effect again: nothing to click, the dots under it keep theirs.
    const later = render(
      <svg viewBox="0 0 100 100">
        <UtilityLayer replay={data} currentTick={700} map={map} floor={null} onSelect={onSelect} />
      </svg>
    ).container;
    expect(later.querySelector('[data-utility-id="utility-smoke-301-300"]')).toHaveAttribute("data-phase", "effect");
    expect(later.querySelector(".utility-flight-select, .utility-flight-hit, [pointer-events='auto']")).toBeNull();
  });

  it("keeps grenades in flight click-through without a select handler", () => {
    const { container } = draw(replay(), 360);
    expect(container.querySelector('[data-phase="flight"]')).not.toBeNull();
    expect(container.querySelector(".utility-flight-select, .utility-flight-hit, title")).toBeNull();
  });

  it("draws nothing for a v1 replay or a quiet moment", () => {
    expect(draw(replayV1(), 400).container.querySelector('[data-testid="utility-layer"]')).toBeNull();
    expect(draw(replay(), 200).container.querySelector('[data-testid="utility-layer"]')).toBeNull();
  });
});
