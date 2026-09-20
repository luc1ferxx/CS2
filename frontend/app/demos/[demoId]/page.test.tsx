import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import * as api from "@/lib/api";
import {
  T_ENTRY_ID,
  coachingEvent,
  demoStatus,
  demoSummary,
  ingestion,
  renderWorkerStatus,
  replayData,
  replayVideo
} from "@/lib/test-fixtures/review";
import type { CoachingFeedback } from "@/types/coaching";
import type { ReplayData } from "@/types/replay";

vi.mock("next/navigation", () => ({
  useParams: () => ({ demoId: "demo-1" })
}));

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    state: {
      status: "authenticated",
      account: { displayName: "Local development", avatarUrl: null, provider: "development" }
    },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {})
  })
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getDemoStatus: vi.fn(),
    getReplay: vi.fn(),
    getCoaching: vi.fn(),
    getRenderJobs: vi.fn(),
    getDemoVideo: vi.fn(),
    getRenderWorkerStatus: vi.fn(),
    retryDemoParse: vi.fn(),
    createMockRenderJob: vi.fn(),
    createRenderClipJob: vi.fn(),
    retryRenderClipJob: vi.fn(),
    saveVideoCalibration: vi.fn(),
    uploadDemoVideo: vi.fn(),
    saveCoachingFeedback: vi.fn(),
    clearCoachingFeedback: vi.fn()
  };
});

const PREPARING = "正在准备回放，完成后会自动显示。";
const REPLAY_FETCH_FAILED = "回放暂时无法打开，请刷新页面重试。";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function parsingStatus() {
  return demoStatus({
    status: "parsing",
    completed_at: null,
    ingestion: ingestion({ phase: "parsing", active: true, jobStatus: "processing" })
  });
}

describe("DemoDetailPage", () => {
  beforeEach(() => {
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent()]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(
      renderWorkerStatus({ connected: true, status: "connected", age_seconds: 2 })
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Status turns "completed" a beat before the replay request resolves. That
  // window used to render the failure copy for every healthy demo; the browser
  // caught it, the pure helper tests could not.
  it("keeps the preparing notice while a completed demo's replay is still loading", async () => {
    const replay = deferred<ReplayData>();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockReturnValue(replay.promise);

    render(<DemoDetailPage />);

    expect(await screen.findByText("可以开始复盘")).toBeInTheDocument();
    expect(screen.getByText(PREPARING)).toBeInTheDocument();
    expect(screen.queryByText(REPLAY_FETCH_FAILED)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).not.toBeInTheDocument();

    await act(async () => {
      replay.resolve(replayData());
    });

    expect(await screen.findByRole("region", { name: "Review transport" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Mock Match demo-1" })).toBeInTheDocument();
    expect(screen.queryByText(PREPARING)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a failed replay fetch and drops that verdict once a re-parse is queued", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({ ingestion: ingestion({ retryable: true }) }));
    vi.mocked(api.getReplay).mockRejectedValue(new Error("Replay artifact is missing"));
    vi.mocked(api.retryDemoParse).mockImplementation(async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(parsingStatus());
      return demoSummary({ status: "queued" });
    });

    render(<DemoDetailPage />);

    expect(await screen.findByText(REPLAY_FETCH_FAILED)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Review transport" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新处理" }));

    expect(api.retryDemoParse).toHaveBeenCalledWith("demo-1");
    expect(await screen.findByText(PREPARING)).toBeInTheDocument();
    expect(screen.queryByText(REPLAY_FETCH_FAILED)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("polls a parsing demo and loads the replay once it completes", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockResolvedValueOnce(parsingStatus()).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    render(<DemoDetailPage />);
    await flush();

    expect(screen.getByText("正在准备比赛")).toBeInTheDocument();
    expect(screen.getByText(PREPARING)).toBeInTheDocument();
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);
    expect(api.getReplay).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });
    await flush();
    await flush();

    expect(api.getDemoStatus).toHaveBeenCalledTimes(2);
    expect(api.getReplay).toHaveBeenCalledTimes(1);
    expect(screen.getByText("可以开始复盘")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Review transport" })).toBeInTheDocument();
    expect(screen.queryByText(PREPARING)).not.toBeInTheDocument();
  });

  it("records a verdict on a suggestion for the reviewed player and shows the progress", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.saveCoachingFeedback).mockResolvedValue({
      verdict: "helpful", note: null, updated_at: "2026-09-18T12:00:00Z"
    });

    render(<DemoDetailPage />);
    await screen.findByRole("region", { name: "Review transport" });

    // Suggestions follow the reviewed player; nothing is rated until one is chosen.
    expect(screen.queryByRole("group", { name: /这条建议是否有帮助/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Player to review" }), T_ENTRY_ID);
    const verdicts = await screen.findByRole("group", { name: /这条建议是否有帮助/ });
    expect(screen.getByText(/已评价 0\/1/)).toBeInTheDocument();

    await user.click(within(verdicts).getByRole("button", { name: "有帮助" }));

    expect(api.saveCoachingFeedback).toHaveBeenCalledWith("demo-1", "event-1", { verdict: "helpful" });
    expect(within(verdicts).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/已评价 1\/1/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the latest verdict when an earlier save resolves late, and clears on a repeat click", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    const helpful = deferred<CoachingFeedback>();
    const irrelevant = deferred<CoachingFeedback>();
    vi.mocked(api.saveCoachingFeedback)
      .mockReturnValueOnce(helpful.promise)
      .mockReturnValueOnce(irrelevant.promise);
    vi.mocked(api.clearCoachingFeedback).mockResolvedValue(undefined);

    render(<DemoDetailPage />);
    await screen.findByRole("region", { name: "Review transport" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Player to review" }), T_ENTRY_ID);
    const verdicts = await screen.findByRole("group", { name: /这条建议是否有帮助/ });

    await user.click(within(verdicts).getByRole("button", { name: "有帮助" }));
    await user.click(within(verdicts).getByRole("button", { name: "无关" }));
    await act(async () => {
      irrelevant.resolve({ verdict: "irrelevant", note: null, updated_at: "2026-09-18T12:00:02Z" });
      helpful.resolve({ verdict: "helpful", note: null, updated_at: "2026-09-18T12:00:01Z" });
    });

    expect(within(verdicts).getByRole("button", { name: "无关" })).toHaveAttribute("aria-pressed", "true");
    expect(within(verdicts).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "false");

    await user.click(within(verdicts).getByRole("button", { name: "无关" }));
    expect(api.clearCoachingFeedback).toHaveBeenCalledWith("demo-1", "event-1");
    expect(within(verdicts).getByRole("button", { name: "无关" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/已评价 0\/1/)).toBeInTheDocument();
  });

  it("explains a failed parse and offers the retry the backend allows", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(
      demoStatus({
        status: "failed",
        completed_at: null,
        error_message: "Invalid demo header",
        ingestion: ingestion({
          phase: "failed",
          jobStatus: "failed",
          retryable: true,
          failure: {
            errorCode: "INVALID_DEMO",
            message: "Invalid demo header",
            failedAt: "2026-09-18T12:00:00Z",
            updatedAt: "2026-09-18T12:00:00Z",
            retryable: true,
            attemptCount: 1
          }
        })
      })
    );

    render(<DemoDetailPage />);

    expect(await screen.findByText("处理失败")).toBeInTheDocument();
    expect(screen.getByText(/^比赛处理失败。/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新处理" })).toBeEnabled();
    expect(api.getReplay).not.toHaveBeenCalled();
  });
});
