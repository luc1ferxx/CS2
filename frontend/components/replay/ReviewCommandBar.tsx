"use client";

import { ChevronLeft, ChevronRight, Keyboard, Pause, Play, RotateCcw, RotateCw, SkipForward } from "lucide-react";
import { memo } from "react";

import type { CoachingEvent } from "@/types/coaching";

export const REVIEW_SHORTCUTS: ReadonlyArray<[keys: string, action: string]> = [
  ["空格 / K", "播放或暂停"],
  ["← / →", "后退或前进 5 秒"],
  ["Shift + ← / →", "后退或前进 1 秒"],
  ["[ / ]", "上一回合 / 下一回合"],
  ["P / N", "上一条 / 下一条建议"],
  ["?", "显示或隐藏快捷键"]
];

export const ReviewCommandBar = memo(function ReviewCommandBar({
  selectedRound,
  roundTime,
  roundDuration,
  currentPovName,
  playing,
  speed,
  previousFinding,
  nextFinding,
  nextRoundNumber = null,
  shortcutsOpen = false,
  onTogglePlay,
  onSpeedChange,
  onPreviousFinding,
  onNextFinding,
  onSeekBy,
  onNextRound,
  onToggleShortcuts
}: {
  selectedRound: number;
  roundTime: string;
  // The round's length, shown after the clock as "1:12 / 1:55".
  roundDuration?: string;
  currentPovName: string;
  playing: boolean;
  speed: number;
  previousFinding: CoachingEvent | null;
  nextFinding: CoachingEvent | null;
  // Set only when the playhead sits at the end of a round that has a successor.
  nextRoundNumber?: number | null;
  shortcutsOpen?: boolean;
  onTogglePlay: () => void;
  onSpeedChange: (speed: number) => void;
  onPreviousFinding: () => void;
  onNextFinding: () => void;
  onSeekBy?: (seconds: number) => void;
  onNextRound?: () => void;
  onToggleShortcuts?: () => void;
}) {
  return (
    <section className="review-command-bar" aria-label="播放控制">
      <div className="review-command-actions">
        {onSeekBy ? (
          <button className="ghost-button compact-button review-seek-button" type="button"
            onClick={() => onSeekBy(-5)} aria-label="后退 5 秒" title="后退 5 秒（←）">
            <RotateCcw size={15} aria-hidden="true" />
          </button>
        ) : null}
        {nextRoundNumber !== null && onNextRound ? (
          <button className="primary-button compact-button coordinate-play-button" type="button" onClick={onNextRound}
            title="从下一回合冻结时间结束处继续播放">
            <SkipForward size={17} aria-hidden="true" />
            <span>下一回合</span>
          </button>
        ) : (
          <button className="primary-button compact-button coordinate-play-button" type="button" onClick={onTogglePlay}
            title={playing ? "暂停（空格）" : "播放（空格）"}>
            {playing ? <Pause size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
            <span>{playing ? "暂停" : "播放"}</span>
          </button>
        )}
        {onSeekBy ? (
          <button className="ghost-button compact-button review-seek-button" type="button"
            onClick={() => onSeekBy(5)} aria-label="前进 5 秒" title="前进 5 秒（→）">
            <RotateCw size={15} aria-hidden="true" />
          </button>
        ) : null}
        <label className="review-speed-control">
          <span>倍速</span>
          <select
            className="speed-select"
            data-review-shortcuts=""
            value={speed}
            onChange={(event) => onSpeedChange(Number(event.target.value))}
            aria-label="播放倍速"
          >
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
        </label>
        <span className="review-clock">
          <strong>{roundTime}</strong>
          {roundDuration ? <span> / {roundDuration}</span> : null}
        </span>
      </div>
      <div className="review-command-coordinate">
        <div className="review-transport-readouts">
          <TransportReadout label="回合" value={`第 ${selectedRound} 回合`} />
          <TransportReadout label="玩家" value={currentPovName} />
        </div>
      </div>
      <div className="review-finding-navigation" role="group" aria-label="建议导航">
        <button
          className="secondary-button compact-button"
          type="button"
          onClick={onPreviousFinding}
          disabled={!previousFinding}
          title={previousFinding ? "查看上一条建议（P）" : "已经是第一条建议"}
        >
          <ChevronLeft size={15} aria-hidden="true" />
          <span>上一条</span>
        </button>
        <button
          className="secondary-button compact-button"
          type="button"
          onClick={onNextFinding}
          disabled={!nextFinding}
          title={nextFinding ? "查看下一条建议（N）" : "没有下一条建议"}
        >
          <span>下一条</span>
          <ChevronRight size={15} aria-hidden="true" />
        </button>
      </div>
      {onToggleShortcuts ? (
        <button className="ghost-button compact-button review-shortcuts-button" type="button"
          onClick={onToggleShortcuts} aria-expanded={shortcutsOpen} aria-controls="review-shortcuts" title="快捷键（?）">
          <Keyboard size={15} aria-hidden="true" />
          <span className="visually-hidden">快捷键</span>
        </button>
      ) : null}
      {shortcutsOpen ? (
        <dl id="review-shortcuts" className="review-shortcuts" aria-label="快捷键">
          {REVIEW_SHORTCUTS.map(([keys, action]) => (
            <div key={keys}><dt>{keys}</dt><dd>{action}</dd></div>
          ))}
        </dl>
      ) : null}
    </section>
  );
});

function TransportReadout({ label, value }: { label: string; value: string }) {
  return (
    <span className="review-transport-readout">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}
