"use client";

import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

import { timelineMarkersForRound } from "@/lib/coaching-review";
import { coachingCopy } from "@/lib/coaching-copy";
import { clusterTimelineMarkers, timelineParserEventMarkersForRound, type ParserEventTimelineMarker } from "@/lib/replay-events";
import { formatRoundTime, roundPlaybackStartTick } from "@/lib/replay-time";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayRound } from "@/types/replay";

// Event chips are 18 px wide: nearer than this they share one chip (a tick on phones, 3 px).
const EVENT_CHIP_GAP_PX = 20;
const EVENT_TICK_GAP_PX = 6;
const NARROW_LANE_PX = 480;

// One or more parser events drawn as one chip, anchored at the first.
interface ParserLaneItem {
  event: ParserEventTimelineMarker["event"];
  seekTick: number;
  leftPercent: number;
  members: ParserEventTimelineMarker[];
}

// Most telling first: my death, my kill, any kill, the rest.
function clusterTone(members: ParserEventTimelineMarker[]): string {
  if (members.some((member) => member.presentation.tone === "own-death")) return "has-own-death";
  if (members.some((member) => member.presentation.tone === "own-kill")) return "has-own-kill";
  return "";
}

// Keeps an 18 px chip whole at either end of the lane.
function laneLeft(percent: number, halfWidthPx: number): string {
  return `clamp(${halfWidthPx}px, ${percent}%, calc(100% - ${halfWidthPx}px))`;
}

interface TimelineProps {
  currentTick: number;
  selectedRound: number;
  rounds: ReplayRound[];
  events: CoachingEvent[];
  parserEvents?: ReplayEvent[];
  selectedPlayerName?: string | null;
  selectedPlayerId?: string | null;
  tickRate?: number;
  onSeek: (tick: number) => void;
  // A suggestion marker lands like "查看这一刻" (lead-in, focused card) when this is given.
  onSeekFinding?: (event: CoachingEvent) => void;
}

export const Timeline = memo(function Timeline({
  currentTick,
  selectedRound,
  rounds,
  events,
  parserEvents = [],
  selectedPlayerName,
  selectedPlayerId = null,
  tickRate = 64,
  onSeek,
  onSeekFinding
}: TimelineProps) {
  const round = rounds.find((item) => item.roundNumber === selectedRound) ?? rounds[0];
  const minTick = round?.startTick ?? 0;
  const maxTick = round?.endTick ?? 0;
  const markers = useMemo(
    () => timelineMarkersForRound(events, selectedRound, minTick, maxTick),
    [events, maxTick, minTick, selectedRound]
  );
  const parserEventMarkers = useMemo(
    () => timelineParserEventMarkersForRound(parserEvents, selectedRound, minTick, maxTick, selectedPlayerId),
    [maxTick, minTick, parserEvents, selectedPlayerId, selectedRound]
  );
  const lanesShown = rounds.length > 0;
  const tracksRef = useRef<HTMLDivElement | null>(null);
  const [laneWidth, setLaneWidth] = useState(0);
  useEffect(() => {
    const element = tracksRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setLaneWidth((previous) => (Math.abs(previous - width) < 1 ? previous : width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [lanesShown]);
  const parserLaneItems = useMemo<ParserLaneItem[]>(
    () => clusterTimelineMarkers(parserEventMarkers, laneWidth, laneWidth < NARROW_LANE_PX ? EVENT_TICK_GAP_PX : EVENT_CHIP_GAP_PX)
      .map((members) => ({ event: members[0].event, seekTick: members[0].seekTick, leftPercent: members[0].leftPercent, members })),
    [laneWidth, parserEventMarkers]
  );
  const hasRounds = rounds.length > 0;
  const span = Math.max(1, maxTick - minTick);
  const currentTickPercent = hasRounds
    ? Math.max(0, Math.min(100, ((currentTick - minTick) / span) * 100))
    : 0;
  const freezeEndPercent = round ? Math.max(0, Math.min(100, ((roundPlaybackStartTick(round) - minTick) / span) * 100)) : 0;
  const stackStyle = { "--timeline-freeze-end": `${freezeEndPercent}%` } as CSSProperties;
  // The playhead moves every playback frame. Only the two elements drawn from it (the spine and
  // the slider track) carry the variable: on the lane stack it restyled every marker per frame.
  const playheadStyle = { "--timeline-current-tick": `${currentTickPercent}%` } as CSSProperties;
  const safeTickRate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  const clock = (tick: number) => formatRoundTime((tick - minTick) / safeTickRate);
  const elapsed = clock(Math.min(maxTick, Math.max(minTick, currentTick)));
  const duration = formatRoundTime((maxTick - minTick) / safeTickRate);
  const seekBy = (deltaSeconds: number) =>
    onSeek(Math.min(maxTick, Math.max(minTick, currentTick + deltaSeconds * safeTickRate)));

  function handleSliderKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // Matches the page shortcuts: arrows move 5 s, Shift + arrow 1 s.
    const step = event.shiftKey ? 1 : 5;
    const moves: Record<string, number> = {
      ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step, PageDown: -10, PageUp: 10
    };
    if (event.key in moves) {
      event.preventDefault();
      seekBy(moves[event.key]);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      onSeek(event.key === "Home" ? minTick : maxTick);
    }
  }

  return (
    <section className="timeline-panel evidence-timeline" aria-label="回合时间轴">
      <div className="timeline-heading">
        <div>
          <strong>第 {selectedRound} 回合 · {elapsed} <span>/ {duration}</span></strong>
          <span>{parserEventMarkers.length} 个事件 · {markers.length} 条建议</span>
          {selectedPlayerName !== undefined ? (
            <small className="visually-hidden">{selectedPlayerName ? `${selectedPlayerName} 的事件和建议，保留炸弹与回合事件` : "选择玩家后显示个人事件；当前显示比赛事件"}</small>
          ) : null}
        </div>
      </div>

      {hasRounds ? (
        <div className="timeline-lane-stack" style={stackStyle}>
          <div className="timeline-lane-labels" aria-hidden="true">
            <div className="timeline-lane-label">
              <span>回合</span>
              <small>{selectedRound}</small>
            </div>
            <div className="timeline-lane-label">
              <span>事件</span>
              <small>{parserEventMarkers.length}</small>
            </div>
            <div className="timeline-lane-label">
              <span>建议</span>
              <small>{markers.length}</small>
            </div>
          </div>
          <div className="timeline-lane-tracks" ref={tracksRef}>
            <div className="timeline-current-spine" aria-hidden="true" style={playheadStyle}>
              <span>{elapsed}</span>
            </div>
            <div className="timeline-lane-track round-lane">
              <button
                className="round-boundary-marker start"
                type="button"
                onClick={() => onSeek(minTick)}
                aria-label={`开始：跳到第 ${selectedRound} 回合开始`}
                title="回合开始 · 0:00"
              >
                开始
              </button>
              <span className="round-duration-track" aria-hidden="true" />
              <button
                className="round-boundary-marker end"
                type="button"
                onClick={() => onSeek(maxTick)}
                aria-label={`结束：跳到第 ${selectedRound} 回合结束`}
                title={`回合结束 · ${duration}`}
              >
                结束
              </button>
            </div>
            <MarkerLane
              className="timeline-lane-track parser-lane parser-event-markers"
              label="比赛事件"
              currentTick={currentTick}
              markers={parserLaneItems}
              markerTick={(item) => item.seekTick}
              renderMarker={(item, tabIndex, register) => {
                if (item.members.length === 1) {
                  const marker = item.members[0];
                  const text = `${marker.description} · ${clock(marker.seekTick)}`;
                  return (
                    <button
                      key={marker.event.id}
                      ref={register}
                      className={`parser-event-marker ${marker.presentation.tone}${marker.side ? ` side-${marker.side.toLowerCase()}` : ""}`}
                      style={{ left: laneLeft(marker.leftPercent, 9) }}
                      type="button"
                      tabIndex={tabIndex}
                      onClick={() => onSeek(marker.seekTick)}
                      aria-label={`${marker.presentation.label}：${text}`}
                      title={text}
                    >
                      {marker.presentation.shortLabel}
                    </button>
                  );
                }
                // Events too close to tell apart: one chip with their count; it lands on the first.
                const lines = item.members.map((member) => `${member.description} · ${clock(member.seekTick)}`);
                const tone = clusterTone(item.members);
                return (
                  <button
                    key={item.event.id}
                    ref={register}
                    className={`parser-event-marker cluster${tone ? ` ${tone}` : ""}`}
                    style={{ left: laneLeft(item.leftPercent, 9) }}
                    type="button"
                    tabIndex={tabIndex}
                    onClick={() => onSeek(item.seekTick)}
                    aria-label={`${item.members.length} 个事件：${lines.join("；")}`}
                    title={lines.join("\n")}
                  >
                    {item.members.length}
                  </button>
                );
              }}
            />
            <MarkerLane
              className="timeline-lane-track coaching-lane event-markers"
              label="建议"
              currentTick={currentTick}
              markers={markers}
              markerTick={(marker) => marker.event.tick_start}
              renderMarker={(marker, tabIndex, register) => {
                const text = `建议：${coachingCopy(marker.event).title} · ${clock(marker.event.tick_start)}`;
                return (
                  <button
                    key={marker.event.id}
                    ref={register}
                    className={`event-marker ${marker.event.severity}`}
                    style={{ left: laneLeft(marker.leftPercent, 4) }}
                    type="button"
                    tabIndex={tabIndex}
                    onClick={() => (onSeekFinding ? onSeekFinding(marker.event) : onSeek(marker.event.tick_start))}
                    aria-label={text}
                    title={text}
                  />
                );
              }}
            />
          </div>
        </div>
      ) : (
        <div className="timeline-empty-state">这场比赛暂时没有可用的回合时间轴。</div>
      )}

      {hasRounds ? (
        <div className="timeline-scrubber">
          <input
            className="timeline-slider"
            style={playheadStyle}
            type="range"
            min={minTick}
            max={maxTick}
            step={1}
            value={Math.min(maxTick, Math.max(minTick, currentTick))}
            onChange={(event) => onSeek(Number(event.target.value))}
            onKeyDown={handleSliderKeyDown}
            aria-label="拖动定位回放"
            aria-valuetext={`第 ${selectedRound} 回合 ${elapsed}，共 ${duration}`}
          />
        </div>
      ) : null}
    </section>
  );
});

// One Tab stop per lane: the marker at or before the playhead is reachable, arrows move between markers.
function MarkerLane<T extends { event: { id: string } }>({
  className,
  label,
  currentTick,
  markers,
  markerTick,
  renderMarker
}: {
  className: string;
  label: string;
  currentTick: number;
  markers: T[];
  markerTick: (marker: T) => number;
  renderMarker: (marker: T, tabIndex: number, register: (element: HTMLButtonElement | null) => void) => JSX.Element;
}) {
  // Index of the marker holding focus; -1 while focus is elsewhere, so the playhead picks the stop.
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  let rovingIndex = focusedIndex >= 0 && focusedIndex < markers.length ? focusedIndex : -1;
  if (rovingIndex < 0) {
    rovingIndex = 0;
    for (let index = 0; index < markers.length; index += 1) {
      if (markerTick(markers[index]) <= currentTick + 0.5) rovingIndex = index;
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (markers.length === 0 || event.altKey || event.ctrlKey || event.metaKey) return;
    const index = buttons.current.findIndex((button) => button === event.target);
    if (index < 0) return;
    const next = event.key === "ArrowLeft" ? index - 1 : event.key === "ArrowRight" ? index + 1
      : event.key === "Home" ? 0 : event.key === "End" ? markers.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    const target = Math.max(0, Math.min(markers.length - 1, next));
    setFocusedIndex(target);
    buttons.current[target]?.focus();
  }

  buttons.current.length = markers.length;
  return (
    <div className={className} role="group" aria-label={label} onKeyDown={handleKeyDown}
      onFocus={(event) => {
        const target: EventTarget = event.target;
        setFocusedIndex(buttons.current.findIndex((button) => button === target));
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusedIndex(-1);
      }}>
      {markers.map((marker, index) => renderMarker(marker, index === rovingIndex ? 0 : -1, (element) => {
        buttons.current[index] = element;
      }))}
    </div>
  );
}
