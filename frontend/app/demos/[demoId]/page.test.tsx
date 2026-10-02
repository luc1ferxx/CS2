import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DemoDetailPage from "@/app/demos/[demoId]/page";
import { useAuth } from "@/components/auth/AuthProvider";
import * as api from "@/lib/api";
import { buildRoundReviewModel } from "@/lib/round-review";
import type { AuthAccount, AuthCapabilities } from "@/lib/auth";
import { takeLibraryNotice } from "@/lib/library-notice";
import { PLAYER_PREFERENCE_KEY } from "@/lib/personal-review";
import {
  CT_ANCHOR_ID,
  T_ENTRY_ID,
  coachingEvent,
  demoStatus,
  demoSummary,
  ingestion,
  renderJob,
  renderWorkerStatus,
  replayData,
  replayVideo
} from "@/lib/test-fixtures/review";
import type { CoachingFeedback } from "@/types/coaching";
import type { ReplayData } from "@/types/replay";

// One router object for the whole file: a fresh one per render would change the page's callbacks.
const { router } = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn() } }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ demoId: "demo-1" }),
  useRouter: () => router
}));

vi.mock("@/components/auth/AuthProvider", () => ({ useAuth: vi.fn() }));

// Counted, not replaced: playback frames must not rebuild the round rail.
vi.mock("@/lib/round-review", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/round-review")>();
  return { ...actual, buildRoundReviewModel: vi.fn(actual.buildRoundReviewModel) };
});

const DEV_ACCOUNT: AuthAccount = { displayName: "Local development", avatarUrl: null, provider: "development" };

function steamAccount(steamId: string, displayName = "Tactical Reviewer"): AuthAccount {
  return { displayName, avatarUrl: null, provider: "steam", steamId };
}

function mockAuth(capabilities: AuthCapabilities, account: AuthAccount = DEV_ACCOUNT) {
  vi.mocked(useAuth).mockReturnValue({
    state: {
      status: "authenticated",
      account,
      capabilities
    },
    provider: "steam",
    refreshSession: vi.fn(async () => true),
    signIn: vi.fn(),
    signOut: vi.fn(async () => {}),
    markSignedOut: vi.fn()
  });
}

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
    clearCoachingFeedback: vi.fn(),
    deleteDemo: vi.fn()
  };
});

const LOADING_REPLAY = "正在载入回放…";
const PARSING = "解析中：读取回合与玩家位置…";
const REPLAY_FETCH_FAILED = "载入回放数据时出错，可以重新载入。";
// The processing card's own line; the page repeats it in a visually hidden live region.
const CARD_TEXT = { selector: ".demo-state-heading p" };
const GENERATE_HINT = "从建议或当前时刻生成视频，完成后会保存在这里，随时重播。";

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

// A finished clip for the reviewed player, saved before render clips were turned off.
function savedClipJob() {
  return renderJob({
    status: "completed",
    video_status: "ready",
    video: replayVideo({
      status: "ready",
      source: "rendered",
      url: "/demos/demo-1/render/jobs/job-render-1/media/video",
      durationSeconds: 10,
      tickStart: 400,
      tickEnd: 1040,
      povSteamId: T_ENTRY_ID,
      renderJobId: "job-render-1"
    }),
    finished_at: "2026-09-18T12:01:00Z"
  });
}

function playerSelect() {
  return screen.getByRole("combobox", { name: /复盘玩家/ });
}

function slider() {
  return screen.getByRole("slider", { name: "拖动定位回放" });
}

async function openReviewFor(playerId: string) {
  const user = userEvent.setup();
  render(<DemoDetailPage />);
  await screen.findByRole("region", { name: "播放控制" });
  await user.selectOptions(playerSelect(), playerId);
  await screen.findByRole("heading", { name: /^正在复盘 / });
  return user;
}

describe("DemoDetailPage", () => {
  beforeEach(() => {
    mockAuth({ devTools: true, renderClips: true });
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent()]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(
      renderWorkerStatus({ connected: true, status: "connected", age_seconds: 2 })
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    window.localStorage.clear();
    // The review keeps its place in the URL; each test starts from a clean address.
    window.history.replaceState(null, "", "/");
  });

  // Status turns "completed" a beat before the replay request resolves. That
  // window used to render the failure copy for every healthy demo; the browser
  // caught it, the pure helper tests could not.
  it("starts the replay download with the status request and shows the review skeleton meanwhile", async () => {
    const status = deferred<ReturnType<typeof demoStatus>>();
    const replay = deferred<ReplayData>();
    const radars: string[] = [];
    const srcSetter = vi.spyOn(HTMLImageElement.prototype, "src", "set").mockImplementation((value: string) => {
      radars.push(value);
    });
    vi.mocked(api.getDemoStatus).mockReturnValue(status.promise);
    vi.mocked(api.getReplay).mockReturnValue(replay.promise);

    render(<DemoDetailPage />);

    // No waterfall: the replay is already requested while the status is in flight.
    expect(api.getReplay).toHaveBeenCalledWith("demo-1");
    expect(screen.getByRole("heading", { name: "正在打开比赛" })).toBeInTheDocument();
    expect(document.querySelector(".detail-meta")).toHaveTextContent("— 回合");
    expect(document.querySelector(".review-skeleton")).toHaveAttribute("aria-busy", "true");

    await act(async () => {
      status.resolve(demoStatus({ map_name: "de_nuke" }));
    });

    expect(await screen.findByText("可以复盘")).toBeInTheDocument();
    expect(screen.getByText(LOADING_REPLAY)).toBeInTheDocument();
    expect(document.querySelector(".detail-meta .detail-map")).toHaveTextContent(/^Nuke$/);
    expect(radars).toEqual(["/maps/de_nuke_radar.png", "/maps/de_nuke_lower_radar.png"]);
    expect(screen.queryByText(REPLAY_FETCH_FAILED)).not.toBeInTheDocument();
    expect(screen.queryByText(/正在准备/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).not.toBeInTheDocument();

    await act(async () => {
      replay.resolve(replayData());
    });

    expect(await screen.findByRole("region", { name: "播放控制" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Mock Match demo-1" })).toBeInTheDocument();
    expect(api.getReplay).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(LOADING_REPLAY)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    srcSetter.mockRestore();
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
    expect(screen.getByRole("alert")).toHaveTextContent(REPLAY_FETCH_FAILED);
    expect(screen.queryByText(/Replay artifact is missing/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "播放控制" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新处理" }));

    expect(api.retryDemoParse).toHaveBeenCalledWith("demo-1");
    expect(await screen.findByText(PARSING, CARD_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(REPLAY_FETCH_FAILED)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reloads a replay whose download failed without re-parsing the demo", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(replayData());

    render(<DemoDetailPage />);

    expect(await screen.findByText(REPLAY_FETCH_FAILED)).toBeInTheDocument();
    expect(screen.getByText("回放不可用")).toBeInTheDocument();
    expect(screen.queryByText("可以复盘")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新载入" }));

    expect(await screen.findByRole("region", { name: "播放控制" })).toBeInTheDocument();
    expect(api.retryDemoParse).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says a missing or foreign demo cannot be found and stops polling", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockRejectedValue(new api.ApiError(404, "Demo not found"));
    vi.mocked(api.getReplay).mockRejectedValue(new api.ApiError(404, "Demo not found"));

    render(<DemoDetailPage />);
    await flush();

    expect(screen.getByRole("heading", { name: "找不到这场比赛" })).toBeInTheDocument();
    expect(screen.getByText("这场比赛可能已被删除，或属于其他账号。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回我的比赛" })).toHaveAttribute("href", "/dashboard");
    expect(screen.queryByText(/Demo not found|正在准备|正在载入/)).not.toBeInTheDocument();
    expect(document.querySelector(".detail-meta")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);
  });

  it("keeps retrying a dropped connection with backoff and recovers without a reload", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockRejectedValue(new TypeError("Failed to fetch"));
    vi.mocked(api.getReplay).mockRejectedValue(new TypeError("Failed to fetch"));

    render(<DemoDetailPage />);
    await flush();

    expect(screen.getByRole("heading", { name: "暂时无法打开比赛" })).toBeInTheDocument();
    expect(screen.getByText("网络连接中断，正在自动重试…")).toBeInTheDocument();
    expect(screen.queryByText(/应用已启动|diagnostics|Redis/)).not.toBeInTheDocument();
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });
    expect(api.getDemoStatus).toHaveBeenCalledTimes(2);
    // The second failure waits longer before the next attempt.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });
    expect(api.getDemoStatus).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3200);
    });
    expect(api.getDemoStatus).toHaveBeenCalledTimes(3);

    vi.mocked(api.getDemoStatus).mockResolvedValue(parsingStatus());
    fireEvent.click(screen.getByRole("button", { name: "立即重试" }));
    await flush();

    expect(screen.getByText(PARSING, CARD_TEXT)).toBeInTheDocument();
    expect(screen.queryByText("网络连接中断，正在自动重试…")).not.toBeInTheDocument();
  });

  it("shows Chinese processing progress for a parsing demo and opens the review once it completes", async () => {
    vi.useFakeTimers();
    mockAuth({ devTools: false, renderClips: false });
    vi.setSystemTime(new Date("2026-09-18T12:02:13Z"));
    vi.mocked(api.getDemoStatus)
      .mockResolvedValueOnce(demoStatus({
        status: "parsing",
        map_name: "unknown",
        round_count: 0,
        completed_at: null,
        ingestion: ingestion({ phase: "parsing", active: true, jobStatus: "processing" })
      }))
      .mockResolvedValue(demoStatus());
    // The early replay request of a demo that is not ready answers 409 and is dropped.
    vi.mocked(api.getReplay)
      .mockRejectedValueOnce(new api.ApiError(409, "Replay is not ready"))
      .mockResolvedValue(replayData());

    render(<DemoDetailPage />);
    await flush();

    expect(screen.getByText("读取比赛中")).toBeInTheDocument();
    expect(document.querySelector(".detail-meta .detail-map")).toHaveTextContent(/^地图待识别$/);
    expect(document.querySelector(".detail-meta")).toHaveTextContent("— 回合");
    expect(screen.getByRole("heading", { name: "正在处理这场比赛" })).toBeInTheDocument();
    expect(screen.getByText(PARSING, CARD_TEXT)).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "处理进度" });
    expect(within(steps).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "上传完成（已完成）", "2解析比赛（进行中）", "3分析建议（未开始）"
    ]);
    expect(within(steps).getByText("解析比赛").closest("li")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText(/已用时 2:13/)).toBeInTheDocument();
    // The developer strip and its English labels stay out of the player's view.
    expect(screen.queryByText(/replay unavailable|not requested|Calibration|Parser/)).not.toBeInTheDocument();
    expect(screen.queryByText(REPLAY_FETCH_FAILED)).not.toBeInTheDocument();
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });
    await flush();
    await flush();

    expect(api.getDemoStatus).toHaveBeenCalledTimes(2);
    expect(api.getReplay).toHaveBeenCalledTimes(2);
    expect(screen.getByText("可以复盘")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "播放控制" })).toBeInTheDocument();
    expect(screen.queryByText(PARSING, CARD_TEXT)).not.toBeInTheDocument();
    // Screen readers hear that the wait is over.
    expect(screen.getByText("比赛处理完成，可以开始复盘。")).toHaveAttribute("aria-live", "polite");
  });

  it("warns when processing runs long and keeps the technical strip for dev tools only", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({
      status: "analyzing",
      completed_at: null,
      ingestion: ingestion({ phase: "analyzing", active: true, stale: true, jobStatus: "processing" })
    }));

    render(<DemoDetailPage />);

    expect(await screen.findByText("分析中：整理复盘建议…")).toBeInTheDocument();
    expect(screen.getByText("处理时间比平时长。如果超时，会自动标记为失败，届时可以重新处理。")).toBeInTheDocument();
    // Dev tools keep the diagnostics, folded away under the technical details.
    expect(screen.getByText("技术信息")).toBeInTheDocument();
    expect(document.querySelector(".detail-summary-strip")).not.toBeVisible();
  });

  it("pauses the status poll while the tab is hidden and checks again on return", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockResolvedValue(parsingStatus());
    const setHidden = (hidden: boolean) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event("visibilitychange"));
    };

    render(<DemoDetailPage />);
    await flush();
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);

    await act(async () => setHidden(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800 * 5);
    });
    expect(api.getDemoStatus).toHaveBeenCalledTimes(1);

    await act(async () => setHidden(false));
    await flush();
    expect(api.getDemoStatus).toHaveBeenCalledTimes(2);
  });

  it("records a verdict on a suggestion for the reviewed player and shows the progress", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.saveCoachingFeedback).mockResolvedValue({
      verdict: "helpful", note: null, updated_at: "2026-09-18T12:00:00Z"
    });

    render(<DemoDetailPage />);
    await screen.findByRole("region", { name: "播放控制" });

    // Suggestions follow the reviewed player; nothing is rated until one is chosen.
    expect(screen.queryByRole("group", { name: /这条建议是否有帮助/ })).not.toBeInTheDocument();
    await user.selectOptions(playerSelect(), T_ENTRY_ID);
    const verdicts = await screen.findByRole("group", { name: /这条建议是否有帮助/ });
    expect(screen.getByText(/已评价 0\/1/)).toBeInTheDocument();

    await user.click(within(verdicts).getByRole("button", { name: "有帮助" }));

    expect(api.saveCoachingFeedback).toHaveBeenCalledWith("demo-1", "event-1", { verdict: "helpful" });
    expect(within(verdicts).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/已评价 1\/1/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("asks for a reload, not a re-send, when the suggestion was recomputed away (404)", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.saveCoachingFeedback).mockRejectedValue(new api.ApiError(404, "Coaching event not found"));

    render(<DemoDetailPage />);
    await screen.findByRole("region", { name: "播放控制" });
    await user.selectOptions(playerSelect(), T_ENTRY_ID);
    const verdicts = await screen.findByRole("group", { name: /这条建议是否有帮助/ });
    const card = document.getElementById("coaching-event-event-1") as HTMLElement;

    await user.click(within(verdicts).getByRole("button", { name: "有帮助" }));

    expect(await within(card).findByText("建议已按新规则更新，请刷新页面")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "重新保存" })).not.toBeInTheDocument();
    expect(within(card).queryByText("评价没有保存。")).not.toBeInTheDocument();
    // The verdict was not kept, so the card and the progress roll back.
    expect(within(verdicts).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/已评价 0\/1/)).toBeInTheDocument();
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
    await screen.findByRole("region", { name: "播放控制" });
    await user.selectOptions(playerSelect(), T_ENTRY_ID);
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

  it("opens a Steam account's review on its own player without asking", async () => {
    mockAuth({ devTools: false, renderClips: false }, steamAccount(T_ENTRY_ID));
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    render(<DemoDetailPage />);

    expect(await screen.findByRole("heading", { name: "正在复盘 T Entry" })).toBeInTheDocument();
    expect(document.querySelector(".match-banner-meta")).toHaveTextContent("1 条建议");
    expect(screen.getByRole("group", { name: /这条建议是否有帮助/ })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "选择你在这场比赛中的玩家" })).not.toBeInTheDocument();
    expect(screen.queryByText(/xelex/)).not.toBeInTheDocument();
  });

  it("asks a viewer missing from the match to pick, and keeps that pick for their account only", async () => {
    const user = userEvent.setup();
    const viewer = steamAccount("76561198000000077");
    mockAuth({ devTools: false, renderClips: false }, viewer);
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({ coaching_event_count: 12 }));
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    const { unmount } = render(<DemoDetailPage />);

    expect(await screen.findByRole("heading", { name: "选择你在这场比赛中的玩家" })).toBeInTheDocument();
    expect(screen.getByText("全场建议")).toBeInTheDocument();
    expect(screen.getByText("全场建议").parentElement).toHaveTextContent("全场建议12");
    expect(screen.getByText("选择你在这场比赛中的玩家后，这里会列出对应的建议。")).toBeInTheDocument();
    expect(screen.queryByText(/xelex|未找到/)).not.toBeInTheDocument();

    // The empty coaching column hands focus to the picker instead of leaving a dead end.
    await user.click(within(document.querySelector<HTMLElement>(".coaching-panel")!).getByRole("button", { name: "选择玩家" }));
    const picker = screen.getByRole("group", { name: "选择你在这场比赛中的玩家" });
    expect(within(picker).getByRole("button", { name: "T Entry" })).toHaveFocus();

    await user.click(within(picker).getByRole("button", { name: "T Entry" }));

    expect(await screen.findByRole("heading", { name: "正在复盘 T Entry" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /这条建议是否有帮助/ })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(`${PLAYER_PREFERENCE_KEY}:steam:76561198000000077`) ?? "null"))
      .toEqual({ version: 1, identity: T_ENTRY_ID });
    expect(window.localStorage.getItem(PLAYER_PREFERENCE_KEY)).toBeNull();
    unmount();

    // Another account on the same browser is asked again rather than handed that pick.
    mockAuth({ devTools: false, renderClips: false }, steamAccount("76561198000000088", "Housemate"));
    render(<DemoDetailPage />);
    expect(await screen.findByRole("heading", { name: "选择你在这场比赛中的玩家" })).toBeInTheDocument();
  });

  it("keeps the development default player for local QA", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData({
      players: [{ id: T_ENTRY_ID, name: "xelex", side: "T", color: "#f5b542" }, replayData().players[1]]
    }));

    render(<DemoDetailPage />);

    expect(await screen.findByRole("heading", { name: "正在复盘 xelex" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /这条建议是否有帮助/ })).toBeInTheDocument();
  });

  it("jumps to the most severe suggestion first, not the earliest", async () => {
    const user = userEvent.setup();
    mockAuth({ devTools: false, renderClips: false }, steamAccount(T_ENTRY_ID));
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.getCoaching).mockResolvedValue([
      coachingEvent({ id: "spacing", tick_start: 300, tick_end: 300, severity: "low",
        structured_context_json: { ruleId: "poor_spacing" } }),
      coachingEvent({ id: "untraded", round_number: 2, tick_start: 1200, tick_end: 1200, severity: "medium",
        structured_context_json: { ruleId: "untraded_death" } }),
      coachingEvent({ id: "other-player", player_id: CT_ANCHOR_ID, player_name: "CT Anchor",
        tick_start: 250, tick_end: 250, severity: "high" })
    ]);

    render(<DemoDetailPage />);
    await screen.findByRole("heading", { name: "正在复盘 T Entry" });

    const metrics = document.querySelector(".personal-review-metrics");
    expect(metrics).toHaveTextContent("2 条建议");
    expect(metrics).toHaveTextContent("1 条值得优先回看");
    await user.click(screen.getByRole("button", { name: "查看最值得回看的一条" }));

    // Lands in round 2 a few seconds early, clamped to the end of its freeze time (1064).
    expect(slider()).toHaveValue("1064");
    expect(document.querySelector(".review-finding-strip")).toHaveTextContent("第 2 回合 0:03");
  });

  it("explains a failed parse and offers the retry the backend allows", async () => {
    mockAuth({ devTools: false, renderClips: false });
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
    expect(screen.getByRole("heading", { name: "这场比赛处理失败" })).toBeInTheDocument();
    expect(screen.getByText("文件无法读取，可能不是完整的 CS2 .dem 比赛文件。")).toBeInTheDocument();
    // A broken file is fixed by another copy of it, so that comes first; the
    // retry the backend allows stays available beside it.
    expect(screen.getByRole("link", { name: "去「我的比赛」重新上传" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("button", { name: "重新处理" })).toBeEnabled();
    expect(screen.queryByText(/Invalid demo header|retry available|attempt 1/)).not.toBeInTheDocument();
    expect(screen.getByText("INVALID_DEMO")).not.toBeVisible();
    expect(screen.queryByRole("region", { name: "播放控制" })).not.toBeInTheDocument();
  });

  it("offers only the re-upload when a transient failure can no longer be retried", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(
      demoStatus({
        status: "failed",
        completed_at: null,
        ingestion: ingestion({
          phase: "failed",
          jobStatus: "failed",
          retryable: false,
          failure: {
            errorCode: "PARSE_TIMED_OUT",
            message: "Parsing this demo took too long and was stopped.",
            failedAt: "2026-09-18T12:00:00Z",
            updatedAt: "2026-09-18T12:00:00Z",
            retryable: false,
            attemptCount: 2
          }
        })
      })
    );

    render(<DemoDetailPage />);

    expect(await screen.findByText("处理时间过长，已停止。")).toBeInTheDocument();
    expect(screen.getByText("暂时无法重新处理，请在「我的比赛」重新上传这场比赛的 .dem 文件。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去「我的比赛」重新上传" })).toBeInTheDocument();
  });

  it("keeps a parse retry refused by the owner's in-flight limit through polling until the demo leaves failed", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockResolvedValue(
      demoStatus({
        status: "failed",
        completed_at: null,
        ingestion: ingestion({ phase: "failed", jobStatus: "failed", retryable: true })
      })
    );
    vi.mocked(api.retryDemoParse).mockRejectedValue(
      new api.ApiError(429, "Too many demos are processing.", "active_parse_limit", 60)
    );
    const limit = "已有比赛正在处理，请等当前比赛处理完成后再试。";

    render(<DemoDetailPage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新处理" }));
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(limit);
    const pollsBefore = vi.mocked(api.getDemoStatus).mock.calls.length;

    // A failed demo keeps the status poll running; each successful poll used to wipe the message.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800 * 2);
    });
    expect(vi.mocked(api.getDemoStatus).mock.calls.length).toBeGreaterThanOrEqual(pollsBefore + 2);
    expect(screen.getByRole("alert")).toHaveTextContent(limit);

    // Once the demo is being processed again the refusal no longer applies.
    vi.mocked(api.getDemoStatus).mockResolvedValue(parsingStatus());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1800);
    });
    expect(screen.getByText(PARSING, CARD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a refused clip in Chinese and lets the player dismiss it", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.createRenderClipJob).mockRejectedValue(
      new api.ApiError(409, "Demo parse must complete before rendering")
    );

    const user = await openReviewFor(T_ENTRY_ID);
    await user.click(screen.getByRole("button", { name: "生成这一刻的视频" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("比赛处理完成后才能生成视频。");
    expect(alert).not.toHaveTextContent(/Demo parse|rendering/);
    await user.click(within(alert).getByRole("button", { name: "关闭提示" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("polls an active render without re-reading the demo video until a job moves", async () => {
    vi.useFakeTimers();
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    const queued = renderJob({ status: "queued", video_status: "queued" });
    vi.mocked(api.getRenderJobs).mockResolvedValue([queued]);

    render(<DemoDetailPage />);
    await flush();
    await flush();
    expect(screen.getByRole("region", { name: "播放控制" })).toBeInTheDocument();
    const jobPollsBefore = vi.mocked(api.getRenderJobs).mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000 * 2);
    });
    expect(vi.mocked(api.getRenderJobs).mock.calls.length).toBeGreaterThanOrEqual(jobPollsBefore + 2);
    expect(api.getDemoVideo).not.toHaveBeenCalled();

    vi.mocked(api.getRenderJobs).mockResolvedValue([{ ...queued, status: "rendering", video_status: "rendering" }]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(api.getDemoVideo).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(api.getDemoVideo).toHaveBeenCalledTimes(1);
  });

  it("offers clip generation and the dev tools when the API serves them", async () => {
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    const user = await openReviewFor(T_ENTRY_ID);

    expect(screen.getByRole("button", { name: "生成这一刻的视频" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成视频" })).toBeInTheDocument();
    expect(screen.getByText(GENERATE_HINT)).toBeInTheDocument();
    // The advanced panels are only mounted (and loaded) once the drawer is opened.
    expect(document.querySelector(".render-operator-panel")).toBeNull();
    expect(screen.getByText("视频校准、生成记录与技术详情")).toBeInTheDocument();
    await user.click(screen.getByText("高级工具"));
    await waitFor(() => expect(document.querySelector(".render-operator-panel")).not.toBeNull());
    await waitFor(() => expect(document.querySelector(".video-setup-panel")).not.toBeNull());
    expect(screen.getByRole("button", { name: "创建模拟视频任务（开发测试）" })).toBeInTheDocument();
    // Developers get the job internals and the replay data checks.
    expect(within(document.querySelector<HTMLElement>(".render-operator-panel")!).getByText("Tick 范围")).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "回放数据检查" })).toBeInTheDocument();
  });

  it("keeps render clips but drops the dev tools when only render clips are enabled", async () => {
    mockAuth({ devTools: false, renderClips: true });
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    const user = await openReviewFor(T_ENTRY_ID);

    expect(screen.getByRole("button", { name: "生成这一刻的视频" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成视频" })).toBeInTheDocument();
    expect(screen.getByText("视频生成状态与比赛信息")).toBeInTheDocument();
    await user.click(screen.getByText("高级工具"));
    await waitFor(() => expect(document.querySelector(".render-operator-panel")).not.toBeNull());
    expect(document.querySelector(".video-setup-panel")).toBeNull();
    expect(screen.queryByText("创建模拟视频任务（开发测试）")).not.toBeInTheDocument();
    // Players see the clip state and the match summary, not job ids, ticks or data checks.
    expect(within(document.querySelector<HTMLElement>(".render-operator-panel")!).queryByText("Tick 范围")).toBeNull();
    expect(screen.getByRole("region", { name: "比赛状态摘要" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "回放数据检查" })).not.toBeInTheDocument();
  });

  it("names the browser tab after the demo and gives the title back on leaving", async () => {
    document.title = "CS2 Demo Coach";
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    const { unmount } = render(<DemoDetailPage />);

    await waitFor(() => expect(document.title).toBe("Mock Match demo-1 - CS2 复盘"));
    unmount();
    expect(document.title).toBe("CS2 Demo Coach");
  });

  describe("deleting the match", () => {
    afterEach(() => {
      takeLibraryNotice();
    });

    it("confirms in a dialog from 高级工具, then returns to the library with a one-shot notice", async () => {
      mockAuth({ devTools: false, renderClips: false });
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      const request = deferred<void>();
      vi.mocked(api.deleteDemo).mockReturnValue(request.promise);
      const user = userEvent.setup();

      render(<DemoDetailPage />);
      await screen.findByRole("region", { name: "播放控制" });
      await user.click(screen.getByText("高级工具"));
      const opener = screen.getByRole("button", { name: "删除这场比赛" });
      await user.click(opener);

      const dialog = screen.getByRole("dialog", { name: "永久删除这场比赛？" });
      expect(dialog).toHaveAttribute("aria-modal", "true");
      expect(dialog).toHaveTextContent("「Mock Match demo-1」的这些内容会被永久删除");
      expect(dialog).toHaveTextContent("此操作无法撤销。");
      // The harmless choice has focus; Esc closes and hands focus back.
      expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(opener).toHaveFocus();
      expect(api.deleteDemo).not.toHaveBeenCalled();

      await user.click(opener);
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "永久删除" }));

      expect(api.deleteDemo).toHaveBeenCalledWith("demo-1");
      const busy = screen.getByRole("dialog");
      expect(within(busy).getByRole("button", { name: "正在删除…" })).toBeDisabled();
      expect(within(busy).getByRole("button", { name: "取消" })).toBeDisabled();

      await act(async () => {
        request.resolve();
      });

      expect(router.replace).toHaveBeenCalledWith("/dashboard");
      // The name travels in memory, never in the address.
      expect(window.location.href).not.toMatch(/Mock|deleted/);
      expect(takeLibraryNotice()).toBe("已永久删除「Mock Match demo-1」");
      expect(takeLibraryNotice()).toBeNull();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("keeps a failed delete in the dialog and treats an already deleted match as deleted", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      vi.mocked(api.deleteDemo)
        .mockRejectedValueOnce(new api.ApiError(500, "boom"))
        .mockRejectedValueOnce(new api.ApiError(404, "Demo not found"));
      const user = userEvent.setup();

      render(<DemoDetailPage />);
      await screen.findByRole("region", { name: "播放控制" });
      await user.click(screen.getByText("高级工具"));
      await user.click(screen.getByRole("button", { name: "删除这场比赛" }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "永久删除" }));

      const dialog = screen.getByRole("dialog");
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("服务暂时出错，请稍后重试。");
      expect(router.replace).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: "永久删除" }));

      await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/dashboard"));
      expect(takeLibraryNotice()).toBe("已永久删除「Mock Match demo-1」");
    });

    it("offers deletion on a processing demo and stops polling once it starts", async () => {
      vi.useFakeTimers();
      vi.mocked(api.getDemoStatus).mockResolvedValue(parsingStatus());
      vi.mocked(api.getReplay).mockRejectedValue(new api.ApiError(409, "Replay is not ready"));
      const request = deferred<void>();
      vi.mocked(api.deleteDemo).mockReturnValue(request.promise);

      render(<DemoDetailPage />);
      await flush();
      expect(screen.getByRole("heading", { name: "正在处理这场比赛" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "删除这场比赛" }));
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "永久删除" }));
      await flush();
      const calls = vi.mocked(api.getDemoStatus).mock.calls.length;

      // The demo is going away: no status poll, so no 404 card or banner flashes before the redirect.
      vi.mocked(api.getDemoStatus).mockRejectedValue(new api.ApiError(404, "Demo not found"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(api.getDemoStatus).toHaveBeenCalledTimes(calls);
      expect(screen.queryByRole("heading", { name: "找不到这场比赛" })).not.toBeInTheDocument();

      await act(async () => {
        request.resolve();
      });
      expect(router.replace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("hides clip generation when render clips are off but still plays saved clips", async () => {
    mockAuth({ devTools: false, renderClips: false });
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
    vi.mocked(api.getRenderJobs).mockResolvedValue([savedClipJob()]);

    const user = await openReviewFor(T_ENTRY_ID);

    expect(screen.queryByRole("button", { name: /这一刻的视频/ })).not.toBeInTheDocument();
    const coaching = document.querySelector<HTMLElement>(".coaching-panel")!;
    expect(within(coaching).queryByRole("button", { name: /生成视频|观看视频|重试|等待生成|生成中/, hidden: true })).toBeNull();
    expect(document.querySelector(".generate-clip-button")).toBeNull();
    expect(screen.queryByText("视频生成状态")).not.toBeInTheDocument();
    expect(screen.queryByText("手动视频校准（开发测试）")).not.toBeInTheDocument();
    expect(screen.queryByText("创建模拟视频任务（开发测试）")).not.toBeInTheDocument();
    expect(screen.queryByText(/可按需生成第一人称视频/)).not.toBeInTheDocument();
    expect(screen.queryByText(/视频校准/)).not.toBeInTheDocument();
    expect(api.getRenderJobs).toHaveBeenCalledWith("demo-1");

    // A saved clip keeps the video controls even though generation is off.
    expect(screen.getByRole("button", { name: /第一人称/ })).toBeInTheDocument();
    const play = screen.getByRole("button", { name: "观看视频：T Entry · 第 1 回合 0:04", hidden: true });
    expect(play).toHaveAttribute("aria-pressed", "false");
    await user.click(play);

    expect(await screen.findByRole("button", { name: "重新观看：T Entry · 第 1 回合 0:04", hidden: true }))
      .toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector("video.first-person-video")).not.toBeNull();
    expect(api.createRenderClipJob).not.toHaveBeenCalled();
    expect(api.retryRenderClipJob).not.toHaveBeenCalled();
  });

  it("shows no video controls at all to an account without clip generation or saved videos", async () => {
    mockAuth({ devTools: false, renderClips: false });
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());

    await openReviewFor(T_ENTRY_ID);

    // No dead 第一人称 switch, no standing "tactical replay" note, no empty saved-videos drawer.
    expect(screen.queryByRole("button", { name: /第一人称/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/当前时刻使用战术回放/)).not.toBeInTheDocument();
    expect(screen.queryByText("已保存的视频")).not.toBeInTheDocument();
    expect(screen.queryByText(GENERATE_HINT)).not.toBeInTheDocument();
    expect(screen.queryByText(/生成视频/)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "战术地图" })).toBeInTheDocument();
  });

  describe("review workflow", () => {
    function blurAll() {
      act(() => (document.activeElement as HTMLElement | null)?.blur());
    }

    it("opens each round where freeze time ends and keeps 回合开始 one click away", async () => {
      const user = userEvent.setup();
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());

      render(<DemoDetailPage />);
      await screen.findByRole("region", { name: "播放控制" });
      expect(slider()).toHaveValue("164");

      await user.click(screen.getByRole("button", { name: /^第 2 回合 / }));
      expect(slider()).toHaveValue("1064");
      await user.click(within(screen.getByRole("group", { name: "快速跳转" })).getByRole("button", { name: /^回合开始/ }));
      expect(slider()).toHaveValue("1000");
    });

    it("carries Play at the end of a round into the next round", async () => {
      const user = userEvent.setup();
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());

      render(<DemoDetailPage />);
      await screen.findByRole("region", { name: "播放控制" });
      fireEvent.keyDown(slider(), { key: "End" });
      expect(slider()).toHaveValue("900");

      await user.click(screen.getByRole("button", { name: "下一回合" }));
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
      // Round 2 from the end of its freeze time (1064), already a few real frames into playback.
      const tick = Number((slider() as HTMLInputElement).value);
      expect(tick).toBeGreaterThanOrEqual(1064);
      expect(tick).toBeLessThan(1064 + 64);
    });

    it("plays by elapsed time and stops at the end of the round", async () => {
      vi.useFakeTimers({
        toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "requestAnimationFrame", "cancelAnimationFrame", "performance"]
      });
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());

      render(<DemoDetailPage />);
      await flush();
      await flush();
      const railBuilds = vi.mocked(buildRoundReviewModel).mock.calls.length;
      fireEvent.click(screen.getByRole("button", { name: "播放" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(vi.mocked(buildRoundReviewModel).mock.calls.length).toBe(railBuilds);
      // One second of 1x playback is about 64 ticks, however the frames were spaced.
      const afterOneSecond = Number((slider() as HTMLInputElement).value);
      expect(afterOneSecond).toBeGreaterThan(164 + 56);
      expect(afterOneSecond).toBeLessThan(164 + 70);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });
      expect(slider()).toHaveValue("900");
      expect(screen.getByRole("button", { name: "下一回合" })).toBeInTheDocument();
    });

    it("lands a few seconds before a suggestion and returns to its card", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      vi.mocked(api.getCoaching).mockResolvedValue([
        coachingEvent({ id: "first", tick_start: 400, tick_end: 400 }),
        coachingEvent({ id: "second", tick_start: 450, tick_end: 450 })
      ]);

      const user = await openReviewFor(T_ENTRY_ID);
      const watchButton = () => within(document.getElementById("coaching-event-first")!).getByRole("button", { name: /查看这一刻/ });
      await user.click(watchButton());

      // Three seconds of lead-in: 400 - 3 × 64.
      expect(slider()).toHaveValue("208");
      const strip = document.querySelector(".review-finding-strip")!;
      expect(strip).toHaveTextContent("当前建议");
      expect(strip).toHaveTextContent("第 1 回合 0:04");

      // 下一条 counts from the suggestion being watched, not from the earlier playhead.
      await user.click(screen.getByRole("button", { name: "下一条建议" }));
      expect(slider()).toHaveValue("258");
      expect(screen.getByRole("button", { name: "下一条建议" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "上一条建议" }));
      expect(slider()).toHaveValue("208");

      await user.click(watchButton());
      await user.click(screen.getByRole("button", { name: "返回建议" }));
      expect(watchButton()).toHaveFocus();
      expect(document.querySelector(".review-finding-strip")).toBeNull();
    });

    it("anchors the clicked suggestion when two share a tick, even if the click does not focus the button", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      vi.mocked(api.getCoaching).mockResolvedValue([
        coachingEvent({ id: "first", tick_start: 400, tick_end: 400, structured_context_json: { ruleId: "untraded_death" } }),
        coachingEvent({ id: "second", tick_start: 400, tick_end: 400, structured_context_json: { ruleId: "poor_spacing" } })
      ]);

      const user = await openReviewFor(T_ENTRY_ID);
      const card = (id: string) => document.getElementById(`coaching-event-${id}`)!;
      const watchButton = within(card("second")).getByRole("button", { name: /查看这一刻/ });
      const secondTitle = watchButton.getAttribute("aria-label")!.replace(/^查看这一刻：/, "");
      blurAll();
      // Safari and macOS Firefox leave focus where it was on a mouse click.
      fireEvent.click(watchButton);

      const strip = document.querySelector(".review-finding-strip")!;
      expect(strip).toHaveTextContent(secondTitle);
      // The stage takes focus on the next frame; 返回建议 then goes back to the clicked card.
      await waitFor(() => expect(document.getElementById("player")).toHaveFocus());
      await user.click(within(strip as HTMLElement).getByRole("button", { name: "返回建议" }));
      expect(watchButton).toHaveFocus();
    });

    it("keeps the review keys working after the slider or the speed select is used", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());

      const user = await openReviewFor(T_ENTRY_ID);
      act(() => slider().focus());
      await user.keyboard("{ArrowRight}");
      expect(slider()).toHaveValue(String(164 + 5 * 64));
      await user.keyboard("k");
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
      await user.keyboard(" ");
      expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
      await user.keyboard("]");
      expect(slider()).toHaveValue("1064");

      act(() => screen.getByRole("combobox", { name: "播放倍速" }).focus());
      await user.keyboard("k");
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
    });

    it("reports a refused verdict on its card, not in a banner at the top of the page", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      vi.mocked(api.saveCoachingFeedback).mockRejectedValue(new TypeError("Failed to fetch"));

      const user = await openReviewFor(T_ENTRY_ID);
      const verdicts = screen.getByRole("group", { name: /这条建议是否有帮助/ });
      await user.click(within(verdicts).getByRole("button", { name: "有帮助" }));

      await waitFor(() => expect(within(verdicts).getByRole("button", { name: "有帮助" })).toHaveAttribute("aria-pressed", "false"));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(document.querySelector(".error-banner")).toBeNull();
      // The page's false return reaches the card, which says so in place and offers to re-send.
      const card = verdicts.closest<HTMLElement>("article")!;
      expect(await within(card).findByText("评价没有保存。")).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "重新保存" })).toBeInTheDocument();
    });

    it("keeps its place in the URL and restores it on reload", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      window.history.replaceState(null, "", `/demos/demo-1?r=2&t=1200&p=${CT_ANCHOR_ID}`);

      const { unmount } = render(<DemoDetailPage />);
      expect(await screen.findByRole("heading", { name: "正在复盘 CT Anchor" })).toBeInTheDocument();
      expect(slider()).toHaveValue("1200");

      fireEvent.keyDown(slider(), { key: "Home" });
      await waitFor(() => expect(window.location.search).toBe(`?r=2&t=1000&p=${CT_ANCHOR_ID}`));
      expect(window.location.pathname).toBe("/demos/demo-1");
      unmount();

      // Anything that does not fit this match is ignored.
      window.history.replaceState(null, "", "/demos/demo-1?r=99&t=abc&p=someone-else");
      render(<DemoDetailPage />);
      await screen.findByRole("region", { name: "播放控制" });
      expect(slider()).toHaveValue("164");
      expect(screen.queryByRole("heading", { name: /正在复盘 CT Anchor/ })).not.toBeInTheDocument();
    });

    it("only highlights a player clicked on the map; switching takes the explicit button", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());

      const user = await openReviewFor(T_ENTRY_ID);
      const roster = screen.getByRole("group", { name: "玩家名单" });
      // One Tab stop for the whole roster.
      expect(within(roster).getAllByRole("button").filter((button) => button.tabIndex === 0)).toHaveLength(1);

      fireEvent.click(document.querySelectorAll(".map-player-dot")[1]);
      expect(document.querySelector(".map-highlight-card")).toHaveTextContent("CT Anchor");
      expect(screen.getByRole("heading", { name: "正在复盘 T Entry" })).toBeInTheDocument();
      expect(screen.getByRole("group", { name: /这条建议是否有帮助/ })).toBeInTheDocument();

      await user.click(within(roster).getByRole("button", { name: /^T Entry/ }));
      expect(document.querySelector(".map-highlight-card")).toHaveTextContent("正在复盘");
      await user.click(within(roster).getByRole("button", { name: "CT Anchor" }));
      await user.click(screen.getByRole("button", { name: "切换为他的视角" }));
      expect(await screen.findByRole("heading", { name: "正在复盘 CT Anchor" })).toBeInTheDocument();
    });

    it("drives playback, seeking, rounds and suggestions from the keyboard, but not while typing", async () => {
      vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
      vi.mocked(api.getReplay).mockResolvedValue(replayData());
      vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent({ id: "later", round_number: 2, tick_start: 1400, tick_end: 1400 })]);

      const user = await openReviewFor(T_ENTRY_ID);
      blurAll();
      expect(slider()).toHaveValue("164");

      await user.keyboard("{ArrowRight}");
      expect(slider()).toHaveValue("484");
      await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
      expect(slider()).toHaveValue("420");
      await user.keyboard("]");
      expect(slider()).toHaveValue("1064");
      await user.keyboard("[[");
      expect(slider()).toHaveValue("164");
      await user.keyboard("n");
      expect(slider()).toHaveValue(String(1400 - 192));

      blurAll();
      await user.keyboard("?");
      expect(screen.getByText("空格 / K")).toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(screen.queryByText("空格 / K")).not.toBeInTheDocument();

      blurAll();
      await user.keyboard(" ");
      expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
      await user.keyboard("k");
      expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
      // Playback ran for a few real frames in between; typing below must not move it again.
      const pausedAt = (slider() as HTMLInputElement).value;

      await user.click(screen.getByRole("textbox", { name: "我的游戏名或 Steam ID" }));
      await user.keyboard("k ]");
      expect(screen.getByRole("button", { name: "播放" })).toBeInTheDocument();
      expect(slider()).toHaveValue(pausedAt);
    });
  });
});

describe("DemoDetailPage match header and first-person view (S9)", () => {
  beforeEach(() => {
    mockAuth({ devTools: true, renderClips: true });
    vi.mocked(api.getCoaching).mockResolvedValue([coachingEvent()]);
    vi.mocked(api.getRenderJobs).mockResolvedValue([]);
    vi.mocked(api.getDemoVideo).mockResolvedValue(replayVideo());
    vi.mocked(api.getReplay).mockResolvedValue(replayData());
  });

  afterEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("heads the review with the score, the stored team names and the reviewed player's team", async () => {
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus({ connected: true, status: "connected" }));
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus({
      matchSummary: {
        teams: [
          { key: "A", name: "Alpha", startSide: "T", score: 1 },
          { key: "B", name: "Bravo", startSide: "CT", score: 1 }
        ],
        rounds: 2,
        version: 1
      }
    }));
    await openReviewFor(T_ENTRY_ID);

    const banner = document.querySelector<HTMLElement>(".match-banner")!;
    // The score is computed from the replay; the summary only brings the names. A two-round match has one half.
    // T Entry was picked to review; the development account's own player is not in this match.
    expect(banner.querySelector(".visually-hidden")).toHaveTextContent(/^比分 Alpha（复盘中的队伍） 1 比 1 Bravo。上半场 Alpha T 1，Bravo CT 1。$/);
    expect(banner).not.toHaveTextContent("你的队伍");
    expect(banner.querySelector(".match-banner-board")).toHaveAttribute("aria-hidden", "true");
    expect(within(banner).getByRole("heading", { level: 1 })).toHaveTextContent("Mock Match demo-1");
    expect(banner.querySelector(".match-banner-meta")).toHaveTextContent(/地图\s*Inferno/);
    const scoreboard = screen.getByRole("region", { name: "计分板" });
    const reviewedRow = within(scoreboard).getByRole("row", { name: /T Entry 复盘中/ });
    expect(reviewedRow).toHaveClass("selected");
  });

  it("marks the viewer's own team as theirs even while reviewing an opponent", async () => {
    mockAuth({ devTools: false, renderClips: false }, steamAccount(T_ENTRY_ID));
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    const user = userEvent.setup();
    render(<DemoDetailPage />);
    await screen.findByRole("heading", { name: "正在复盘 T Entry" });
    const sentence = () => document.querySelector(".match-banner .visually-hidden");
    expect(sentence()).toHaveTextContent(/^比分 队伍 A（你的队伍） 1 比 1 队伍 B。/);

    await user.selectOptions(playerSelect(), CT_ANCHOR_ID);
    await screen.findByRole("heading", { name: "正在复盘 CT Anchor" });
    expect(sentence()).toHaveTextContent(/^比分 队伍 A（你的队伍） 1 比 1 队伍 B（复盘中的队伍）。/);
  });

  it("goes back to the map when play is pressed on the first-person explanation", async () => {
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus());
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    const user = await openReviewFor(T_ENTRY_ID);
    const views = screen.getByRole("group", { name: "回放视图" });
    await user.click(within(views).getByRole("button", { name: "第一人称" }));
    await screen.findByRole("region", { name: "这一刻还没有第一人称视频" });

    await user.click(screen.getByRole("button", { name: "播放" }));
    expect(screen.queryByRole("region", { name: "这一刻还没有第一人称视频" })).toBeNull();
    expect(within(views).getByRole("button", { name: "战术回放" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
  });

  it("explains a missing first-person clip instead of offering a dead tab, and goes back to the map", async () => {
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus());
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    const user = await openReviewFor(T_ENTRY_ID);

    const views = screen.getByRole("group", { name: "回放视图" });
    const firstPerson = within(views).getByRole("button", { name: "第一人称" });
    expect(firstPerson).toBeEnabled();
    await user.click(firstPerson);

    const explainer = await screen.findByRole("region", { name: "这一刻还没有第一人称视频" });
    expect(firstPerson).toHaveAttribute("aria-pressed", "true");
    expect(explainer).toHaveTextContent("第一人称视频要用录制电脑上的 CS2 真实录下来，每段约 20 秒。");
    expect(await within(explainer).findByText("录制程序没有运行，暂时无法生成。可以先排队，录制程序启动后自动开始。")).toBeInTheDocument();
    expect(explainer).toHaveTextContent("Start CS2 Coach.cmd");
    // The toolbar button above still queues this moment; the explanation offers the same action.
    expect(within(explainer).getByRole("button", { name: "生成这一刻的视频" })).toBeEnabled();
    expect(document.querySelector(".review-main-canvas .replay-panel")).toBeNull();

    await user.click(within(explainer).getByRole("button", { name: "返回战术回放" }));
    expect(screen.queryByRole("region", { name: "这一刻还没有第一人称视频" })).toBeNull();
    expect(within(views).getByRole("button", { name: "战术回放" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers to generate this moment when the recorder runs, and points at clips saved elsewhere", async () => {
    vi.mocked(api.getRenderWorkerStatus).mockResolvedValue(renderWorkerStatus({ connected: true, status: "connected" }));
    vi.mocked(api.getDemoStatus).mockResolvedValue(demoStatus());
    vi.mocked(api.getRenderJobs).mockResolvedValue([savedClipJob()]);
    vi.mocked(api.createRenderClipJob).mockResolvedValue({ ...renderJob({ job_id: "job-new", tick_start: 164 }), video: replayVideo() });
    const user = await openReviewFor(T_ENTRY_ID);

    await user.click(within(screen.getByRole("group", { name: "回放视图" })).getByRole("button", { name: "第一人称" }));
    const explainer = await screen.findByRole("region", { name: "这一刻还没有第一人称视频" });
    expect(await within(explainer).findByText("可以现在生成这一刻的视频。")).toBeInTheDocument();

    await user.click(within(explainer).getByRole("button", { name: "查看已保存的视频（1 段）" }));
    const saved = document.getElementById("saved-clips") as HTMLDetailsElement;
    expect(saved.open).toBe(true);
    expect(saved.querySelector("summary")).toHaveFocus();

    await user.click(within(explainer).getByRole("button", { name: "生成这一刻的视频" }));
    expect(api.createRenderClipJob).toHaveBeenCalledTimes(1);
  });
});
