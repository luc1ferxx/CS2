import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReplayDiagnosticsPanel } from "@/components/replay/ReplayDiagnosticsPanel";
import { buildReplayDiagnostics } from "@/lib/replay-diagnostics";
import { coachingEvent, replayData } from "@/lib/test-fixtures/review";

describe("ReplayDiagnosticsPanel", () => {
  it("lists the contract version and the counts behind the review", () => {
    const diagnostics = buildReplayDiagnostics(replayData(), [coachingEvent()], []);
    render(<ReplayDiagnosticsPanel diagnostics={diagnostics} />);

    const panel = screen.getByRole("region", { name: "Replay contract diagnostics" });
    expect(panel).toHaveTextContent("Replay Contract");
    expect(panel).toHaveTextContent("Contract data loaded");
    expect(panel).toHaveTextContent(diagnostics.contractVersion);
    expect(panel).toHaveTextContent("Rounds2");
    expect(panel).toHaveTextContent("Players2");
    expect(panel).toHaveTextContent("Frames4");
    expect(panel).toHaveTextContent("Coaching1");
    expect(panel).toHaveTextContent("Parser events0");
  });

  it("flags a normalized legacy contract and shows its warnings", () => {
    const diagnostics = buildReplayDiagnostics(
      replayData({
        contractVersion: "legacy",
        events: [],
        diagnostics: {
          contractVersion: "legacy",
          normalizedLegacy: true,
          parserEventCount: 0,
          roundCount: 2,
          playerCount: 2,
          frameCount: 4,
          missingFields: ["events"],
          degradedFields: [],
          eventFamilyCounts: {},
          missingEventFamilies: []
        }
      }),
      [],
      []
    );
    render(<ReplayDiagnosticsPanel diagnostics={diagnostics} />);

    const panel = screen.getByRole("region", { name: "Replay contract diagnostics" });
    expect(panel).toHaveTextContent("Legacy or degraded contract normalized for review");
    expect(panel.querySelector(".replay-contract-version")).toHaveClass("legacy");
    const warnings = panel.querySelectorAll(".replay-diagnostics-warnings li");
    expect(warnings.length).toBe(diagnostics.warnings.length);
    expect(diagnostics.warnings.length).toBeGreaterThan(0);
  });
});
