"use client";

// 道具投掷分析 panel. Layout adapted from bugkingZHT/cs2-sandbox GrenadeAnalyzeOverlay.vue (MIT,
// Copyright (c) 2026 huN7er): the thrower row, the throw-style tag, the live key panel, the mini
// timeline (0.25x default, red release marker) and the copy-position command, in one card with the
// reference's black translucent look and blue accents (styles in product.css, "S17 THROW ANALYSIS").
import { Pause, Play, X } from "lucide-react";
import {
  memo,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent
} from "react";

import { KeyboardOverlay } from "@/components/replay/KeyboardOverlay";
import { decodeButtons } from "@/lib/player-inputs";
import { THROW_BUTTON_LABELS, formatRelativeSeconds, type ThrowAnalysis } from "@/lib/throw-analysis";
import type { ThrowPlaybackSpeed } from "@/lib/use-throw-playback";
import { UTILITY_LABELS } from "@/lib/utility";
import type { ReplayUtility } from "@/types/replay";

export interface ThrowAnalysisPanelProps {
  utility: ReplayUtility;
  analysis: ThrowAnalysis;
  throwerName: string;
  tickRate: number;
  /** The local playback tick (fractional while playing). */
  tick: number;
  playing: boolean;
  speed: ThrowPlaybackSpeed;
  zoomed: boolean;
  /** inputMaskAt(replay, throwerId, Math.floor(tick)); null shows 这场比赛没有按键记录 instead of the keys. */
  keyMask: number | null;
  onTogglePlay: () => void;
  onSpeedChange: (speed: ThrowPlaybackSpeed) => void;
  onSeek: (tick: number) => void;
  onZoomChange: (zoomed: boolean) => void;
  /** ✕: back to the list. */
  onClose: () => void;
  /** 在战术回放里看: the page seeks before the throw and shows 战术回放. */
  onWatchInReplay: () => void;
}

const SPEEDS: readonly ThrowPlaybackSpeed[] = [0.25, 0.5, 1];
const COPY_FEEDBACK_MS = 1500;
const COPY_LABELS = { idle: "复制站位指令", copied: "已复制", failed: "复制失败" } as const;

export const ThrowAnalysisPanel = memo(function ThrowAnalysisPanel({
  utility,
  analysis,
  throwerName,
  tickRate,
  tick,
  playing,
  speed,
  zoomed,
  keyMask,
  onTogglePlay,
  onSpeedChange,
  onSeek,
  onZoomChange,
  onClose,
  onWatchInReplay
}: ThrowAnalysisPanelProps) {
  const rate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  const { windowStart, windowEnd, releaseTick, utilityId, command } = analysis;
  const span = Math.max(1, windowEnd - windowStart);
  const percentOf = (value: number) => Math.min(100, Math.max(0, ((value - windowStart) / span) * 100));
  const progress = percentOf(tick);
  const releasePercent = percentOf(releaseTick);
  const relative = formatRelativeSeconds(tick - releaseTick, rate);

  const trackRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<number | null>(null);

  // Copy feedback belongs to the throw it was for, so another throw opens on the plain label.
  const [copied, setCopied] = useState<{ id: string; state: "copied" | "failed" } | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentIdRef = useRef(utilityId);
  currentIdRef.current = utilityId;
  useEffect(() => () => {
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
  }, []);
  const copyState = copied && copied.id === utilityId ? copied.state : "idle";

  async function copyCommand() {
    if (!command) return;
    const id = utilityId;
    let ok = false;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command);
        ok = true;
      }
    } catch {
      ok = false;
    }
    if (currentIdRef.current !== id) return;
    setCopied({ id, state: ok ? "copied" : "failed" });
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopied(null), COPY_FEEDBACK_MS);
  }

  function tickAt(clientX: number): number | null {
    const box = trackRef.current?.getBoundingClientRect();
    if (!box || box.width <= 0) return null;
    const share = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    return Math.round(windowStart + share * (windowEnd - windowStart));
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    trackRef.current?.focus();
    dragRef.current = event.pointerId;
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // A pointer that is already gone cannot be captured; the click still seeks.
    }
    const value = tickAt(event.clientX);
    if (value !== null) onSeek(value);
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current !== event.pointerId) return;
    const value = tickAt(event.clientX);
    if (value !== null) onSeek(value);
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current !== event.pointerId) return;
    dragRef.current = null;
    try {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    } catch {
      // Already released.
    }
  }

  function handleTrackKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    const current = Math.round(tick);
    let next: number | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = current - 1;
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") next = current + 1;
    else if (event.key === "Home") next = windowStart;
    else if (event.key === "End") next = windowEnd;
    if (next === null) return;
    event.preventDefault();
    onSeek(Math.min(windowEnd, Math.max(windowStart, next)));
  }

  const side = utility.throwerSide === "T" ? "t" : utility.throwerSide === "CT" ? "ct" : "unknown";
  const initial = (Array.from(throwerName.trim())[0] ?? "?").toUpperCase();

  return (
    <aside className="throw-analysis-panel" aria-label="道具投掷分析">
      <div className="throw-analysis-head">
        <span className={`throw-analysis-avatar side-${side}`} aria-hidden="true">{initial}</span>
        <span className="throw-analysis-name">{throwerName}</span>
        <span className="throw-analysis-kind">{UTILITY_LABELS[utility.type]}</span>
        <button type="button" className="throw-analysis-close" aria-label="关闭分析" title="返回列表" onClick={onClose}>
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>

      <div className="throw-analysis-row">
        <div className="throw-analysis-segments" role="group" aria-label="地图范围">
          <button type="button" aria-pressed={zoomed} onClick={() => onZoomChange(true)}>放大</button>
          <button type="button" aria-pressed={!zoomed} onClick={() => onZoomChange(false)}>全图</button>
        </div>
        {analysis.style ? <span className="throw-analysis-tag" title="扔法">{analysis.style}</span> : null}
        {analysis.button ? <span className="throw-analysis-tag" title="出手按键">{THROW_BUTTON_LABELS[analysis.button]}</span> : null}
      </div>

      <div className="throw-analysis-keys">
        {keyMask !== null
          ? <KeyboardOverlay {...decodeButtons(keyMask)} playerName={throwerName} embedded />
          : <p className="throw-analysis-muted">这场比赛没有按键记录</p>}
      </div>

      <div className="throw-analysis-timeline">
        <div className="throw-analysis-controls">
          <button type="button" className="throw-analysis-play" aria-label={playing ? "暂停" : "播放"} onClick={onTogglePlay}>
            {playing
              ? <Pause size={13} strokeWidth={2.4} aria-hidden="true" />
              : <Play size={13} strokeWidth={2.4} aria-hidden="true" />}
          </button>
          <div className="throw-analysis-segments" role="group" aria-label="慢放速度">
            {SPEEDS.map((value) => (
              <button key={value} type="button" aria-pressed={speed === value} onClick={() => onSpeedChange(value)}>
                {value}x
              </button>
            ))}
          </div>
          <span className="throw-analysis-time" aria-hidden="true">{relative}</span>
        </div>
        <div
          ref={trackRef}
          className="throw-analysis-track"
          role="slider"
          tabIndex={0}
          aria-label="出手前后时间"
          aria-valuemin={windowStart}
          aria-valuemax={windowEnd}
          aria-valuenow={Math.round(tick)}
          aria-valuetext={relative}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyDown={handleTrackKey}
        >
          <span className="throw-analysis-fill" style={{ width: `${round2(progress)}%` }} />
          <span className="throw-analysis-release" style={{ left: `${round2(releasePercent)}%` }} title="出手时刻" />
          <span className="throw-analysis-handle" style={{ left: `${round2(progress)}%` }} />
        </div>
        <div className="throw-analysis-scale" aria-hidden="true">
          <span>{formatRelativeSeconds(windowStart - releaseTick, rate, 1)}</span>
          <span className="throw-analysis-scale-release" style={{ left: `${round2(Math.min(85, Math.max(15, releasePercent)))}%` }}>出手</span>
          <span>{formatRelativeSeconds(windowEnd - releaseTick, rate, 1)}</span>
        </div>
      </div>

      {command ? (
        <div className="throw-analysis-command">
          <button type="button" className={`throw-analysis-button copy-${copyState}`} onClick={copyCommand}>
            <span aria-live="polite">{COPY_LABELS[copyState]}</span>
          </button>
          <code className="throw-analysis-code">{command}</code>
        </div>
      ) : (
        <p className="throw-analysis-muted">这场比赛还在补充站位数据</p>
      )}

      <div className="throw-analysis-foot">
        {analysis.hint ? <p className="throw-analysis-hint">{analysis.hint}</p> : null}
        <button type="button" className="throw-analysis-button throw-analysis-watch" onClick={onWatchInReplay}>
          在战术回放里看
        </button>
      </div>
    </aside>
  );
});

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
