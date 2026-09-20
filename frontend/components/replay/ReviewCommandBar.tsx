"use client";

import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";

import type { CoachingEvent } from "@/types/coaching";

export function ReviewCommandBar({
  mapName,
  selectedRound,
  currentTick,
  roundTime,
  currentPovName,
  playing,
  speed,
  previousFinding,
  nextFinding,
  onTogglePlay,
  onSpeedChange,
  onPreviousFinding,
  onNextFinding
}: {
  mapName: string;
  selectedRound: number;
  currentTick: number;
  roundTime: string;
  currentPovName: string;
  playing: boolean;
  speed: number;
  previousFinding: CoachingEvent | null;
  nextFinding: CoachingEvent | null;
  onTogglePlay: () => void;
  onSpeedChange: (speed: number) => void;
  onPreviousFinding: () => void;
  onNextFinding: () => void;
}) {
  return (
    <section className="review-command-bar" aria-label="Review transport" title={`${mapName} · tick ${Math.round(currentTick)}`}>
      <div className="review-command-coordinate">
        <div className="review-transport-readouts">
          <TransportReadout label="回合" value={`第 ${selectedRound} 回合`} />
          <TransportReadout label="时间" value={roundTime} emphasis />
          <TransportReadout label="玩家" value={currentPovName} />
        </div>
      </div>
      <div className="review-command-actions">
        <button
          className="primary-button compact-button coordinate-play-button"
          type="button"
          onClick={onTogglePlay}
          aria-label={playing ? "Pause replay" : "Play replay"}
        >
          {playing ? <Pause size={17} /> : <Play size={17} />}
          <span>{playing ? "暂停" : "播放"}</span>
        </button>
        <label className="review-speed-control">
          <span>倍速</span>
          <select
            className="speed-select"
            value={speed}
            onChange={(event) => onSpeedChange(Number(event.target.value))}
            aria-label="Playback speed"
          >
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
        </label>
        <div className="review-finding-navigation" aria-label="Finding navigation">
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onPreviousFinding}
            disabled={!previousFinding}
            title={previousFinding ? "查看上一条建议" : "已经是第一条建议"}
          >
            <ChevronLeft size={15} aria-hidden="true" />
            <span>上一条</span>
          </button>
          <button
            className="secondary-button compact-button"
            type="button"
            onClick={onNextFinding}
            disabled={!nextFinding}
            title={nextFinding ? "查看下一条建议" : "没有下一条建议"}
          >
            <span>下一条</span>
            <ChevronRight size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
    </section>
  );
}

function TransportReadout({
  label,
  value,
  emphasis = false
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <span className={`review-transport-readout ${emphasis ? "emphasis" : ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}
