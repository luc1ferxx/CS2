import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReplayDiagnosticsPanel } from "@/components/replay/ReplayDiagnosticsPanel";
import { buildReplayDiagnostics } from "@/lib/replay-diagnostics";
import { coachingEvent, replayData } from "@/lib/test-fixtures/review";

describe("ReplayDiagnosticsPanel", () => {
  it("lists the contract version and the counts behind the review", () => {
    const diagnostics = buildReplayDiagnostics(replayData(), [coachingEvent()], []);
    render(<ReplayDiagnosticsPanel diagnostics={diagnostics} />);

    const panel = screen.getByRole("region", { name: "回放数据检查" });
    expect(panel).toHaveTextContent("回放数据检查");
    expect(panel).toHaveTextContent("回放数据已载入");
    expect(panel).toHaveTextContent(diagnostics.contractVersion);
    expect(panel).toHaveTextContent("回合2");
    expect(panel).toHaveTextContent("玩家2");
    expect(panel).toHaveTextContent("位置帧4");
    expect(panel).toHaveTextContent("建议1");
    expect(panel).toHaveTextContent("解析事件0");
    expect(panel).toHaveTextContent("视频生成未生成");
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

    const panel = screen.getByRole("region", { name: "回放数据检查" });
    expect(panel).toHaveTextContent("旧版或不完整的回放数据，已整理后用于复盘");
    expect(panel.querySelector(".replay-contract-version")).toHaveClass("legacy");
    const warnings = panel.querySelectorAll(".replay-diagnostics-warnings li");
    expect(warnings.length).toBe(diagnostics.warnings.length);
    expect(diagnostics.warnings.length).toBeGreaterThan(0);
    // Known warnings read in Chinese; the helper's English stays out of the panel.
    expect(panel).toHaveTextContent("旧版回放数据已在载入时整理。");
    expect(panel).toHaveTextContent("缺少可选字段：events。");
    expect(panel).not.toHaveTextContent(/No parser events|legacy replay contract|Missing optional/);
  });
});
