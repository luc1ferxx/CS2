import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RenderOperatorPanel } from "@/components/replay/RenderOperatorPanel";
import { renderJob, renderWorkerStatus, replayVideo } from "@/lib/test-fixtures/review";

type PanelProps = Parameters<typeof RenderOperatorPanel>[0];

function renderPanel(overrides: Partial<PanelProps> = {}) {
  const props: PanelProps = {
    video: replayVideo(),
    latestJob: null,
    renderWorker: null,
    jobCount: 0,
    refreshing: false,
    onRefresh: vi.fn(),
    ...overrides
  };
  render(<RenderOperatorPanel {...props} />);
  return props;
}

function statusRow() {
  return screen.getByRole("region", { name: "Render operator status" });
}

describe("RenderOperatorPanel", () => {
  it("says a queued job is stuck when no render worker has ever connected", () => {
    renderPanel({
      latestJob: renderJob({ status: "queued" }),
      renderWorker: renderWorkerStatus({ required: true, connected: false, status: "never_seen" }),
      jobCount: 1
    });
    expect(statusRow()).toHaveTextContent("Queued, no render worker");
    expect(statusRow()).toHaveTextContent("Render worker has never polled. The job stays queued and starts on its own once one connects.");
    expect(statusRow()).toHaveTextContent("1 render_clip jobs");
  });

  it("treats a queued job as normal while a render worker is connected", () => {
    renderPanel({
      latestJob: renderJob({ status: "queued" }),
      renderWorker: renderWorkerStatus({ connected: true, status: "connected", age_seconds: 3 })
    });
    expect(statusRow()).toHaveTextContent("Queued");
    expect(statusRow()).not.toHaveTextContent("no render worker");
    expect(statusRow()).toHaveTextContent("Waiting for a render worker to claim the job");
  });

  it("surfaces a failed job with its error", () => {
    renderPanel({
      latestJob: renderJob({ status: "failed", error_code: "RENDER_FAILED", error_message: "Render output could not be produced." })
    });
    expect(statusRow()).toHaveTextContent("Failed");
    expect(statusRow()).toHaveTextContent("Render output could not be produced.");
  });

  it("reports a rendered, playable clip as completed", () => {
    renderPanel({
      video: replayVideo({ status: "ready", source: "rendered", url: "/demos/demo-1/media/video?job=job-render-1" }),
      latestJob: renderJob({ status: "completed" })
    });
    expect(statusRow()).toHaveTextContent("Completed and playable");
    expect(statusRow()).toHaveTextContent("Private media bound");
  });

  it("refreshes on demand and shows when a refresh is in flight", async () => {
    const user = userEvent.setup();
    const props = renderPanel();
    expect(statusRow()).toHaveTextContent("No render_clip jobs");
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(props.onRefresh).toHaveBeenCalledTimes(1);

    renderPanel({ refreshing: true });
    expect(screen.getByRole("button", { name: "Refreshing" })).toBeDisabled();
  });
});
