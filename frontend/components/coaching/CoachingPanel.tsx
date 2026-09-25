"use client";

import { ChevronDown, Search, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { buildCoachingReviewModel, feedbackProgress, type RuleFilter, type SeverityFilter } from "@/lib/coaching-review";
import { coachingLocation, coachingMatchesSearch, coachingRuleLabel } from "@/lib/coaching-copy";
import type { CoachingEvent, CoachingVerdict } from "@/types/coaching";
import type { RenderJobStatus } from "@/lib/api";
import type { ReplayPlayer, ReplayRound } from "@/types/replay";
import { CoachingEventCard } from "./CoachingEventCard";

interface CoachingPanelProps {
  events: CoachingEvent[];
  players: ReplayPlayer[];
  currentTick: number;
  selectedRound: number;
  rounds?: ReplayRound[];
  tickRate?: number;
  selectedPlayerName?: string | null;
  renderJobByEventId: Map<string, RenderJobStatus>;
  requestingEventId: string | null;
  onSeek: (tick: number) => void;
  // Omitted when render clips are off; the cards then drop their clip button.
  onGenerateClip?: (event: CoachingEvent) => void;
  onFeedback: (event: CoachingEvent, verdict: CoachingVerdict | null) => void;
}

export function CoachingPanel({
  events, players, currentTick, selectedRound, rounds, tickRate, selectedPlayerName,
  renderJobByEventId, requestingEventId, onSeek, onGenerateClip, onFeedback
}: CoachingPanelProps) {
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [rule, setRule] = useState<RuleFilter>("all");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState<"current" | "all">("current");
  const [inspectedEventId, setInspectedEventId] = useState<string | null>(null);
  const [expandedRoundNumbers, setExpandedRoundNumbers] = useState<Set<number>>(() => new Set([selectedRound]));
  const reviewModel = useMemo(() => {
    const model = buildCoachingReviewModel(events, players, { severity, rule, search: "" });
    const roundGroups = model.roundGroups.map((group) => ({
      ...group, events: group.events.filter((event) => coachingMatchesSearch(event, search))
    })).filter((group) => group.events.length > 0);
    return { ...model, roundGroups, filteredCount: roundGroups.reduce((count, group) => count + group.events.length, 0) };
  }, [events, players, rule, search, severity]);
  const progress = useMemo(() => feedbackProgress(events), [events]);
  const activeEventIds = useMemo(() => new Set(events.filter((event) =>
    currentTick >= event.tick_start - 128 && currentTick <= event.tick_end + 128
  ).map((event) => event.id)), [currentTick, events]);
  const selectedGroup = reviewModel.roundGroups.find((group) => group.roundNumber === selectedRound);
  const visibleGroups = scope === "current" ? (selectedGroup ? [selectedGroup] : []) : reviewModel.roundGroups;
  const hasFilters = severity !== "all" || rule !== "all" || search.trim().length > 0;

  useEffect(() => {
    setExpandedRoundNumbers(new Set([selectedRound]));
  }, [selectedRound]);

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
    <aside className="coaching-panel review-queue" aria-label="重点建议">
      <div className="coaching-header">
        <div>
          <h2>重点建议</h2>
          <p>{selectedPlayerName ? `${selectedPlayerName} · ${reviewModel.totalCount} 条复盘线索${progress.total > 0 ? ` · 已评价 ${progress.rated}/${progress.total}` : ""}` : "结合比赛画面，回看每次选择"}</p>
        </div>
        <span className="mini-pill">第 {selectedRound} 回合</span>
      </div>

      <div className="coaching-round-scope" role="group" aria-label="建议回合范围">
        <button className={`filter-button ${scope === "current" ? "active" : ""}`} type="button" aria-pressed={scope === "current"} onClick={() => changeScope("current")}>当前回合{selectedGroup ? ` · ${selectedGroup.events.length}` : ""}</button>
        <button className={`filter-button ${scope === "all" ? "active" : ""}`} type="button" aria-pressed={scope === "all"} onClick={() => changeScope("all")}>全部回合 · {reviewModel.filteredCount}</button>
      </div>

      <details className="coaching-filter-toggle">
        <summary><SlidersHorizontal size={14} aria-hidden="true" />筛选建议{hasFilters ? <span className="mini-pill">已筛选</span> : null}<ChevronDown size={14} aria-hidden="true" /></summary>
        <div className="coaching-controls">
          <label className="coaching-search">
            <Search size={15} aria-hidden="true" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索建议、选手或依据" aria-label="搜索建议" />
          </label>
          <div className="severity-filter" role="group" aria-label="重要程度">
            {SEVERITY_FILTERS.map((filter) => (
              <button key={filter.value} className={`filter-button ${severity === filter.value ? "active" : ""}`} type="button" aria-pressed={severity === filter.value} onClick={() => setSeverity(filter.value)}>{filter.label}</button>
            ))}
          </div>
          <select className="rule-filter-select" value={rule} onChange={(event) => setRule(event.target.value as RuleFilter)} aria-label="建议类型">
            <option value="all">全部建议类型</option>
            {reviewModel.availableRules.map((availableRule) => (
              <option key={availableRule.id} value={availableRule.id}>{coachingRuleLabel(availableRule.id, availableRule.label)}（{availableRule.count}）</option>
            ))}
          </select>
          {hasFilters ? <button className="text-button" type="button" onClick={clearFilters}>清除筛选</button> : null}
        </div>
      </details>

      <div className="coaching-body">
        {visibleGroups.length === 0 ? (
          <div className="coaching-empty-state">
            <p>{reviewModel.totalCount === 0 ? selectedPlayerName === null ? "先选择一位选手，再查看对应的复盘建议。" : "暂未发现可供复盘的线索，可以直接观看比赛。没有提示不代表每次选择都正确。" : hasFilters && reviewModel.filteredCount === 0 ? "没有符合筛选条件的建议。" : "这一回合暂无建议，可以查看其他回合。"}</p>
            {scope === "current" && reviewModel.filteredCount > 0 ? <button className="secondary-button compact-button" type="button" onClick={() => changeScope("all")}>查看其他回合的 {reviewModel.filteredCount} 条建议</button> : null}
            {hasFilters ? <button className="text-button" type="button" onClick={clearFilters}>清除筛选</button> : null}
          </div>
        ) : visibleGroups.map((roundGroup) => {
          const expanded = scope === "current" || expandedRoundNumbers.has(roundGroup.roundNumber);
          const eventsId = `coaching-round-${roundGroup.roundNumber}-events`;
          return (
            <section key={roundGroup.roundNumber} className={`coaching-round-group ${roundGroup.roundNumber === selectedRound ? "selected" : ""}`} aria-label={`第 ${roundGroup.roundNumber} 回合建议`}>
              {scope === "all" ? (
                <button className="coaching-round-header" type="button" onClick={() => toggleRound(roundGroup.roundNumber)} aria-expanded={expanded} aria-controls={eventsId}>
                  <span className="coaching-round-header-copy"><strong>第 {roundGroup.roundNumber} 回合</strong><small>{roundGroup.events.length} 条建议</small></span>
                  <ChevronDown className="coaching-round-chevron" size={15} aria-hidden="true" />
                </button>
              ) : null}
              {expanded ? (
                <div id={eventsId} className="coaching-round-events">
                  {roundGroup.events.map((reviewEvent) => (
                    <CoachingEventCard key={reviewEvent.event.id} reviewEvent={reviewEvent} active={activeEventIds.has(reviewEvent.event.id)} inspected={inspectedEventId === reviewEvent.event.id} locationLabel={coachingLocation(reviewEvent.event, rounds, tickRate)} renderJob={renderJobByEventId.get(reviewEvent.event.id)} clipRequesting={requestingEventId === reviewEvent.event.id}
                      onToggleInspect={() => setInspectedEventId((current) => current === reviewEvent.event.id ? null : reviewEvent.event.id)} onSeek={onSeek} onGenerateClip={onGenerateClip} onFeedback={onFeedback} />
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

const SEVERITY_FILTERS: { value: SeverityFilter; label: string }[] = [
  { value: "all", label: "全部" }, { value: "high", label: "优先回看" },
  { value: "medium", label: "值得留意" }, { value: "low", label: "细节建议" }
];