import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailSummary } from "@/components/replay/DetailSummary";
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

    const strip = screen.getByRole("region", { name: "Demo status summary" });
    const cells = strip.querySelectorAll(".detail-summary-item");
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveClass("default");
    expect(cells[0]).toHaveTextContent("File");
    expect(cells[0]).toHaveTextContent("mock_demo_1.dem");
    expect(cells[0].querySelector("small")).toBeNull();
    expect(cells[1]).toHaveClass("good");
    expect(cells[2]).toHaveClass("danger");
    expect(cells[2].querySelector("small")).toHaveTextContent("No render worker picked this clip up.");
  });

  it("renders nothing but the strip for an empty list", () => {
    render(<DetailSummary items={[]} />);
    expect(screen.getByRole("region", { name: "Demo status summary" })).toBeEmptyDOMElement();
  });
});
