"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  activeCoachingEventIds, buildCoachingReviewModel, byImportance, coachingMomentLabel, coachingRoundClock, coachingSidesByRound,
  feedbackProgress, severityFilterOptions, type FeedbackSaveState, type ReviewEvent, type ReviewEventOptions, type RuleFilter,
  type SeverityFilter
} from "@/lib/coaching-review";
import { coachingMatchesSearch, coachingSeverityLabel } from "@/lib/coaching-copy";
import { weaponName } from "@/lib/replay-events";
import type { CoachingEvent, CoachingVerdict } from "@/types/coaching";
import type { RenderJobStatus } from "@/lib/api";
import type { PlayerSide, ReplayPlayer, ReplayRound } from "@/types/replay";
import { CoachingEventCard } from "./CoachingEventCard";

interface CoachingPanelProps {
  events: CoachingEvent[];
  players: ReplayPlayer[];
  // Only used to find the cards at the playhead. Pass activeEventIds (see
  // useActiveCoachingEventIds) instead so playback frames skip this panel.
  currentTick?: number;
  activeEventIds?: ReadonlySet<string>;
  selectedRound: number;
  rounds?: ReplayRound[];
  tickRate?: number;
  selectedPlayerName?: string | null;
  renderJobByEventId: Map<string, RenderJobStatus>;
  requestingEventId: string | null;
  onSeek: (tick: number, eventId: string, origin: "card") => void;
  // Omitted when render clips are off; the cards then drop their clip button.
  onGenerateClip?: (event: CoachingEvent) => void;
  // Resolve false (or reject) when the verdict was not saved: the card then
  // says so in place and offers to re-send it. Resolve "stale" (or reject with a
  // 404) when the suggestion no longer exists: the card asks for a reload instead.
  onFeedback: (event: CoachingEvent, verdict: CoachingVerdict | null) => void | Promise<FeedbackResult>;
  // Takes the viewer to the player picker while nobody is selected.
  onChoosePlayer?: () => void;
  // The reviewed player's side per round (see playerSidesByRound); sides swap
  // at half. Without it the side comes from what each rule records.
  playerSides?: ReadonlyMap<number, PlayerSide | null>;
}

export type FeedbackResult = boolean | "stale" | void;

const NO_ACTIVE_EVENTS: ReadonlySet<string> = new Set();
const REVIEW_OPTIONS: ReviewEventOptions = { weaponLabel: weaponName };
// 本场最值得回看: this many cards, offered once the player has more than this many.
const TOP_COUNT = 5;

export const CoachingPanel = memo(function CoachingPanel({
  events, players, currentTick, activeEventIds, selectedRound, rounds, tickRate, selectedPlayerName,
  renderJobByEventId, requestingEventId, onSeek, onGenerateClip, onFeedback, onChoosePlayer, playerSides
}: CoachingPanelProps) {
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [rule, setRule] = useState<RuleFilter>("all");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState<"current" | "all">("current");
  const [inspectedEventId, setInspectedEventId] = useState<string | null>(null);
  const [expandedRoundNumbers, setExpandedRoundNumbers] = useState<Set<number>>(() => new Set([selectedRound]));
  const [feedbackStates, setFeedbackStates] = useState<ReadonlyMap<string, FeedbackSaveState>>(() => new Map());
  const feedbackRequests = useRef(new Map<string, number>());
  // Cards get stable handlers, so a page re-render with fresh closures does not re-render every card.
  const latestHandlers = useRef({ onSeek, onGenerateClip, onFeedback });
  useEffect(() => {
    latestHandlers.current = { onSeek, onGenerateClip, onFeedback };
  });

  const reviewModel = useMemo(() => {
    const model = buildCoachingReviewModel(events, players, { severity, rule, search: "" }, REVIEW_OPTIONS);
    const roundGroups = model.roundGroups.map((group) => ({
      ...group, events: group.events.filter((event) => matchesPanelSearch(event, search))
    })).filter((group) => group.events.length > 0);
    return { ...model, roundGroups, filteredCount: roundGroups.reduce((count, group) => count + group.events.length, 0) };
  }, [events, players, rule, search, severity]);
  const hasFilters = severity !== "all" || rule !== "all" || search.trim().length > 0;
  // 本场最值得回看: the whole match unfiltered, for a player with more than TOP_COUNT suggestions.
  const showTop = scope === "all" && !hasFilters && events.length > TOP_COUNT;
  const topEvents = useMemo(
    () => showTop ? byImportance(reviewModel.roundGroups.flatMap((group) => group.events)).slice(0, TOP_COUNT) : [],
    [reviewModel, showTop]
  );
  // Each card is listed once (the page finds cards by id), so the rounds below keep the rest.
  const listedGroups = useMemo(() => {
    if (topEvents.length === 0) return reviewModel.roundGroups;
    const topIds = new Set(topEvents.map((reviewEvent) => reviewEvent.event.id));
    return reviewModel.roundGroups
      .map((group) => ({ ...group, events: group.events.filter((reviewEvent) => !topIds.has(reviewEvent.event.id)) }))
      .filter((group) => group.events.length > 0);
  }, [reviewModel, topEvents]);
  const progress = useMemo(() => feedbackProgress(events), [events]);
  const recordedSides = useMemo(() => coachingSidesByRound(events), [events]);
  // A level with nothing in it is a filter that can only come back empty.
  const severityOptions = useMemo(() => severityFilterOptions(events), [events]);
  const activeIds = useMemo(
    () => activeEventIds ?? (currentTick === undefined ? NO_ACTIVE_EVENTS : activeCoachingEventIds(events, currentTick)),
    [activeEventIds, currentTick, events]
  );
  const selectedGroup = reviewModel.roundGroups.find((group) => group.roundNumber === selectedRound);
  const visibleGroups = scope === "current" ? (selectedGroup ? [selectedGroup] : []) : listedGroups;

  // Opening another round's card adds that round; groups the player opened stay open.
  useEffect(() => {
    setExpandedRoundNumbers((current) => current.has(selectedRound) ? current : new Set(current).add(selectedRound));
  }, [selectedRound]);

  const seek = useCallback((tick: number, eventId: string) => latestHandlers.current.onSeek(tick, eventId, "card"), []);
  const generateClip = useCallback((event: CoachingEvent) => latestHandlers.current.onGenerateClip?.(event), []);
  const toggleInspect = useCallback((eventId: string) => {
    setInspectedEventId((current) => current === eventId ? null : eventId);
  }, []);
  const sendFeedback = useCallback((event: CoachingEvent, verdict: CoachingVerdict | null) => {
    const request = (feedbackRequests.current.get(event.id) ?? 0) + 1;
    feedbackRequests.current.set(event.id, request);
    // Only the latest click on a card decides what that card says.
    const settle = (outcome: "saved" | "failed" | "stale") => {
      if (feedbackRequests.current.get(event.id) !== request) return;
      setFeedbackStates((current) => withFeedbackState(current, event.id, outcome === "saved" ? null : { status: outcome, verdict }));
    };
    let result: ReturnType<CoachingPanelProps["onFeedback"]>;
    try {
      result = latestHandlers.current.onFeedback(event, verdict);
    } catch (error) {
      settle(isNotFound(error) ? "stale" : "failed");
      return;
    }
    if (!result || typeof result.then !== "function") {
      settle("saved");
      return;
    }
    setFeedbackStates((current) => withFeedbackState(current, event.id, { status: "saving", verdict }));
    result.then(
      (saved) => settle(saved === "stale" ? "stale" : saved === false ? "failed" : "saved"),
      (error: unknown) => settle(isNotFound(error) ? "stale" : "failed")
    );
  }, []);
  const renderCard = (reviewEvent: ReviewEvent, showRound = false) => {
    const eventId = reviewEvent.event.id;
    const round = reviewEvent.event.round_number;
    return (
      <CoachingEventCard key={eventId} reviewEvent={reviewEvent} active={activeIds.has(eventId)} showRound={showRound}
        inspected={inspectedEventId === eventId} locationLabel={coachingMomentLabel(reviewEvent.event, rounds, tickRate)}
        clock={coachingRoundClock(reviewEvent.event, rounds, tickRate)}
        side={playerSides?.get(round) ?? recordedSides.get(round) ?? null}
        renderJob={renderJobByEventId.get(eventId)} clipRequesting={requestingEventId === eventId}
        feedbackState={feedbackStates.get(eventId)} onToggleInspect={toggleInspect} onSeek={seek}
        onGenerateClip={onGenerateClip ? generateClip : undefined} onFeedback={sendFeedback} />
    );
  };

  function changeScope(next: "current" | "all") {
    setScope(next);
    if (next === "all") {
      setExpandedRoundNumbers(new Set([selectedGroup?.roundNumber ?? reviewModel.roundGroups[0]?.roundNumber ?? selectedRound]));
    }
  }

  function toggleRound(roundNumber: number) {
    setExpandedRoundNumbers((current) => {
      const next = new Set(current);
      if (next.has(roundNumber)) next.delete(roundNumber);
      else next.add(roundNumber);
      return next;
    });
  }

  function clearFilters() {
    setSeverity("all");
    setRule("all");
    setSearch("");
  }

  return (
    <aside className="panel coaching-panel" aria-label="重点建议">
      <div className="panel-bar coaching-header">
        <h2 className="panel-bar-title">重点建议</h2>
        <div className="coaching-round-scope" role="group" aria-label="建议回合范围">
          <button className={scope === "current" ? "active" : ""} type="button" aria-pressed={scope === "current"} onClick={() => changeScope("current")}>
            当前回合{selectedGroup ? <span className="coaching-count"> {selectedGroup.events.length} 条</span> : null}
          </button>
          <button className={scope === "all" ? "active" : ""} type="button" aria-pressed={scope === "all"} onClick={() => changeScope("all")}>
            全部回合<span className="coaching-count"> {reviewModel.filteredCount} 条</span>
          </button>
        </div>
      </div>

      <div className="coaching-toolbar">
        {selectedPlayerName ? (
          <p className="coaching-header-meta">
            <strong className="coaching-header-player" title={selectedPlayerName}>{selectedPlayerName}</strong>
            <span className="coaching-header-round">第 {selectedRound} 回合</span>
            {progress.total > 0 ? <span>已评价 {progress.rated}/{progress.total}</span> : null}
          </p>
        ) : <p className="coaching-header-meta"><span className="coaching-header-round">第 {selectedRound} 回合</span></p>}
        <details className="coaching-filter-toggle">
          <summary>筛选建议{hasFilters ? <span className="coaching-filter-active">（已筛选）</span> : null}</summary>
          <div className="coaching-controls">
            <input className="coaching-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索建议、玩家或依据" aria-label="搜索建议" />
            {severityOptions.length > 1 ? (
              <div className="severity-filter" role="group" aria-label="重要程度">
                <button className={`filter-button ${severity === "all" ? "active" : ""}`} type="button" aria-pressed={severity === "all"} onClick={() => setSeverity("all")}>全部<span className="coaching-count"> {events.length}</span></button>
                {severityOptions.map((option) => (
                  <button key={option.value} className={`filter-button severity-${option.value} ${severity === option.value ? "active" : ""}`} type="button" aria-pressed={severity === option.value} onClick={() => setSeverity(option.value)}>
                    <span className="coaching-severity-mark" aria-hidden="true" />{coachingSeverityLabel(option.value)}<span className="coaching-count"> {option.count}</span>
                  </button>
                ))}
              </div>
            ) : null}
            <select className="rule-filter-select" value={rule} onChange={(event) => setRule(event.target.value as RuleFilter)} aria-label="建议类型">
              <option value="all">全部建议类型</option>
              {reviewModel.availableRules.map((availableRule) => (
                <option key={availableRule.id} value={availableRule.id}>{availableRule.label}（{availableRule.count}）</option>
              ))}
            </select>
            {hasFilters ? <button className="text-button coaching-link" type="button" onClick={clearFilters}>清除筛选</button> : null}
          </div>
        </details>
      </div>

      <div className="coaching-body">
        {topEvents.length > 0 ? (
          <section className="coaching-round-group coaching-top-group" aria-label="本场最值得回看">
            <div className="coaching-top-header">
              <strong>本场最值得回看</strong><small>按回合输赢、首个阵亡、人数劣势排序</small>
            </div>
            <div className="coaching-round-events">{topEvents.map((reviewEvent) => renderCard(reviewEvent, true))}</div>
          </section>
        ) : null}
        {visibleGroups.length === 0 && topEvents.length === 0 ? (
          <div className="coaching-empty-state">
            <p>{reviewModel.totalCount === 0 ? selectedPlayerName === null ? "选择你在这场比赛中的玩家后，这里会列出对应的建议。" : "暂未发现值得回看的时刻，可以直接观看比赛。没有建议不代表每次选择都正确。" : hasFilters && reviewModel.filteredCount === 0 ? "没有符合筛选条件的建议。" : "这一回合暂无建议，可以查看其他回合。"}</p>
            {selectedPlayerName === null && onChoosePlayer ? <button className="text-button coaching-link" type="button" onClick={onChoosePlayer}>选择玩家</button> : null}
            {scope === "current" && reviewModel.filteredCount > 0 ? <button className="text-button coaching-link" type="button" onClick={() => changeScope("all")}>查看其他回合的 {reviewModel.filteredCount} 条建议</button> : null}
            {hasFilters ? <button className="text-button coaching-link" type="button" onClick={clearFilters}>清除筛选</button> : null}
          </div>
        ) : visibleGroups.map((roundGroup) => {
          const expanded = scope === "current" || expandedRoundNumbers.has(roundGroup.roundNumber);
          const eventsId = `coaching-round-${roundGroup.roundNumber}-events`;
          return (
            <section key={roundGroup.roundNumber} className={`coaching-round-group ${roundGroup.roundNumber === selectedRound ? "selected" : ""}`} aria-label={`第 ${roundGroup.roundNumber} 回合建议`}>
              {scope === "all" ? (
                <button className="coaching-round-header" type="button" onClick={() => toggleRound(roundGroup.roundNumber)} aria-expanded={expanded} aria-controls={eventsId}>
                  <span className="coaching-round-toggle" aria-hidden="true">{expanded ? "−" : "+"}</span>
                  <strong>第 {roundGroup.roundNumber} 回合</strong><small>{roundGroup.events.length} 条建议</small>
                </button>
              ) : null}
              {expanded ? (
                <div id={eventsId} className="coaching-round-events">{roundGroup.events.map((reviewEvent) => renderCard(reviewEvent))}</div>
              ) : null}
            </section>
          );
        })}
      </div>
    </aside>
  );
});

// Also finds the Chinese facts line, chips, extra reasons and evidence values the card shows.
function matchesPanelSearch(reviewEvent: ReviewEvent, search: string): boolean {
  if (coachingMatchesSearch(reviewEvent, search)) return true;
  const needle = search.trim().toLocaleLowerCase();
  return [reviewEvent.facts ?? "", reviewEvent.feed?.killer ?? "", ...(reviewEvent.chips ?? []),
    ...(reviewEvent.extraReasonLines ?? []), ...(reviewEvent.playerEvidence ?? []).map((item) => item.value)]
    .join(" ").toLocaleLowerCase().includes(needle);
}

// A rejected save that the server answered 404 (duck-typed: the panel does not load the API client).
function isNotFound(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { status?: unknown }).status === 404;
}

function withFeedbackState(
  current: ReadonlyMap<string, FeedbackSaveState>,
  eventId: string,
  state: FeedbackSaveState | null
): ReadonlyMap<string, FeedbackSaveState> {
  if (!state && !current.has(eventId)) return current;
  const next = new Map(current);
  if (state) next.set(eventId, state);
  else next.delete(eventId);
  return next;
}
