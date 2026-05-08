"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import {
  buildCoachingReviewModel,
  type RuleFilter,
  type SeverityFilter
} from "@/lib/coaching-review";
import type { CoachingEvent } from "@/types/coaching";
import type { RenderJobStatus } from "@/lib/api";
import type { ReplayPlayer } from "@/types/replay";
import { CoachingEventCard } from "./CoachingEventCard";

interface CoachingPanelProps {
  events: CoachingEvent[];
  players: ReplayPlayer[];
  currentTick: number;
  selectedRound: number;
  renderJobByEventId: Map<string, RenderJobStatus>;
  requestingEventId: string | null;
  onSeek: (tick: number) => void;
  onGenerateClip: (event: CoachingEvent) => void;
}

export function CoachingPanel({
  events,
  players,
  currentTick,
  selectedRound,
  renderJobByEventId,
  requestingEventId,
  onSeek,
  onGenerateClip
}: CoachingPanelProps) {
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [rule, setRule] = useState<RuleFilter>("all");
  const [search, setSearch] = useState("");
  const reviewModel = useMemo(
    () => buildCoachingReviewModel(events, players, { severity, rule, search }),
    [events, players, rule, search, severity]
  );
  const activeEventIds = useMemo(
    () =>
      new Set(
        events
          .filter(
            (event) =>
              currentTick >= event.tick_start - 128 && currentTick <= event.tick_end + 128
          )
          .map((event) => event.id)
      ),
    [currentTick, events]
  );
  const activeCount = activeEventIds.size;

  return (
    <aside className="panel coaching-panel" aria-label="Coaching panel">
      <div className="coaching-header">
        <div>
          <h2>Coaching</h2>
          <p>{reviewModel.filteredCount} of {reviewModel.totalCount} events</p>
        </div>
        <div className="coaching-header-pills">
          <span className="mini-pill">Round {selectedRound}</span>
          <span className="mini-pill">
            {activeCount > 0 ? `${activeCount} current` : "No current flag"}
          </span>
        </div>
      </div>

      <div className="coaching-controls">
        <label className="coaching-search">
          <Search size={15} aria-hidden="true" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search player, title, rule"
            aria-label="Search coaching events"
          />
        </label>

        <div className="severity-filter" aria-label="Severity filter">
          {SEVERITY_FILTERS.map((filter) => (
            <button
              key={filter}
              className={`filter-button ${severity === filter ? "active" : ""}`}
              type="button"
              onClick={() => setSeverity(filter)}
            >
              {filter}
            </button>
          ))}
        </div>

        <select
          className="rule-filter-select"
          value={rule}
          onChange={(event) => setRule(event.target.value as RuleFilter)}
          aria-label="Rule filter"
        >
          <option value="all">All rules</option>
          {reviewModel.availableRules.map((availableRule) => (
            <option key={availableRule.id} value={availableRule.id}>
              {availableRule.label} ({availableRule.count})
            </option>
          ))}
        </select>
      </div>

      <div className="coaching-body">
        {reviewModel.roundGroups.length === 0 ? (
          <div className="loading-panel">No coaching events match these filters.</div>
        ) : (
          reviewModel.roundGroups.map((roundGroup) => (
            <section
              key={roundGroup.roundNumber}
              className={`coaching-round-group ${
                roundGroup.roundNumber === selectedRound ? "selected" : ""
              }`}
              aria-label={`Round ${roundGroup.roundNumber} coaching events`}
            >
              <div className="coaching-round-header">
                <h3>Round {roundGroup.roundNumber}</h3>
                <span>{roundGroup.events.length}</span>
              </div>
              <div className="coaching-round-events">
                {roundGroup.events.map((reviewEvent) => (
                  <CoachingEventCard
                    key={reviewEvent.event.id}
                    reviewEvent={reviewEvent}
                    active={activeEventIds.has(reviewEvent.event.id)}
                    renderJob={renderJobByEventId.get(reviewEvent.event.id)}
                    clipRequesting={requestingEventId === reviewEvent.event.id}
                    onSeek={onSeek}
                    onGenerateClip={onGenerateClip}
                  />
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </aside>
  );
}

const SEVERITY_FILTERS: SeverityFilter[] = ["all", "high", "medium", "low"];
