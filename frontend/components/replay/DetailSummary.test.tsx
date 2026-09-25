import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailSummary, detailSummaryValue } from "@/components/replay/DetailSummary";
import type { DetailSummaryItem } from "@/lib/demo-library";

describe("DetailSummary", () => {
  it("renders every item with its tone and optional detail", () => {
    const items: DetailSummaryItem[] = [
      { label: "File", value: "mock_demo_1.dem" },
      { label: "Parser", value: "ready", tone: "good" },
      {
        label: "Render",
        value: "render_clip failed",
        tone: "danger",
        detail: "No render worker picked this clip up."
      }
    ];
    render(<DetailSummary items={items} />);

    const strip = screen.getByRole("region", { name: "比赛状态摘要" });
    const cells = strip.querySelectorAll(".detail-summary-item");
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveClass("default");
    expect(cells[0]).toHaveTextContent("文件");
    expect(cells[0]).toHaveTextContent("mock_demo_1.dem");
    expect(cells[0].querySelector("small")).toBeNull();
    expect(cells[1]).toHaveClass("good");
    expect(cells[1]).toHaveTextContent("处理状态已完成");
    expect(cells[2]).toHaveClass("danger");
    expect(cells[2]).toHaveTextContent("视频生成视频片段，失败");
    expect(cells[2].querySelector("small")).toHaveTextContent("No render worker picked this clip up.");
  });

  it("shows the known summary values in Chinese and anything else as-is", () => {
    expect(detailSummaryValue("Coaching", "12 events")).toBe("12 条");
    expect(detailSummaryValue("Coaching", "1 event")).toBe("1 条");
    expect(detailSummaryValue("Media", "replay unavailable")).toBe("未载入回放");
    expect(detailSummaryValue("Media", "mock pending")).toBe("无视频");
    expect(detailSummaryValue("Media", "rendered ready")).toBe("已生成视频，可播放");
    expect(detailSummaryValue("Render", "not requested")).toBe("未生成");
    expect(detailSummaryValue("Calibration", "approximate")).toBe("近似校准");
    expect(detailSummaryValue("Parser", "PARSER_INVALID_DEMO")).toBe("PARSER_INVALID_DEMO");
    expect(detailSummaryValue("File", "ready.dem")).toBe("ready.dem");
  });

  it("renders nothing but the strip for an empty list", () => {
    render(<DetailSummary items={[]} />);
    expect(screen.getByRole("region", { name: "比赛状态摘要" })).toBeEmptyDOMElement();
  });
});
