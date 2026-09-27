import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FirstPersonExplainer, firstPersonExplainerState } from "@/components/replay/FirstPersonExplainer";
import { T_ENTRY_ID, renderJob, replayVideo } from "@/lib/test-fixtures/review";

const READY_CLIP = renderJob({
  status: "completed",
  video_status: "ready",
  video: replayVideo({
    status: "ready", source: "rendered", url: "/demos/demo-1/render/jobs/job-render-1/media/video",
    durationSeconds: 10, tickStart: 400, tickEnd: 1040, povSteamId: T_ENTRY_ID, renderJobId: "job-render-1"
  })
});

function renderExplainer(overrides: Partial<Parameters<typeof FirstPersonExplainer>[0]> = {}) {
  const props = {
    canGenerate: true,
    playerSelected: true,
    workerOffline: false,
    clipJob: null,
    requestLabel: "生成这一刻的视频",
    requesting: false,
    savedClipCount: 0,
    devTools: false,
    onRequest: vi.fn(),
    onChoosePlayer: vi.fn(),
    onShowSavedClips: vi.fn(),
    onBackToMap: vi.fn(),
    ...overrides
  };
  render(<FirstPersonExplainer {...props} />);
  return { ...props, panel: screen.getByRole("region") };
}

describe("FirstPersonExplainer", () => {
  it("says why there is no video, what it takes, and offers to generate this moment", async () => {
    const user = userEvent.setup();
    const { panel, onRequest, onBackToMap } = renderExplainer();
    expect(panel).toHaveAccessibleName("这一刻还没有第一人称视频");
    expect(panel).toHaveTextContent("第一人称视频要用录制电脑上的 CS2 真实录下来，每段约 20 秒。");
    expect(within(panel).getByRole("status")).toHaveTextContent("可以现在生成这一刻的视频。");
    await user.click(within(panel).getByRole("button", { name: "生成这一刻的视频" }));
    expect(onRequest).toHaveBeenCalledTimes(1);
    await user.click(within(panel).getByRole("button", { name: "返回战术回放" }));
    expect(onBackToMap).toHaveBeenCalledTimes(1);
    expect(within(panel).queryByRole("button", { name: /查看已保存的视频/ })).toBeNull();
  });

  it("explains an offline recorder, offers to queue this moment like the toolbar does, and the dev launcher only in dev", async () => {
    const user = userEvent.setup();
    const { panel, onRequest } = renderExplainer({ workerOffline: true, devTools: true });
    expect(within(panel).getByRole("status")).toHaveTextContent(/^录制程序没有运行，暂时无法生成。可以先排队，录制程序启动后自动开始。$/);
    expect(within(panel).getByRole("status")).toHaveClass("notice", "warning");
    expect(panel).toHaveTextContent("Start CS2 Coach.cmd");
    await user.click(within(panel).getByRole("button", { name: "生成这一刻的视频" }));
    expect(onRequest).toHaveBeenCalledTimes(1);
  });

  it("asks for a player first when the recorder is offline and nobody is reviewed", async () => {
    const user = userEvent.setup();
    const { panel, onChoosePlayer } = renderExplainer({ workerOffline: true, playerSelected: false });
    expect(within(panel).queryByRole("button", { name: "生成这一刻的视频" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "选择玩家" }));
    expect(onChoosePlayer).toHaveBeenCalledTimes(1);
  });

  it("keeps the launcher hint out of production", () => {
    const { panel } = renderExplainer({ workerOffline: true, devTools: false });
    expect(panel).not.toHaveTextContent("Start CS2 Coach.cmd");
  });

  it("follows a clip for this moment through queued and recording, with no second request", () => {
    expect(firstPersonExplainerState({ canGenerate: true, playerSelected: true, workerOffline: false, clipJob: renderJob() })).toBe("queued");
    expect(firstPersonExplainerState({ canGenerate: true, playerSelected: true, workerOffline: true, clipJob: renderJob() })).toBe("queued_offline");
    const { panel } = renderExplainer({ clipJob: renderJob({ status: "rendering" }), requestLabel: "视频生成中" });
    expect(within(panel).getByRole("status")).toHaveTextContent("正在录制这一刻的视频，完成后会自动出现在这里。");
    expect(within(panel).queryByRole("button", { name: "视频生成中" })).toBeNull();
  });

  it("says a queued clip waits for the recorder when it is offline", () => {
    const { panel } = renderExplainer({ clipJob: renderJob(), workerOffline: true });
    expect(within(panel).getByRole("status")).toHaveTextContent("录制程序没有运行，暂时无法生成。这一刻已排队，录制程序启动后会自动开始。");
  });

  it("offers another try after a failed clip, with the page's busy label while it is sent", () => {
    const { panel } = renderExplainer({ clipJob: renderJob({ status: "failed" }), requesting: true, requestLabel: "正在提交" });
    expect(within(panel).getByRole("status")).toHaveTextContent("上次生成失败了，可以再试一次。");
    expect(within(panel).getByRole("button", { name: "正在提交" })).toBeDisabled();
  });

  it("asks for the reviewed player first", async () => {
    const user = userEvent.setup();
    const { panel, onChoosePlayer } = renderExplainer({ playerSelected: false });
    expect(within(panel).getByRole("status")).toHaveTextContent("先选择要复盘的玩家，才能生成这一刻的视频。");
    await user.click(within(panel).getByRole("button", { name: "选择玩家" }));
    expect(onChoosePlayer).toHaveBeenCalledTimes(1);
  });

  it("points at clips saved elsewhere in the match, and at them alone when clips cannot be made", async () => {
    const user = userEvent.setup();
    const { panel, onShowSavedClips } = renderExplainer({ canGenerate: false, savedClipCount: 3 });
    expect(within(panel).getByRole("status")).toHaveTextContent("这个账户不能生成新视频，只能观看已经保存的片段。");
    expect(within(panel).queryByRole("button", { name: "生成这一刻的视频" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "查看已保存的视频（3 段）" }));
    expect(onShowSavedClips).toHaveBeenCalledTimes(1);
  });

  it("offers to open a saved clip that covers this moment", () => {
    const { panel } = renderExplainer({ clipJob: READY_CLIP, requestLabel: "观看这一刻的视频" });
    expect(panel).toHaveAccessibleName("这一刻有保存好的第一人称视频");
    expect(within(panel).getByRole("button", { name: "观看这一刻的视频" })).toBeEnabled();
  });
});
