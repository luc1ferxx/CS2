import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThrowAnalysisLayer } from "@/components/replay/ThrowAnalysisLayer";
import type { ThrowAnalysis } from "@/lib/throw-analysis";
import { utilityRadiusPercent, type MapScaleSource } from "@/lib/utility";
import type { ReplayUtility } from "@/types/replay";

const MIRAGE: MapScaleSource = { transform: { type: "bounds", minX: -3400, maxX: 1720, minY: -3220, maxY: 1880 } };
const SMOKE: ReplayUtility = {
  id: "utility-smoke-412-77300", type: "smoke", throwerId: "76561198000000101", throwerName: "xertioN", throwerSide: "T",
  roundNumber: 10, throwTick: 77300, detonateTick: 77420, endTick: 78572,
  points: [{ tick: 77300, x: 93.3, y: 39.2 }, { tick: 77360, x: 70, y: 48 }, { tick: 77420, x: 60, y: 52 }]
};

function analysis(overrides: Partial<ThrowAnalysis> = {}): ThrowAnalysis {
  return {
    utilityId: SMOKE.id, throwerId: SMOKE.throwerId, releaseTick: 77301, windowStart: 77237, windowEnd: 77365,
    releaseMask: 513, style: "走投", button: "left", moving: true, speed: 120, heldMoveKeys: ["A"],
    command: null, hint: null,
    throwerPath: [{ tick: 77248, x: 95, y: 38 }, { tick: 77280, x: 94, y: 39 }, { tick: 77312, x: 93, y: 40 }],
    bounds: { x: 52.69, y: 23.11, size: 46.4 },
    ...overrides
  };
}

function draw(options: { utility?: ReplayUtility; tick?: number; scale?: number; analysis?: ThrowAnalysis; map?: MapScaleSource | null } = {}) {
  return render(
    <svg viewBox="0 0 100 100">
      <ThrowAnalysisLayer utility={options.utility ?? SMOKE} analysis={options.analysis ?? analysis()} tick={options.tick ?? 77300}
        map={options.map === undefined ? MIRAGE : options.map} scale={options.scale ?? 1} />
    </svg>
  );
}

describe("ThrowAnalysisLayer", () => {
  it("draws the flight, the landing smoke, the thrower's path and the release point, and takes no clicks", () => {
    const { container } = draw();
    const layer = container.querySelector(".throw-analysis-layer")!;
    expect(layer).toHaveAttribute("pointer-events", "none");
    expect(layer).toHaveClass("side-t");
    expect(container.querySelector(".throw-analysis-trajectory")).toHaveAttribute("points", "93.3,39.2 70,48 60,52");
    expect(container.querySelector(".throw-analysis-thrower-path")).toHaveAttribute("points", "95,38 94,39 93,40");
    expect(container.querySelector(".throw-analysis-thrower-path")).toHaveAttribute("stroke-dasharray", "1 0.7");

    const effect = container.querySelector(".throw-analysis-effect")!;
    expect(effect).toHaveClass("utility-smoke");
    expect(effect).toHaveAttribute("transform", "translate(60 52)");
    // The smoke keeps its true size on the map.
    expect(effect.querySelector(".throw-analysis-effect-area")).toHaveAttribute("r", String(utilityRadiusPercent("smoke", MIRAGE)));
    expect(effect.querySelector(".throw-analysis-effect-icon")).toHaveClass("lucide-cloud");

    expect(container.querySelector(".throw-analysis-release-dot")).toHaveAttribute("cx", "93.3");
    expect(container.querySelector(".throw-analysis-release-dot")).toHaveAttribute("r", "2.2");
    expect(container.querySelector(".throw-analysis-release-ring")).toHaveAttribute("r", "3.8");
    const labels = [...container.querySelectorAll(".throw-analysis-label")];
    expect(labels.map((label) => label.textContent)).toEqual(["出手", "落点"]);
    expect(labels[0]).toHaveAttribute("font-size", "3.4");
    // 出手 above the red ring (3.8 + 1.4 over the release point).
    expect(labels[0]).toHaveAttribute("y", "34");
  });

  it("multiplies every marker size and stroke by the zoom scale", () => {
    const { container } = draw({ scale: 0.5 });
    expect(container.querySelector(".throw-analysis-trajectory")).toHaveAttribute("stroke-width", "0.4");
    expect(container.querySelector(".throw-analysis-thrower-path")).toHaveAttribute("stroke-width", "0.225");
    expect(container.querySelector(".throw-analysis-thrower-path")).toHaveAttribute("stroke-dasharray", "0.5 0.35");
    expect(container.querySelector(".throw-analysis-release-dot")).toHaveAttribute("r", "1.1");
    expect(container.querySelector(".throw-analysis-release-ring")).toHaveAttribute("r", "1.9");
    expect(container.querySelector(".throw-analysis-release-ring")).toHaveAttribute("stroke-width", "0.25");
    expect(container.querySelector(".throw-analysis-label")).toHaveAttribute("font-size", "1.7");
    // The smoke's area does not scale; its glyph does.
    expect(container.querySelector(".throw-analysis-effect-area")).toHaveAttribute("r", String(utilityRadiusPercent("smoke", MIRAGE)));
    expect(container.querySelector(".throw-analysis-effect-icon")).toHaveAttribute("width", "1.6");
    // A bad scale counts as the whole map.
    const { container: bad } = draw({ scale: Number.NaN });
    expect(bad.querySelector(".throw-analysis-release-dot")).toHaveAttribute("r", "2.2");
  });

  it("puts the thrower on his path at the playback tick, and nowhere outside it", () => {
    const { container, rerender } = draw({ tick: 77264 });
    const thrower = container.querySelector('[data-testid="throw-analysis-thrower"]');
    expect(thrower).toHaveAttribute("cx", "94.5");
    expect(thrower).toHaveAttribute("cy", "38.5");
    rerender(
      <svg viewBox="0 0 100 100">
        <ThrowAnalysisLayer utility={SMOKE} analysis={analysis()} tick={77340} map={MIRAGE} scale={1} />
      </svg>
    );
    expect(container.querySelector('[data-testid="throw-analysis-thrower"]')).toBeNull();
  });

  it("shows the grenade only while it is in the air", () => {
    expect(draw({ tick: 77299 }).container.querySelector('[data-testid="throw-analysis-grenade"]')).toBeNull();
    const { container } = draw({ tick: 77330 });
    const grenade = container.querySelector('[data-testid="throw-analysis-grenade"]');
    expect(grenade).toHaveAttribute("transform", "translate(81.65 43.6)");
    expect(grenade?.querySelector(".throw-analysis-grenade-icon")).toHaveClass("lucide-cloud");
    expect(draw({ tick: 77420 }).container.querySelector('[data-testid="throw-analysis-grenade"]')).toBeNull();
  });

  it("marks a flash's landing with a small ring that keeps its screen size", () => {
    const flash: ReplayUtility = { ...SMOKE, id: "utility-flash-5-77300", type: "flash", throwerSide: "CT" };
    const { container } = draw({ utility: flash, scale: 0.5, map: null });
    expect(container.querySelector(".throw-analysis-layer")).toHaveClass("side-ct");
    const area = container.querySelector(".throw-analysis-effect-area");
    expect(area).toHaveAttribute("r", "1.3");
    expect(area).toHaveAttribute("stroke-width", "0.25");
    expect(container.querySelector(".throw-analysis-effect-icon")).toHaveClass("lucide-zap");
  });

  it("skips the path line when the thrower has fewer than two frames in the window", () => {
    const { container } = draw({ analysis: analysis({ throwerPath: [{ tick: 77300, x: 93, y: 40 }] }) });
    expect(container.querySelector(".throw-analysis-thrower-path")).toBeNull();
    expect(container.querySelector('[data-testid="throw-analysis-thrower"]')).toHaveAttribute("cx", "93");
  });
});
