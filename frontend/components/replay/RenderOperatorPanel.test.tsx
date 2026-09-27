import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { RenderOperatorPanel } from "@/components/replay/RenderOperatorPanel";
import { renderJob, renderWorkerStatus, replayVideo } from "@/lib/test-fixtures/review";

type PanelProps = ComponentProps<typeof RenderOperatorPanel>;

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
  return screen.getByRole("region", { name: "视频生成状态" });
}

describe("RenderOperatorPanel", () => {
  it("says a queued job is stuck when no render worker has ever connected", () => {
    renderPanel({
      latestJob: renderJob({ status: "queued" }),
      renderWorker: renderWorkerStatus({ required: true, connected: false, status: "never_seen" }),
      jobCount: 1
    });
    expect(statusRow()).toHaveTextContent("排队中，视频服务暂时离线");
    expect(statusRow()).toHaveTextContent("已排队，服务恢复后会自动开始生成。");
    expect(statusRow()).not.toHaveTextContent("渲染器");
    expect(statusRow()).toHaveTextContent("共 1 个视频片段任务");
  });

  it("gives the operator the renderer's heartbeat behind dev tools", () => {
    renderPanel({
      latestJob: renderJob({ status: "queued" }),
      renderWorker: renderWorkerStatus({ required: true, connected: false, status: "never_seen" }),
      jobCount: 1,
      devTools: true
    });
    expect(statusRow()).toHaveTextContent("排队中，视频服务暂时离线");
    expect(statusRow()).toHaveTextContent("渲染器从未连接过。任务已保留，渲染器启动后会自动开始。");
  });

  it("treats a queued job as normal while a render worker is connected", () => {
    renderPanel({
      latestJob: renderJob({ status: "queued" }),
      renderWorker: renderWorkerStatus({ connected: true, status: "connected", age_seconds: 3 })
    });
    expect(statusRow()).toHaveTextContent("排队中");
    expect(statusRow()).not.toHaveTextContent("视频服务暂时离线");
    expect(statusRow()).toHaveTextContent("等待开始生成");
  });

  it("surfaces a failed job, with the backend error for dev tools only", () => {
    const latestJob = renderJob({ status: "failed", error_code: "RENDER_FAILED", error_message: "Render output could not be produced." });
    renderPanel({ latestJob });
    expect(statusRow()).toHaveTextContent("生成失败");
    expect(statusRow()).toHaveTextContent("可以在对应的建议上重新生成这段视频。");
    expect(statusRow()).not.toHaveTextContent("Render output could not be produced.");

    renderPanel({ latestJob, devTools: true });
    expect(screen.getAllByRole("region", { name: "视频生成状态" })[1]).toHaveTextContent("Render output could not be produced.");
  });

  it("keeps job ids, ticks and the manual render steps behind dev tools", () => {
    const latestJob = renderJob({ status: "rendering" });
    renderPanel({ latestJob, jobCount: 1 });
    expect(statusRow()).toHaveTextContent("视频生成中，完成后会出现在已保存的视频里。");
    expect(statusRow()).toHaveTextContent("10 秒");
    expect(statusRow()).not.toHaveTextContent(/prepare-job|render_clip|job-rend|400 - 1040|76561198/);

    renderPanel({ latestJob, jobCount: 1, devTools: true });
    const operator = screen.getAllByRole("region", { name: "视频生成状态" })[1];
    expect(operator).toHaveTextContent("prepare-job");
    expect(operator).toHaveTextContent("400 - 1040");
    expect(operator).toHaveTextContent("render_clip / rendering（job-rend）");
  });

  it("reports a rendered, playable clip as completed", () => {
    renderPanel({
      video: replayVideo({ status: "ready", source: "rendered", url: "/demos/demo-1/media/video?job=job-render-1" }),
      latestJob: renderJob({ status: "completed" })
    });
    expect(statusRow()).toHaveTextContent("已完成，可以播放");
    expect(statusRow()).toHaveTextContent("可以播放");
    expect(statusRow()).not.toHaveTextContent("job=job-render-1");
  });

  it("refreshes on demand and shows when a refresh is in flight", async () => {
    const user = userEvent.setup();
    const props = renderPanel();
    expect(statusRow()).toHaveTextContent("还没有视频片段任务");
    await user.click(screen.getByRole("button", { name: "刷新" }));
    expect(props.onRefresh).toHaveBeenCalledTimes(1);

    renderPanel({ refreshing: true });
    expect(screen.getByRole("button", { name: "刷新中" })).toBeDisabled();
  });
});
