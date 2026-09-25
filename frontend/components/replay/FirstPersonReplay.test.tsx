import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FirstPersonReplay } from "@/components/replay/FirstPersonReplay";
import { replayData, replayVideo } from "@/lib/test-fixtures/review";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ refreshSession: vi.fn() })
}));

// Matches the paused position, so the paused-resync effect has nothing to correct.
const PAUSED_TICK = 42.5 * 64;

const replay = replayData({
  video: replayVideo({
    status: "ready",
    source: "manual_upload",
    url: "/demos/demo-1/media/video",
    durationSeconds: 60,
    tickStart: 0,
    tickEnd: 64 * 60
  })
});

function mount(onVideoTimeChange?: (seconds: number) => void) {
  return (
    <FirstPersonReplay
      replay={replay}
      currentTick={PAUSED_TICK}
      playing={false}
      speed={1}
      playbackState="active"
      mediaUnavailable={false}
      renderRequesting={false}
      renderClipRequesting={false}
      latestRenderClipJob={null}
      onRequestMockRender={vi.fn()}
      onVideoTickChange={vi.fn()}
      onVideoUnavailable={vi.fn()}
      onViewVideoClip={vi.fn()}
      onVideoTimeChange={onVideoTimeChange}
    />
  );
}

describe("FirstPersonReplay", () => {
  it("reports the paused video position when a time listener attaches later", () => {
    const { container, rerender } = render(mount());
    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    // The developer scrubbed and paused while no listener was attached; a paused video fires no timeupdate.
    Object.defineProperty(video!, "currentTime", { configurable: true, get: () => 42.5, set: () => {} });

    const onVideoTimeChange = vi.fn();
    rerender(mount(onVideoTimeChange));

    expect(onVideoTimeChange).toHaveBeenLastCalledWith(42.5);
  });

  it("labels the dev mock scene as a placeholder without a middle-dot meta string", () => {
    const { container } = render(
      <FirstPersonReplay
        replay={replayData()}
        currentTick={500}
        playing={false}
        speed={1}
        playbackState="unavailable"
        mediaUnavailable={false}
        renderRequesting={false}
        renderClipRequesting={false}
        latestRenderClipJob={null}
        onRequestMockRender={vi.fn()}
        onVideoTickChange={vi.fn()}
        onVideoUnavailable={vi.fn()}
        onViewVideoClip={vi.fn()}
      />
    );

    expect(container.querySelector(".mock-render-label")).toHaveTextContent("模拟占位画面，非真实游戏画面");
    expect(container.querySelector(".mock-render-label")?.textContent).not.toContain("·");
  });
});
