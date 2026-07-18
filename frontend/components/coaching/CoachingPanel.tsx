"use client";

import { ChevronDown, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
  const [inspectedEventId, setInspectedEventId] = useState<string | null>(null);
  const [expandedRoundNumbers, setExpandedRoundNumbers] = useState<Set<number>>(
    () => new Set([selectedRound])
  );
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
  const orderedRoundGroups = useMemo(() => {
    const selectedGroup = reviewModel.roundGroups.find(
      (roundGroup) => roundGroup.roundNumber === selectedRound
    );
    if (!selectedGroup) {
      return reviewModel.roundGroups;
    }
    return [
      selectedGroup,
      ...reviewModel.roundGroups.filter((roundGroup) => roundGroup.roundNumber !== selectedRound)
    ];
  }, [reviewModel.roundGroups, selectedRound]);

  useEffect(() => {
    setExpandedRoundNumbers(new Set([selectedRound]));
  }, [selectedRound]);

  function toggleRound(roundNumber: number) {
    setExpandedRoundNumbers((current) => {
      const next = new Set(current);
      if (next.has(roundNumber)) {
        next.delete(roundNumber);
      } else {
        next.add(roundNumber);
      }
      return next;
    });
  }

  return (
    <aside className="coaching-panel review-queue" aria-label="Review Queue">
      <div className="coaching-header">
        <div>
          <span className="workspace-kicker">Deterministic review</span>
          <h2>Review Queue</h2>
          <p>{reviewModel.filteredCount} of {reviewModel.totalCount} findings</p>
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
          <div className="coaching-empty-state">
            {reviewModel.totalCount === 0
              ? "No coaching events were generated for this replay."
              : "No coaching events match these filters."}
          </div>
        ) : (
          orderedRoundGroups.map((roundGroup) => {
            const expanded = expandedRoundNumbers.has(roundGroup.roundNumber);
            const eventsId = `coaching-round-${roundGroup.roundNumber}-events`;
            return (
              <section
                key={roundGroup.roundNumber}
                className={`coaching-round-group ${
                  roundGroup.roundNumber === selectedRound ? "selected" : ""
                }`}
                aria-label={`Round ${roundGroup.roundNumber} coaching events`}
              >
                <button
                  className="coaching-round-header"
                  type="button"
                  onClick={() => toggleRound(roundGroup.roundNumber)}
                  aria-expanded={expanded}
                  aria-controls={eventsId}
                >
                  <span className="coaching-round-header-copy">
                    <strong>R{roundGroup.roundNumber} evidence</strong>
                    <small>{roundGroup.events.length} findings</small>
                  </span>
                  <ChevronDown className="coaching-round-chevron" size={15} aria-hidden="true" />
                </button>
                {expanded ? (
                  <div id={eventsId} className="coaching-round-events">
                    {roundGroup.events.map((reviewEvent) => (
                      <CoachingEventCard
                        key={reviewEvent.event.id}
                        reviewEvent={reviewEvent}
                        active={activeEventIds.has(reviewEvent.event.id)}
                        inspected={inspectedEventId === reviewEvent.event.id}
                        renderJob={renderJobByEventId.get(reviewEvent.event.id)}
                        clipRequesting={requestingEventId === reviewEvent.event.id}
                        onToggleInspect={() => {
                          setInspectedEventId((current) =>
                            current === reviewEvent.event.id ? null : reviewEvent.event.id
                          );
                        }}
                        onSeek={onSeek}
                        onGenerateClip={onGenerateClip}
                      />
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })
        )}
      </div>
    </aside>
  );
}

const SEVERITY_FILTERS: SeverityFilter[] = ["all", "high", "medium", "low"];
