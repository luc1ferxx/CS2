import type { RenderJobStatus } from "@/lib/api";
import { clipIsActive, playableClipVideo } from "@/lib/render-clips";

export type FirstPersonExplainerState =
  | "play"
  | "rendering"
  | "queued"
  | "queued_offline"
  | "offline"
  | "can_generate"
  | "choose_player"
  | "saved_only";

interface FirstPersonExplainerProps {
  // Whether this account may generate clips (production can turn it off).
  canGenerate: boolean;
  playerSelected: boolean;
  workerOffline: boolean;
  // The clip job for this moment and reviewed player, if any.
  clipJob: RenderJobStatus | null;
  // The page's label for its "这一刻" request button, so both read the same.
  requestLabel: string;
  requesting: boolean;
  savedClipCount: number;
  devTools: boolean;
  onRequest: () => void;
  onChoosePlayer: () => void;
  onShowSavedClips: () => void;
  onBackToMap: () => void;
}

export function firstPersonExplainerState({ canGenerate, playerSelected, workerOffline, clipJob }:
  Pick<FirstPersonExplainerProps, "canGenerate" | "playerSelected" | "workerOffline" | "clipJob">): FirstPersonExplainerState {
  if (playableClipVideo(clipJob)) return "play";
  if (clipIsActive(clipJob)) {
    if (clipJob?.status !== "queued") return "rendering";
    return workerOffline ? "queued_offline" : "queued";
  }
  if (!canGenerate) return "saved_only";
  if (workerOffline) return "offline";
  return playerSelected ? "can_generate" : "choose_player";
}

/**
 * What the 第一人称 view shows when no clip covers the current moment: why there is no
 * video here, and the one thing that can be done about it.
 */
export function FirstPersonExplainer(props: FirstPersonExplainerProps) {
  const { clipJob, requestLabel, requesting, savedClipCount, devTools } = props;
  const state = firstPersonExplainerState(props);
  const offline = state === "offline" || state === "queued_offline";
  // Offline, the request still queues (as the toolbar button does), so the same action is offered.
  const canRequest = state === "play" || state === "can_generate" || (state === "offline" && props.playerSelected);
  const needsPlayer = state === "choose_player" || (state === "offline" && !props.playerSelected);
  return (
    <div className="first-person-explainer" role="region" aria-labelledby="first-person-explainer-title">
      <div className="first-person-explainer-body">
        <h3 className="first-person-explainer-title" id="first-person-explainer-title">
          {state === "play" ? "这一刻有保存好的第一人称视频" : "这一刻还没有第一人称视频"}
        </h3>
        <p>第一人称视频要用录制电脑上的 CS2 真实录下来，每段约 20 秒。</p>
        <p className={`first-person-explainer-state notice${offline ? " warning" : ""}`} role="status">
          {stateLine(state, clipJob)}
        </p>
        {offline && devTools ? (
          <p className="first-person-explainer-hint">开发环境：在录制电脑上双击仓库根目录的 Start CS2 Coach.cmd，会同时启动录制程序。</p>
        ) : null}
        <div className="first-person-explainer-actions">
          {canRequest ? (
            <button className="primary-button" type="button" disabled={requesting} onClick={props.onRequest}>
              {requestLabel}
            </button>
          ) : null}
          {needsPlayer ? (
            <button className="secondary-button" type="button" onClick={props.onChoosePlayer}>选择玩家</button>
          ) : null}
          {savedClipCount > 0 ? (
            <button className="text-button" type="button" onClick={props.onShowSavedClips}>
              查看已保存的视频（{savedClipCount} 段）
            </button>
          ) : null}
          <button className="text-button" type="button" onClick={props.onBackToMap}>返回战术回放</button>
        </div>
      </div>
    </div>
  );
}

function stateLine(state: FirstPersonExplainerState, clipJob: RenderJobStatus | null): string {
  switch (state) {
    case "play":
      return "视频还没有打开，点下面的按钮就会从这一刻开始播放。";
    case "rendering":
      return "正在录制这一刻的视频，完成后会自动出现在这里。";
    case "queued":
      return "已排队，录制程序空闲后就会开始录制这一刻。";
    case "queued_offline":
      return "录制程序没有运行，暂时无法生成。这一刻已排队，录制程序启动后会自动开始。";
    case "offline":
      return "录制程序没有运行，暂时无法生成。可以先排队，录制程序启动后自动开始。";
    case "choose_player":
      return "先选择要复盘的玩家，才能生成这一刻的视频。";
    case "saved_only":
      return "这个账户不能生成新视频，只能观看已经保存的片段。";
    default:
      return clipJob?.status === "failed" ? "上次生成失败了，可以再试一次。" : "可以现在生成这一刻的视频。";
  }
}
