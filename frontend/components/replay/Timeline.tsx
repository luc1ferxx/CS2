"use client";

import type { CSSProperties } from "react";

import { timelineMarkersForRound } from "@/lib/coaching-review";
import { timelineParserEventMarkersForRound } from "@/lib/replay-events";
import type { CoachingEvent } from "@/types/coaching";
import type { ReplayEvent, ReplayRound } from "@/types/replay";

interface TimelineProps {
  currentTick: number;
  selectedRound: number;
  rounds: ReplayRound[];
  events: CoachingEvent[];
  parserEvents?: ReplayEvent[];
  selectedPlayerName?: string | null;
  tickRate?: number;
  onSeek: (tick: number) => void;
}

export function Timeline({
  currentTick,
  selectedRound,
  rounds,
  events,
  parserEvents = [],
  selectedPlayerName,
  tickRate = 64,
  onSeek
}: TimelineProps) {
  const round = rounds.find((item) => item.roundNumber === selectedRound) ?? rounds[0];
  const minTick = round?.startTick ?? 0;
  const maxTick = round?.endTick ?? 0;
  const markers = timelineMarkersForRound(events, selectedRound, minTick, maxTick);
  const parserEventMarkers = timelineParserEventMarkersForRound(
    parserEvents,
    selectedRound,
    minTick,
    maxTick
  );
  const hasRounds = rounds.length > 0;
  const currentTickPercent = hasRounds
    ? Math.max(0, Math.min(100, ((currentTick - minTick) / Math.max(1, maxTick - minTick)) * 100))
    : 0;
  const timelineStyle = { "--timeline-current-tick": `${currentTickPercent}%` } as CSSProperties;
  const safeTickRate = Number.isFinite(tickRate) && tickRate > 0 ? tickRate : 64;
  const elapsed = formatTimelineTime((Math.min(maxTick, Math.max(minTick, currentTick)) - minTick) / safeTickRate);
  const duration = formatTimelineTime((maxTick - minTick) / safeTickRate);

  return (
    <section className="timeline-panel evidence-timeline" aria-label="Replay timeline">
      <div className="timeline-heading">
        <div>
          <strong title={`Tick ${Math.round(currentTick)}`}>第 {selectedRound} 回合 · {elapsed} <span>/ {duration}</span></strong>
          <span>{parserEventMarkers.length} 个事件 · {markers.length} 条建议</span>
          {selectedPlayerName !== undefined ? (
            <small className="visually-hidden">{selectedPlayerName ? `${selectedPlayerName} 的事件和建议，保留炸弹与回合事件` : "选择玩家后显示个人事件；当前显示比赛事件"}</small>
          ) : null}
        </div>
      </div>

      {hasRounds ? (
        <div className="timeline-lane-stack" style={timelineStyle}>
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
          <div className="timeline-lane-tracks">
            <div className="timeline-current-spine" aria-hidden="true">
              <span>{elapsed}</span>
            </div>
            <div className="timeline-lane-track round-lane">
              <button
                className="round-boundary-marker start"
                type="button"
                onClick={() => onSeek(minTick)}
                aria-label={`Jump to round ${selectedRound} start at tick ${minTick}`}
                title={`回合开始 · Tick ${minTick}`}
              >
                开始
              </button>
              <span className="round-duration-track" aria-hidden="true" />
              <button
                className="round-boundary-marker end"
                type="button"
                onClick={() => onSeek(maxTick)}
                aria-label={`Jump to round ${selectedRound} end at tick ${maxTick}`}
                title={`回合结束 · Tick ${maxTick}`}
              >
                结束
              </button>
            </div>
            <div className="timeline-lane-track parser-lane parser-event-markers" aria-label="Parser event markers">
              {parserEventMarkers.map((marker) => (
                <button
                  key={marker.event.id}
                  className={`parser-event-marker ${marker.presentation.tone}`}
                  style={{ left: `${marker.leftPercent}%` }}
                  type="button"
                  onClick={() => onSeek(marker.seekTick)}
                  aria-label={`Jump to ${marker.presentation.label} parser event at tick ${marker.seekTick}`}
                  title={`${parserEventLabel(marker.event.type)} · ${formatTimelineTime((marker.seekTick - minTick) / safeTickRate)} · Tick ${marker.seekTick}`}
                >
                  {parserEventLabel(marker.event.type).slice(0, 1)}
                </button>
              ))}
            </div>
            <div className="timeline-lane-track coaching-lane event-markers" aria-label="Coaching event markers">
              {markers.map((marker) => (
                <button
                  key={marker.event.id}
                  className={`event-marker ${marker.event.severity}`}
                  style={{ left: `${marker.leftPercent}%` }}
                  type="button"
                  onClick={() => onSeek(marker.event.tick_start)}
                  aria-label={`Jump to ${marker.event.severity} coaching event at tick ${marker.event.tick_start}`}
                  title={`${marker.event.title} · ${formatTimelineTime((marker.event.tick_start - minTick) / safeTickRate)} · Tick ${marker.event.tick_start}`}
                >
                  <span className="visually-hidden">{marker.event.title}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="timeline-empty-state">这场比赛暂时没有可用的回合时间轴。</div>
      )}

      {hasRounds ? (
        <div className="timeline-scrubber">
          <input
            className="timeline-slider"
            type="range"
            min={minTick}
            max={maxTick}
            step={1}
            value={Math.min(maxTick, Math.max(minTick, currentTick))}
            onChange={(event) => onSeek(Number(event.target.value))}
            aria-label="Seek replay"
            aria-valuetext={`第 ${selectedRound} 回合 ${elapsed}，共 ${duration}`}
          />
        </div>
      ) : null}
    </section>
  );
}

function formatTimelineTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
}

function parserEventLabel(type: ReplayEvent["type"]): string {
  return {
    kill: "击杀", death: "阵亡", damage: "伤害", bomb_pickup: "拾取炸弹", bomb_dropped: "丢下炸弹",
    bomb_planted: "安装炸弹", bomb_defused: "拆除炸弹", bomb_exploded: "炸弹爆炸",
    smoke: "烟雾弹", flash: "闪光弹", molotov: "燃烧弹", he: "手雷", round_start: "回合开始", round_end: "回合结束"
  }[type] ?? "事件";
}
