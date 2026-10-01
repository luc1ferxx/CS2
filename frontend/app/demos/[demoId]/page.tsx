"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { CoachingPanel } from "@/components/coaching/CoachingPanel";
import { coachingCardId } from "@/components/coaching/CoachingEventCard";
import { useActiveCoachingEventIds } from "@/components/coaching/useActiveCoachingEventIds";
import { AuthBoundary } from "@/components/auth/AuthBoundary";
import { useAuth } from "@/components/auth/AuthProvider";
import { SessionControls } from "@/components/auth/SessionControls";
import { AppBrand } from "@/components/layout/AppBrand";
import { ConfirmDialog } from "@/components/feedback/ConfirmDialog";
import { ErrorBanner } from "@/components/feedback/ErrorBanner";
import { SiteFooter } from "@/components/layout/SiteFooter";
import {
  FirstPersonReplay,
  type FirstPersonReplayHandle
} from "@/components/replay/FirstPersonReplay";
import { ClipLibrary } from "@/components/replay/ClipLibrary";
import { DemoStateCard, ReviewSkeleton } from "@/components/replay/DemoLoadState";
import { DetailSummary } from "@/components/replay/DetailSummary";
import { FirstPersonExplainer } from "@/components/replay/FirstPersonExplainer";
import { MatchScoreBanner } from "@/components/replay/MatchScoreBanner";
import { ReviewCommandBar } from "@/components/replay/ReviewCommandBar";
import { ReplayViewer, type ReplayMapOverlayContext } from "@/components/replay/ReplayViewer";
import { PLAYER_PICKER_ID, PersonalReviewPanel } from "@/components/replay/PersonalReviewPanel";
import { RoundReviewPanel, RoundStrip } from "@/components/replay/RoundReviewPanel";
import { Timeline } from "@/components/replay/Timeline";
import {
  DEFAULT_UTILITY_FINDER_STATE,
  UtilityFinder,
  UtilityFinderPending,
  type UtilityFinderState
} from "@/components/replay/UtilityFinder";
import { UtilityLayer } from "@/components/replay/UtilityLayer";
import { MatchAnalysis } from "@/components/stats/MatchAnalysis";
import { Scoreboard } from "@/components/stats/Scoreboard";
import {
  clearCoachingFeedback,
  createMockRenderJob,
  createRenderClipJob,
  deleteDemo,
  getCoaching,
  getDemoStatus,
  getDemoVideo,
  getRenderJobs,
  getRenderWorkerStatus,
  getReplay,
  retryDemoParse,
  retryRenderClipJob,
  saveCoachingFeedback,
  saveVideoCalibration,
  uploadDemoVideo,
  type RenderClipRequest,
  type RenderJobStatus,
  type RenderWorkerStatus,
  type VideoCalibrationUpdate
} from "@/lib/api";
import { NO_CAPABILITIES } from "@/lib/auth";
import { coachingCopy } from "@/lib/coaching-copy";
import {
  demoStatusDisplayLabel,
  detailLoadState,
  detailSummaryItems,
  friendlyErrorMessage,
  isRenderActiveStatus,
  processingHeadline,
  type DetailLoadState,
  type DetailProcessingStep,
  type StatusFetchFailure
} from "@/lib/demo-library";
import { leaveLibraryNotice } from "@/lib/library-notice";
import { mapDisplayName, tacticalRadarImagePaths, type TacticalMapLevelMode } from "@/lib/map-config";
import { matchTeams, playerMatchStats, teamKeyOfPlayer } from "@/lib/match-stats";
import { buildReplayDiagnostics } from "@/lib/replay-diagnostics";
import { resolvePrivateMediaSource } from "@/lib/media-url";
import { parseReviewPlace, reviewPlaceSearch } from "@/lib/replay-place";
import {
  advanceReplayTick,
  findingLeadInTick,
  formatRoundTime,
  roundPlaybackStartTick,
  roundTimeAt,
  videoMediaIdentity,
  videoPlaybackState
} from "@/lib/replay-time";
import { savedClipAtTick, usesVideoClock } from "@/lib/review-workspace";
import { findRoundForTick, findRoundNumberForTick } from "@/lib/round-review";
import { buildEventClipRequest, buildTickClipRequest, clipIsActive, clipRequestAction, clipsForPlayer, matchingClipJob, playableClipVideo, renderJobsSignature, retainSelectedClip, reviewVideo, type SelectedClip } from "@/lib/render-clips";
import {
  RENDER_WORKER_OFFLINE_DETAIL as RENDER_OFFLINE_DETAIL,
  RENDER_WORKER_OFFLINE_LABEL as RENDER_OFFLINE_LABEL,
  renderWorkerOffline
} from "@/lib/render-worker";
import { usePoll } from "@/lib/use-poll";
import { requestFailureKind, userFacingError } from "@/lib/user-errors";
import { utilityAvailability, utilityJumpTick } from "@/lib/utility";
import { withFeedback } from "@/lib/coaching-review";
import {
  coachingForPlayer,
  parserEventsForPlayer,
  personalReviewSummary,
  playerPreferenceKey,
  readPreferredPlayer,
  resolveReviewIdentity,
  reviewIdentityCandidates,
  savePreferredPlayer
} from "@/lib/personal-review";
import type { CoachingEvent, CoachingFeedback, CoachingVerdict } from "@/types/coaching";
import type { DemoProcessingStatus, DemoStatus } from "@/types/demo";
import type { ReplayData, ReplayUtility, ReplayVideo } from "@/types/replay";

// Operator and diagnostics panels only load once "高级工具" is opened.
const RenderOperatorPanel = dynamic(
  () => import("@/components/replay/RenderOperatorPanel").then((module) => module.RenderOperatorPanel),
  { ssr: false }
);
const VideoSetupPanel = dynamic(
  () => import("@/components/replay/VideoSetupPanel").then((module) => module.VideoSetupPanel),
  { ssr: false }
);
const ReplayDiagnosticsPanel = dynamic(
  () => import("@/components/replay/ReplayDiagnosticsPanel").then((module) => module.ReplayDiagnosticsPanel),
  { ssr: false }
);

type ReplayBundle = [ReplayData, CoachingEvent[], RenderJobStatus[]];

interface PageError {
  message: string;
  // Render-state failures clear themselves on the next successful poll.
  source: "action" | "render";
}

// The suggestion the playhead was sent to, and whether the viewer came from its card.
interface FocusedFinding {
  id: string;
  fromCard: boolean;
}

type FindingOrigin = "card" | "navigation" | "timeline";

const STATUS_POLL_MS = 1800;
// A failed status fetch backs off instead of hammering a dropped connection.
const STATUS_RETRY_DELAYS_MS = [1800, 5000, 15000];
const RENDER_POLL_MS = 3000;
const PLACE_WRITE_DELAY_MS = 250;
// A frame after a long stall (hidden tab, debugger) must not jump the replay ahead.
const MAX_FRAME_MS = 250;
const CLIP_CONFLICT_MESSAGE = "比赛处理完成后才能生成视频。";

// The badge uses the library's status wording, so a demo reads the same in both places.
const PROCESSING_STEP_STATUSES: Record<DetailProcessingStep, DemoProcessingStatus> = {
  uploaded: "queued", parsing: "parsing", analyzing: "analyzing"
};

export default function DemoDetailPage() {
  return (
    <AuthBoundary>
      <DemoDetailContent />
    </AuthBoundary>
  );
}

function DemoDetailContent() {
  const params = useParams<{ demoId: string }>();
  const demoId = params.demoId;
  const router = useRouter();
  const { state: authState } = useAuth();
  const { devTools, renderClips } = authState.capabilities ?? NO_CAPABILITIES;
  const account = authState.account;
  const preferenceKey = playerPreferenceKey(account);

  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [statusFailure, setStatusFailure] = useState<StatusFetchFailure | null>(null);
  const [statusFailureCount, setStatusFailureCount] = useState(0);
  const [loadedReplay, setReplay] = useState<ReplayData | null>(null);
  const [selectedClip, setSelectedClip] = useState<SelectedClip | null>(null);
  const [events, setEvents] = useState<CoachingEvent[]>([]);
  const [currentTick, setCurrentTick] = useState(0);
  const [selectedRound, setSelectedRound] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  // "auto" plays a clip when one covers the moment; "video" is the 第一人称 tab chosen on purpose,
  // which explains itself when there is no clip instead of falling back to the map. "utility" is
  // 道具反查, which runs on the map clock and never plays.
  const [viewMode, setViewMode] = useState<"auto" | "map" | "video" | "utility">("auto");
  // 道具反查's filters and selection outlive a trip to 战术回放 and back.
  const [finderState, setFinderState] = useState<UtilityFinderState>(DEFAULT_UTILITY_FINDER_STATE);
  // "只看 X": the thrower a 看这颗 jump focused on; every other player is dimmed on the map.
  const [focusedThrower, setFocusedThrower] = useState<{ demoId: string; id: string; name: string } | null>(null);
  const [mapLevelMode, setMapLevelMode] = useState<TacticalMapLevelMode>("auto");
  const [savedIdentity, setSavedIdentity] = useState("");
  const [preferenceSaved, setPreferenceSaved] = useState(true);
  const [playerOverride, setPlayerOverride] = useState<{ demoId: string; playerId: string | null } | null>(null);
  const [focusedFinding, setFocusedFinding] = useState<FocusedFinding | null>(null);
  // The "当前建议" strip; dismissing it keeps the finding as the anchor for 上一条/下一条.
  const [findingStripOpen, setFindingStripOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [error, setError] = useState<PageError | null>(null);
  const [replayLoadFailed, setReplayLoadFailed] = useState(false);
  const [replayReloading, setReplayReloading] = useState(false);
  const [sawProcessing, setSawProcessing] = useState(false);
  const [renderRequesting, setRenderRequesting] = useState(false);
  const [parseRetrying, setParseRetrying] = useState(false);
  // A refused retry stays on screen until the next attempt or until the demo leaves "failed".
  const [parseRetryError, setParseRetryError] = useState<string | null>(null);
  const [clipRequestingEventId, setClipRequestingEventId] = useState<string | null>(null);
  const [tickClipRequesting, setTickClipRequesting] = useState(false);
  const [renderJobs, setRenderJobs] = useState<RenderJobStatus[]>([]);
  const [renderWorker, setRenderWorker] = useState<RenderWorkerStatus | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Set before the delete request: polls stop and the 404s that follow stay quiet.
  const [deleting, setDeleting] = useState(false);
  const deletingRef = useRef(false);
  // A background render may update the demo's default video without changing the clip being reviewed.
  const replay = useMemo(() => loadedReplay
    ? { ...loadedReplay, video: reviewVideo(loadedReplay.video, renderJobs, selectedClip, demoId) }
    : null, [demoId, loadedReplay, renderJobs, selectedClip]);
  const [renderJobsRefreshing, setRenderJobsRefreshing] = useState(false);
  const [currentVideoTime, setCurrentVideoTime] = useState(0);
  const [detectedVideoDuration, setDetectedVideoDuration] = useState<number | null>(null);
  const [unavailableVideoIdentity, setUnavailableVideoIdentity] = useState<string | null>(null);
  const firstPersonReplayRef = useRef<FirstPersonReplayHandle | null>(null);
  const renderJobsSignatureRef = useRef<string | null>(null);
  const defaultVideoRef = useRef<ReplayVideo | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);
  const savedClipsRef = useRef<HTMLDetailsElement | null>(null);
  // The control the viewer pressed "查看这一刻" on, so "返回建议" can hand focus back to it.
  const findingOriginRef = useRef<HTMLElement | null>(null);

  const revealStage = useCallback(() => {
    window.requestAnimationFrame(() => {
      const stage = stageRef.current;
      if (!stage) return;
      stage.focus({ preventScroll: true });
      const bounds = stage.getBoundingClientRect();
      if (bounds.top < 56 || bounds.bottom > window.innerHeight) {
        stage.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
      }
    });
  }, []);

  useEffect(() => {
    setSavedIdentity(readPreferredPlayer(() => window.localStorage, preferenceKey));
  }, [preferenceKey]);

  // The tab names the demo under review. On leaving, the previous title comes back
  // unless the next route has already put its own in place.
  const documentTitleName = status ? status.name ?? `比赛 ${status.id.slice(0, 8)}` : null;
  useEffect(() => {
    if (!documentTitleName) return;
    const previous = document.title;
    const title = `${documentTitleName} - CS2 复盘`;
    document.title = title;
    return () => {
      if (document.title === title) document.title = previous;
    };
  }, [documentTitleName]);

  const revealPlayerPicker = useCallback(() => {
    const picker = document.getElementById(PLAYER_PICKER_ID);
    if (!picker) return;
    picker.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    picker.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const nextStatus = await getDemoStatus(demoId);
      setStatus(nextStatus);
      setStatusFailure(null);
      setStatusFailureCount(0);
      if (nextStatus.status !== "failed") {
        setParseRetryError(null);
      }
      return nextStatus;
    } catch (err) {
      if (deletingRef.current) return null;
      // A 404 is final: deleted, another account's demo, or a mistyped link.
      if (requestFailureKind(err) === "not_found") {
        setStatusFailure("not_found");
      } else {
        setStatusFailure("unreachable");
        setStatusFailureCount((count) => count + 1);
      }
      return null;
    }
  }, [demoId]);

  const fetchReplayBundle = useCallback(
    (): Promise<ReplayBundle> => Promise.all([getReplay(demoId), getCoaching(demoId), getRenderJobs(demoId)]),
    [demoId]
  );

  const loadReplay = useCallback(async (prefetched?: Promise<ReplayBundle>, isCurrent: () => boolean = () => true) => {
    let request = prefetched ?? fetchReplayBundle();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const [nextReplay, nextEvents, nextRenderJobs] = await request;
        if (!isCurrent()) return;
        setReplay(nextReplay);
        const initialSelection = retainSelectedClip(null, nextRenderJobs, nextReplay.video, demoId);
        const initialVideo = reviewVideo(nextReplay.video, nextRenderJobs, initialSelection, demoId);
        setSelectedClip(initialSelection);
        setEvents(nextEvents);
        setRenderJobs(nextRenderJobs);
        renderJobsSignatureRef.current = renderJobsSignature(nextRenderJobs);
        // A place kept in the URL (refresh, back, signing in again) wins; otherwise the
        // saved video, otherwise the first round once freeze time is over.
        const place = parseReviewPlace(window.location.search, nextReplay);
        const placeRound = place.roundNumber === null
          ? undefined
          : nextReplay.rounds.find((round) => round.roundNumber === place.roundNumber);
        const firstRound = nextReplay.rounds[0];
        const initialTick = place.tick ?? (placeRound
          ? roundPlaybackStartTick(placeRound)
          : initialVideo.url
            ? initialVideo.tickStart
            : firstRound ? roundPlaybackStartTick(firstRound) : 0);
        const initialRound = findRoundForTick(nextReplay.rounds, initialTick) ?? firstRound;
        if (place.playerId) setPlayerOverride({ demoId, playerId: place.playerId });
        setSelectedRound(initialRound?.roundNumber ?? 1);
        setCurrentTick(initialTick);
        setCurrentVideoTime(initialVideo.timeOriginSeconds ?? 0);
        setDetectedVideoDuration(null);
        setReplayLoadFailed(false);
        return;
      } catch (err) {
        if (!isCurrent()) return;
        if (requestFailureKind(err) !== "conflict") {
          setReplayLoadFailed(true);
          return;
        }
        // 409: the demo left "completed" between the status and replay requests
        // (a re-parse). Refetch the status; if it is processing, the poll takes over.
        const nextStatus = await loadStatus();
        if (!isCurrent() || nextStatus?.status !== "completed") return;
        request = fetchReplayBundle();
      }
    }
    setReplayLoadFailed(true);
  }, [demoId, fetchReplayBundle, loadStatus]);

  const replayLoaded = loadedReplay !== null;
  const refreshStatus = useCallback(async () => {
    const nextStatus = await loadStatus();
    if (nextStatus?.status === "completed" && !replayLoaded) {
      await loadReplay();
    }
  }, [loadReplay, loadStatus, replayLoaded]);

  const reloadReplay = useCallback(async () => {
    setReplayReloading(true);
    try {
      await loadReplay();
    } finally {
      setReplayReloading(false);
    }
  }, [loadReplay]);

  const retryParse = useCallback(async () => {
    setParseRetrying(true);
    setParseRetryError(null);
    try {
      await retryDemoParse(demoId);
      // The previous fetch's verdict is void the moment a re-parse is queued;
      // clearing it here keeps the stale failure out of the window where status
      // flips back to "completed" while the new replay is still in flight.
      setReplayLoadFailed(false);
      // Refetch instead of folding the summary the retry returns into the
      // status shape: it also puts the demo back in a non-completed state,
      // which re-arms the poll below and loads the replay on its own.
      await loadStatus();
    } catch (err) {
      setParseRetryError(userFacingError(err, "重新处理失败，请稍后再试。"));
    } finally {
      setParseRetrying(false);
    }
  }, [demoId, loadStatus]);

  // Resolves false when the server refused the verdict; the card says so in place and offers to re-send.
  const submitCoachingFeedback = useCallback(async (event: CoachingEvent, verdict: CoachingVerdict | null) => {
    // The verdict is the player's own statement, so show it at once and only
    // roll back if the server refuses it.
    const previous = event.feedback ?? null;
    setEvents((current) => withFeedback(
      current, event.id, verdict ? { verdict, note: null, updated_at: new Date().toISOString() } : null
    ));
    // A later click may already have moved the card on; a response (or a
    // rollback) only lands if the card still shows the verdict it was for.
    const applyIfStillCurrent = (feedback: CoachingFeedback | null) =>
      setEvents((current) => current.map((item) =>
        item.id === event.id && (item.feedback?.verdict ?? null) === verdict ? { ...item, feedback } : item
      ));
    try {
      if (verdict) {
        applyIfStillCurrent(await saveCoachingFeedback(demoId, event.id, { verdict }));
      } else {
        await clearCoachingFeedback(demoId, event.id);
      }
      return true;
    } catch {
      applyIfStillCurrent(previous);
      return false;
    }
  }, [demoId]);

  useEffect(() => {
    let cancelled = false;
    const isCurrent = () => !cancelled;
    // Demos opened from the library are nearly always completed, so the replay
    // download starts alongside the status request instead of after it. A demo
    // that is not ready answers 409 at once and the result is dropped.
    const prefetched = fetchReplayBundle();
    prefetched.catch(() => undefined);

    async function boot() {
      const nextStatus = await loadStatus();
      if (!cancelled && nextStatus?.status === "completed") {
        await loadReplay(prefetched, isCurrent);
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, [fetchReplayBundle, loadReplay, loadStatus]);

  // No poll before the first status answer, none once processing is over, and
  // none for a demo that does not exist; a dropped connection backs off.
  const statusPollDelay = deleting || (status === null && statusFailure === null) || statusFailure === "not_found" ||
    (status?.status === "completed" && !status.ingestion?.active)
    ? null
    : statusFailure === "unreachable"
      ? STATUS_RETRY_DELAYS_MS[Math.min(statusFailureCount, STATUS_RETRY_DELAYS_MS.length) - 1] ?? STATUS_POLL_MS
      : STATUS_POLL_MS;
  usePoll(refreshStatus, statusPollDelay);

  // The radar only loads once the replay JSON is parsed; start it as soon as the map is known.
  const readyMapName = status?.status === "completed" ? status.map_name : null;
  useEffect(() => {
    for (const source of tacticalRadarImagePaths(readyMapName)) {
      const image = new Image();
      image.src = source;
    }
  }, [readyMapName]);

  useEffect(() => {
    defaultVideoRef.current = loadedReplay?.video ?? null;
  }, [loadedReplay?.video]);

  const selectedRoundData = useMemo(
    () => replay?.rounds.find((round) => round.roundNumber === selectedRound),
    [replay?.rounds, selectedRound]
  );
  const nextRoundData = useMemo(() => {
    if (!replay || !selectedRoundData) return undefined;
    return [...replay.rounds]
      .sort((left, right) => left.roundNumber - right.roundNumber)
      .find((round) => round.roundNumber > selectedRoundData.roundNumber);
  }, [replay, selectedRoundData]);
  const atRoundEnd = Boolean(selectedRoundData && currentTick >= selectedRoundData.endTick - 0.5);
  const identityCandidates = useMemo(
    () => reviewIdentityCandidates(savedIdentity, account),
    [account, savedIdentity]
  );
  const preferredPlayerMatch = useMemo(
    () => resolveReviewIdentity(replay?.players ?? [], identityCandidates),
    [identityCandidates, replay?.players]
  );
  const selectedPlayerId = playerOverride?.demoId === demoId
    ? playerOverride.playerId
    : preferredPlayerMatch.player?.id ?? null;
  const selectPlayer = useCallback((playerId: string | null) => {
    setPlayerOverride({ demoId, playerId });
  }, [demoId]);
  const selectedPlayer = useMemo(
    () => replay?.players.find((player) => player.id === selectedPlayerId) ?? null,
    [replay?.players, selectedPlayerId]
  );
  // Scores and the scoreboard are computed once per loaded replay (the index is cached by its arrays).
  const matchSummaryTeams = status?.matchSummary?.teams;
  const teams = useMemo(
    () => (loadedReplay ? matchTeams(loadedReplay, matchSummaryTeams) : []),
    [loadedReplay, matchSummaryTeams]
  );
  const scoreboardStats = useMemo(() => (loadedReplay ? playerMatchStats(loadedReplay) : []), [loadedReplay]);
  const reviewedTeamKey = loadedReplay && selectedPlayerId ? teamKeyOfPlayer(loadedReplay, selectedPlayerId) : null;
  // "你的队伍" follows the viewer's own identity, not whoever is being reviewed.
  const yourPlayerId = preferredPlayerMatch.status === "matched" ? preferredPlayerMatch.player?.id ?? null : null;
  const yourTeamKey = loadedReplay && yourPlayerId ? teamKeyOfPlayer(loadedReplay, yourPlayerId) : null;
  const videoUnavailable = Boolean(replay && (
    unavailableVideoIdentity === videoMediaIdentity(replay.video) ||
    (replay.video.url && !resolvePrivateMediaSource(replay.video.url))
  ));
  const videoPlayback = replay
    ? videoPlaybackState(replay.video, currentTick, selectedPlayerId, videoUnavailable)
    : "unavailable";
  const mapClock = viewMode === "map" || viewMode === "utility";
  const videoDrivesClock = usesVideoClock(mapClock ? "map" : "auto", videoPlayback);
  // v1 replays have no throws: the tab waits for the background upgrade, or is not offered.
  const utilityTab = replay
    ? utilityAvailability(replay, Boolean(status?.ingestion?.replayUpgradePending))
    : "hidden";
  const finderSelected = viewMode === "utility" && utilityTab !== "hidden";
  const focusPlayer = focusedThrower?.demoId === demoId ? focusedThrower : null;
  const personalEvents = useMemo(
    () => coachingForPlayer(events, selectedPlayer?.id ?? null),
    [events, selectedPlayer?.id]
  );
  const personalSummary = useMemo(
    () => personalReviewSummary(events, selectedPlayer?.id ?? null),
    [events, selectedPlayer?.id]
  );
  const scopedReplay = useMemo(
    () => replay ? { ...replay, events: parserEventsForPlayer(replay.events ?? [], selectedPlayer?.id ?? null) } : null,
    [replay, selectedPlayer?.id]
  );
  const orderedFindings = useMemo(
    () => [...personalEvents].sort((left, right) => left.tick_start - right.tick_start),
    [personalEvents]
  );
  // With the lead-in the playhead sits before the focused finding, so 上一条/下一条
  // count from that finding until playback moves past it.
  const focusedFindingIndex = focusedFinding
    ? orderedFindings.findIndex((event) => event.id === focusedFinding.id)
    : -1;
  const anchorIndex = focusedFindingIndex >= 0 && currentTick <= orderedFindings[focusedFindingIndex].tick_start + 0.5
    ? focusedFindingIndex
    : -1;
  const previousFinding = useMemo(() => {
    if (anchorIndex >= 0) return orderedFindings[anchorIndex - 1] ?? null;
    for (let index = orderedFindings.length - 1; index >= 0; index -= 1) {
      if (orderedFindings[index].tick_start < currentTick - 0.5) {
        return orderedFindings[index];
      }
    }
    return null;
  }, [anchorIndex, currentTick, orderedFindings]);
  const nextFinding = useMemo(
    () => anchorIndex >= 0
      ? orderedFindings[anchorIndex + 1] ?? null
      : orderedFindings.find((event) => event.tick_start > currentTick + 0.5) ?? null,
    [anchorIndex, currentTick, orderedFindings]
  );
  const focusedEvent = focusedFindingIndex >= 0 ? orderedFindings[focusedFindingIndex] : null;
  const tickActiveEventIds = useActiveCoachingEventIds(personalEvents, currentTick);
  // The card of the finding being watched stays marked through its lead-in.
  const activeEventIds = useMemo(() => {
    if (!focusedEvent || tickActiveEventIds.has(focusedEvent.id)) return tickActiveEventIds;
    return new Set([...tickActiveEventIds, focusedEvent.id]);
  }, [focusedEvent, tickActiveEventIds]);
  const renderJobByEventId = useMemo(() => {
    const jobsByEventId = new Map<string, RenderJobStatus>();
    if (!replay) return jobsByEventId;
    for (const event of personalEvents) {
      const job = savedClipAtTick(renderJobs, event.tick_start, event.player_id, replay.video.renderJobId)
        ?? matchingClipJob(renderJobs, buildEventClipRequest(replay, event));
      if (job) jobsByEventId.set(event.id, job);
    }
    return jobsByEventId;
  }, [personalEvents, renderJobs, replay]);
  const personalClips = useMemo(() => clipsForPlayer(renderJobs, selectedPlayerId), [renderJobs, selectedPlayerId]);
  const currentTickClipJob = useMemo(() => replay && renderClips
    ? savedClipAtTick(renderJobs, currentTick, selectedPlayerId, replay.video.renderJobId)
      ?? matchingClipJob(renderJobs, buildTickClipRequest(replay, currentTick, selectedRound, selectedPlayerId))
    : null, [currentTick, renderClips, renderJobs, replay, selectedPlayerId, selectedRound]);
  const latestRenderClipJob = renderJobs[0] ?? null;
  const tickClipWorkerOffline = renderWorkerOffline(renderWorker) && currentTickClipJob?.status === "queued";
  const hasActiveRenderClipJob = renderJobs.some((job) => isRenderActiveStatus(job.status));
  // Accounts without clip generation and without any saved video never see video controls.
  const hasAnyVideo = Boolean(replay?.video.url) || renderJobs.some((job) => playableClipVideo(job) !== null);
  const showVideoControls = renderClips || hasAnyVideo;
  const showFirstPersonExplainer = showVideoControls && viewMode === "video" && !videoDrivesClock;
  const firstPersonSelected = videoDrivesClock || showFirstPersonExplainer;
  const playableClipCount = useMemo(() => personalClips.filter((job) => playableClipVideo(job)).length, [personalClips]);
  const currentRoundNumber = useMemo(
    () => (replay ? findRoundNumberForTick(replay.rounds, currentTick) : null),
    [currentTick, replay]
  );
  const summaryItems = useMemo(
    () => detailSummaryItems({ status, replay, latestRenderJob: latestRenderClipJob }),
    [latestRenderClipJob, replay, status]
  );
  const detailDiagnostics = useMemo(
    // Replay data checks are a developer tool; players only get the match summary.
    () => (replay && inspectorOpen && devTools ? buildReplayDiagnostics(replay, events, renderJobs) : null),
    [devTools, events, inspectorOpen, renderJobs, replay]
  );

  const videoStatus = loadedReplay?.video.status;
  const loadState: DetailLoadState | null = replay
    ? null
    : detailLoadState({ status, statusFailure, replayLoadFailed });

  useEffect(() => {
    if (loadState?.kind === "processing") setSawProcessing(true);
  }, [loadState?.kind]);

  const loadRenderState = useCallback(async (forceVideo = false) => {
    try {
      const [nextJobs, worker] = await Promise.all([
        getRenderJobs(demoId),
        // A queued job goes nowhere without a renderer, so liveness rides along
        // with the same poll that already drives the job list.
        getRenderWorkerStatus().catch(() => null)
      ]);
      // Each of /render/jobs and /video reads the whole replay blob on the
      // server. The jobs already carry their own videos, so the demo's default
      // video is refetched only when the job list moved (or there are no clip
      // jobs, in which case /render/jobs read nothing).
      const signature = renderJobsSignature(nextJobs);
      const video = forceVideo || nextJobs.length === 0 || signature !== renderJobsSignatureRef.current
        ? await getDemoVideo(demoId)
        : null;
      renderJobsSignatureRef.current = signature;
      setRenderJobs(nextJobs);
      setRenderWorker(worker);
      if (video) {
        setReplay((currentReplay) =>
          currentReplay ? { ...currentReplay, video } : currentReplay
        );
      }
      const defaultVideo = video ?? defaultVideoRef.current;
      if (defaultVideo) {
        setSelectedClip((current) => retainSelectedClip(current, nextJobs, defaultVideo, demoId));
      }
      setError((current) => (current?.source === "render" ? null : current));
      return true;
    } catch (err) {
      if (!deletingRef.current) {
        setError({ source: "render", message: userFacingError(err, "刷新视频状态失败，稍后会自动重试。") });
      }
      return false;
    }
  }, [demoId]);

  const refreshRenderOperatorState = useCallback(async () => {
    setRenderJobsRefreshing(true);
    try {
      await loadRenderState(true);
    } finally {
      setRenderJobsRefreshing(false);
    }
  }, [loadRenderState]);
  const refreshRenderOperator = useCallback(() => void refreshRenderOperatorState(), [refreshRenderOperatorState]);

  usePoll(() => loadRenderState(), !deleting && (isRenderActiveStatus(videoStatus) || hasActiveRenderClipJob) ? RENDER_POLL_MS : null);

  // The first-person explanation says whether the recorder is running; ask once when it opens.
  const needsWorkerStatus = showFirstPersonExplainer && renderClips && renderWorker === null;
  useEffect(() => {
    if (!needsWorkerStatus) return;
    let cancelled = false;
    getRenderWorkerStatus()
      .then((worker) => { if (!cancelled) setRenderWorker(worker); })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [needsWorkerStatus]);

  // Tactical playback advances by the time that actually passed, once per animation frame.
  useEffect(() => {
    if (!playing || !replay || !selectedRoundData || videoDrivesClock) {
      return;
    }

    let frameId = 0;
    let previousTime: number | null = null;
    const step = (now: number) => {
      if (previousTime !== null) {
        const elapsedMs = Math.min(MAX_FRAME_MS, Math.max(0, now - previousTime));
        setCurrentTick((tick) => {
          const nextTick = advanceReplayTick(
            tick, (elapsedMs / 1000) * replay.tickRate * speed, selectedRoundData.endTick,
            replay.video, selectedPlayerId, videoUnavailable || mapClock
          );
          if (nextTick >= selectedRoundData.endTick) {
            setPlaying(false);
            return selectedRoundData.endTick;
          }
          return nextTick;
        });
      }
      previousTime = now;
      frameId = window.requestAnimationFrame(step);
    };
    frameId = window.requestAnimationFrame(step);

    return () => window.cancelAnimationFrame(frameId);
  }, [mapClock, playing, replay, selectedPlayerId, selectedRoundData, speed, videoDrivesClock, videoUnavailable]);

  const updateCoordinateFromTick = useCallback((tick: number) => {
    setCurrentTick(tick);
    const nextRound = replay?.rounds.find(
      (round) => tick >= round.startTick && tick <= round.endTick
    );
    if (nextRound) {
      setSelectedRound(nextRound.roundNumber);
    }
  }, [replay?.rounds]);

  const seek = useCallback((tick: number) => {
    firstPersonReplayRef.current?.seekToTick(tick);
    updateCoordinateFromTick(tick);
  }, [updateCoordinateFromTick]);

  // Seeks the viewer asked for directly (timeline, round rail, quick jumps) leave the suggestion behind.
  const manualSeek = useCallback((tick: number) => {
    setFocusedFinding(null);
    setFindingStripOpen(false);
    seek(tick);
  }, [seek]);

  // Handlers read the latest render's values, so they keep one identity across playback frames
  // and the memoized panels that receive them skip those frames.
  const latest = useRef({
    replay, renderJobs, selectedPlayerId, personalEvents, currentTick, playing, atRoundEnd, nextRoundData,
    selectedRoundData, videoDrivesClock, previousFinding, nextFinding, focusedFinding, viewMode
  });
  latest.current = {
    replay, renderJobs, selectedPlayerId, personalEvents, currentTick, playing, atRoundEnd, nextRoundData,
    selectedRoundData, videoDrivesClock, previousFinding, nextFinding, focusedFinding, viewMode
  };

  const seekToFinding = useCallback((tick: number, eventId?: string, origin?: FindingOrigin) => {
    const state = latest.current;
    // The caller names the suggestion and where it came from; focus is not reliable (Safari does not focus clicked buttons).
    const event = (eventId ? state.personalEvents.find((item) => item.id === eventId) : undefined)
      ?? state.personalEvents.find((item) => item.tick_start === tick)
      ?? null;
    const fromCard = origin === "card" && event !== null;
    const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const saved = savedClipAtTick(state.renderJobs, tick, state.selectedPlayerId, state.replay?.video.renderJobId);
    const video = playableClipVideo(saved);
    if (video) setSelectedClip({ demoId, video });
    const round = state.replay?.rounds.find((item) => item.roundNumber === event?.round_number)
      ?? (state.replay ? findRoundForTick(state.replay.rounds, tick) : undefined);
    let target = findingLeadInTick(tick, round, state.replay?.tickRate ?? 64);
    // A saved clip starts playing at its first frame rather than on the map just before it.
    if (video && target < video.tickStart && tick >= video.tickStart) target = video.tickStart;
    setViewMode("auto");
    setPlaying(false);
    seek(target);
    setFocusedFinding(event ? { id: event.id, fromCard } : null);
    setFindingStripOpen(event !== null);
    const card = event ? document.getElementById(coachingCardId(event.id)) : null;
    findingOriginRef.current = fromCard && activeElement && card?.contains(activeElement) ? activeElement : null;
    if (origin === "timeline") {
      // The timeline sits next to the list on wide screens; bring the matching card into view there.
      if (event && window.matchMedia("(min-width: 1001px)").matches) {
        window.requestAnimationFrame(() => {
          document.getElementById(coachingCardId(event.id))?.scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
        });
      }
    } else {
      revealStage();
    }
  }, [demoId, revealStage, seek]);

  const seekToFindingFromTimeline = useCallback(
    (event: CoachingEvent) => seekToFinding(event.tick_start, event.id, "timeline"),
    [seekToFinding]
  );

  const returnToFinding = useCallback(() => {
    const focused = latest.current.focusedFinding;
    setFindingStripOpen(false);
    if (!focused) return;
    const card = document.getElementById(coachingCardId(focused.id));
    if (!card) {
      document.querySelector(".coaching-panel")?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
      return;
    }
    const origin = findingOriginRef.current;
    const target = origin && origin.isConnected && card.contains(origin)
      ? origin
      : card.querySelector<HTMLElement>(".locate-tick-button") ?? card.querySelector<HTMLElement>("button") ?? card;
    card.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    target.focus({ preventScroll: true });
  }, []);

  const goToPreviousFinding = useCallback(() => {
    const event = latest.current.previousFinding;
    if (event) seekToFinding(event.tick_start, event.id, "navigation");
  }, [seekToFinding]);

  const goToNextFinding = useCallback(() => {
    const event = latest.current.nextFinding;
    if (event) seekToFinding(event.tick_start, event.id, "navigation");
  }, [seekToFinding]);

  const changeRound = useCallback((roundNumber: number) => {
    const nextRound = latest.current.replay?.rounds.find((round) => round.roundNumber === roundNumber);
    setFocusedFinding(null);
    setFindingStripOpen(false);
    if (nextRound) {
      // Freeze time is buy-phase standing around; "回合开始" stays one click away in the quick jumps.
      seek(roundPlaybackStartTick(nextRound));
    } else {
      setSelectedRound(roundNumber);
    }
  }, [seek]);

  const playNextRound = useCallback(() => {
    const nextRound = latest.current.nextRoundData;
    if (!nextRound) return;
    setFocusedFinding(null);
    setFindingStripOpen(false);
    seek(roundPlaybackStartTick(nextRound));
    setPlaying(true);
  }, [seek]);

  const togglePlay = useCallback(() => {
    const state = latest.current;
    if (state.playing) {
      setPlaying(false);
      return;
    }
    // Play from the first-person explanation or 道具反查 shows the map again: the replay never runs unseen.
    if (state.viewMode === "video" && !state.videoDrivesClock) setViewMode("auto");
    if (state.viewMode === "utility") setViewMode("map");
    // At the end of a round, Play carries on into the next one instead of stopping at once.
    if (state.atRoundEnd && state.nextRoundData && !state.videoDrivesClock) {
      playNextRound();
      return;
    }
    setPlaying(true);
  }, [playNextRound]);

  const seekBy = useCallback((seconds: number) => {
    const state = latest.current;
    const round = state.selectedRoundData;
    if (!state.replay || !round) return;
    const rate = state.replay.tickRate > 0 ? state.replay.tickRate : 64;
    seek(Math.min(round.endTick, Math.max(round.startTick, state.currentTick + seconds * rate)));
  }, [seek]);

  const stepRound = useCallback((direction: -1 | 1) => {
    const state = latest.current;
    if (!state.replay || !state.selectedRoundData) return;
    const ordered = [...state.replay.rounds].sort((left, right) => left.roundNumber - right.roundNumber);
    const index = ordered.findIndex((round) => round.roundNumber === state.selectedRoundData?.roundNumber);
    const target = ordered[index + direction];
    if (target) changeRound(target.roundNumber);
  }, [changeRound]);

  const toggleShortcuts = useCallback(() => setShortcutsOpen((open) => !open), []);

  // Jumps from the 数据 section: the stage leaves the first-person explanation (a saved clip
  // covering the moment still plays) and scrolls into view.
  const jumpToTick = useCallback((tick: number) => {
    setPlaying(false);
    setViewMode((mode) => (mode === "video" || mode === "utility" ? "auto" : mode));
    manualSeek(tick);
    revealStage();
  }, [manualSeek, revealStage]);

  const jumpToRound = useCallback((roundNumber: number) => {
    setViewMode((mode) => (mode === "video" || mode === "utility" ? "auto" : mode));
    changeRound(roundNumber);
    revealStage();
  }, [changeRound, revealStage]);

  const showFirstPerson = useCallback(() => {
    // Without a clip here the stage explains itself; the replay should not run on unseen.
    if (latest.current.replay && !latest.current.videoDrivesClock) setPlaying(false);
    setViewMode("video");
  }, []);

  const showUtilityFinder = useCallback(() => {
    setPlaying(false);
    setViewMode("utility");
  }, []);

  // 看这颗: back to 战术回放 two seconds before the throw, through the shared seek, watching the thrower.
  const jumpToUtility = useCallback((utility: ReplayUtility) => {
    const current = latest.current.replay;
    if (!current) return;
    setPlaying(false);
    setViewMode("map");
    manualSeek(utilityJumpTick(utility, current.rounds, current.tickRate));
    const name = current.players.find((player) => player.id === utility.throwerId)?.name ?? utility.throwerName ?? "";
    setFocusedThrower(utility.throwerId ? { demoId, id: utility.throwerId, name } : null);
    revealStage();
  }, [demoId, manualSeek, revealStage]);

  // 显示全部 unmounts its own strip: hand focus to the stage instead of letting it drop to the page.
  const clearFocusPlayer = useCallback(() => {
    setFocusedThrower(null);
    stageRef.current?.focus({ preventScroll: true });
  }, []);

  // Grenades on the map during playback (over the players); the viewer hands over the floor and round it shows.
  const utilityOverlay = useCallback((context: ReplayMapOverlayContext) => loadedReplay ? (
    <UtilityLayer replay={loadedReplay} currentTick={context.currentTick} map={context.map} floor={context.floor}
      roundNumber={context.roundNumber} focusPlayerId={context.focusPlayerId} unitsPerPixel={context.unitsPerPixel} />
  ) : null, [loadedReplay]);

  const showSavedClips = useCallback(() => {
    const details = savedClipsRef.current;
    if (!details) return;
    details.open = true;
    details.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    details.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
  }, []);

  const saveIdentity = useCallback((identity: string) => {
    setSavedIdentity(identity.trim());
    setPlayerOverride(null);
    setPreferenceSaved(savePreferredPlayer(identity, () => window.localStorage, preferenceKey));
  }, [preferenceKey]);

  // A different reviewed player has different suggestions; the old one no longer anchors anything.
  useEffect(() => {
    setFocusedFinding(null);
    setFindingStripOpen(false);
  }, [selectedPlayerId]);

  // Keyboard review: works anywhere on the page except while typing or on a control's own keys.
  useEffect(() => {
    if (!replayLoaded) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const key = event.key;
      if (target && keepsKeyForItself(target, key)) return;
      const onControl = Boolean(target?.closest("button, a, summary, [role='button'], input"));
      if (key === "?") {
        toggleShortcuts();
      } else if (key === "Escape") {
        setShortcutsOpen(false);
        return;
      } else if (event.shiftKey && key !== "ArrowLeft" && key !== "ArrowRight") {
        return;
      } else if (key === " " || key === "Spacebar") {
        // Space on a focused button, checkbox or radio operates that control; on the slider it plays.
        if (onControl && !(target instanceof HTMLInputElement && target.type === "range")) return;
        togglePlay();
      } else if (key === "k" || key === "K") {
        togglePlay();
      } else if (key === "ArrowLeft" || key === "ArrowRight") {
        seekBy((key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 1 : 5));
      } else if (key === "[" || key === "]") {
        stepRound(key === "[" ? -1 : 1);
      } else if (key === "n" || key === "N") {
        goToNextFinding();
      } else if (key === "p" || key === "P") {
        goToPreviousFinding();
      } else {
        return;
      }
      event.preventDefault();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [goToNextFinding, goToPreviousFinding, replayLoaded, seekBy, stepRound, togglePlay, toggleShortcuts]);

  // Keep the place in the URL (never per playback frame), so refresh and re-login return here.
  useEffect(() => {
    if (!replayLoaded || playing) return;
    const timer = window.setTimeout(() => {
      const search = reviewPlaceSearch(window.location.search, {
        roundNumber: selectedRound, tick: currentTick, playerId: selectedPlayerId
      });
      if (search !== window.location.search) {
        window.history.replaceState(window.history.state, "", `${window.location.pathname}${search}${window.location.hash}`);
      }
    }, PLACE_WRITE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [currentTick, playing, replayLoaded, selectedPlayerId, selectedRound]);

  // "进入复盘" links to #player, which only exists once the replay is loaded.
  useEffect(() => {
    if (replayLoaded && window.location.hash === "#player") revealStage();
  }, [replayLoaded, revealStage]);

  const viewVideoClip = useCallback(() => {
    if (!replay) return;
    if (replay.video.povSteamId) {
      const player = replay.players.find((item) => item.id === replay.video.povSteamId);
      if (!player) return;
      selectPlayer(player.id);
    }
    setUnavailableVideoIdentity(null);
    setViewMode("auto");
    seek(replay.video.tickStart);
    setPlaying(true);
    revealStage();
  }, [replay, revealStage, seek, selectPlayer]);

  const playSavedClip = useCallback((job: RenderJobStatus, tick?: number) => {
    const video = playableClipVideo(job);
    if (!video || !replay?.players.some((player) => player.id === video.povSteamId)) return;
    setSelectedClip({ demoId, video });
    setViewMode("auto");
    selectPlayer(video.povSteamId ?? null);
    setUnavailableVideoIdentity(null);
    setDetectedVideoDuration(null);
    const nextTick = tick !== undefined && tick >= video.tickStart && tick < video.tickEnd ? tick : video.tickStart;
    // Reused media needs an explicit seek before its clock resumes. A different clip seeks on mount.
    if (videoMediaIdentity(video) === videoMediaIdentity(replay.video)) {
      seek(nextTick);
    } else {
      updateCoordinateFromTick(nextTick);
    }
    setPlaying(true);
    revealStage();
  }, [demoId, replay?.players, replay?.video, revealStage, seek, selectPlayer, updateCoordinateFromTick]);

  async function requestMockRender() {
    if (!replay) {
      return;
    }

    setRenderRequesting(true);
    try {
      const response = await createMockRenderJob(demoId);
      setSelectedClip(null);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video: response.video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError({ source: "action", message: friendlyErrorMessage(err instanceof Error ? err.message : "创建模拟视频任务失败") });
    } finally {
      setRenderRequesting(false);
    }
  }

  async function requestRenderClipForEvent(event: CoachingEvent) {
    if (!replay) {
      return;
    }

    setClipRequestingEventId(event.id);
    try {
      await requestOrViewClip(buildEventClipRequest(replay, event), event.tick_start);
      setError(null);
    } catch (err) {
      setError({ source: "action", message: userFacingError(err, "生成视频失败，请重试。", { conflict: CLIP_CONFLICT_MESSAGE }) });
    } finally {
      setClipRequestingEventId(null);
    }
  }

  // The coaching list is memoized; hand it one stable function that calls the latest request.
  const requestRenderClipForEventRef = useRef(requestRenderClipForEvent);
  requestRenderClipForEventRef.current = requestRenderClipForEvent;
  const generateClipForEvent = useCallback((event: CoachingEvent) => void requestRenderClipForEventRef.current(event), []);

  async function requestRenderClipAtCurrentTick() {
    if (!replay) {
      return;
    }

    if (currentTickClipJob && playableClipVideo(currentTickClipJob)) {
      playSavedClip(currentTickClipJob, currentTick);
      return;
    }

    setTickClipRequesting(true);
    try {
      await requestOrViewClip(buildTickClipRequest(replay, currentTick, selectedRound, selectedPlayerId), currentTick);
      setError(null);
    } catch (err) {
      setError({ source: "action", message: userFacingError(err, "生成视频失败，请重试。", { conflict: CLIP_CONFLICT_MESSAGE }) });
    } finally {
      setTickClipRequesting(false);
    }
  }

  async function requestOrViewClip(request: RenderClipRequest, tick: number) {
    const existing = savedClipAtTick(renderJobs, tick, request.povSteamId ?? request.playerId ?? null, replay?.video.renderJobId)
      ?? matchingClipJob(renderJobs, request);
    const action = clipRequestAction(existing);
    if (existing && action === "play") {
      playSavedClip(existing, tick);
      return;
    }
    if (action === "wait") return;
    if (replay?.video.status === "ready" && replay.video.url) {
      const currentVideo = replay.video;
      setSelectedClip((current) => retainSelectedClip(current, renderJobs, currentVideo, demoId));
    }
    const response = existing && action === "retry"
      // Requeue the row that failed instead of leaving it behind and starting a
      // second one for the same clip. Both calls return the same shape.
      ? await retryRenderClipJob(demoId, existing.job_id)
      : await createRenderClipJob(demoId, request);
    setRenderJobs((currentJobs) => [response, ...currentJobs.filter((job) => job.job_id !== response.job_id)]);
    // The job is created either way, so surface an offline renderer now rather
    // than leaving the caller on "queued" until the next poll.
    if (response.render_worker) setRenderWorker(response.render_worker);
    if (playableClipVideo(response)) {
      playSavedClip(response, tick);
    } else {
      setReplay((currentReplay) => currentReplay ? { ...currentReplay, video: response.video } : currentReplay);
    }
  }

  // Stable identities, so the memoized VideoSetupPanel skips playback frames.
  const uploadManualVideo = useCallback(async (file: File) => {
    try {
      const video = await uploadDemoVideo(demoId, file);
      setSelectedClip(null);
      setDetectedVideoDuration(null);
      setCurrentVideoTime(video.timeOriginSeconds ?? 0);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError({ source: "action", message: friendlyErrorMessage(err instanceof Error ? err.message : "上传视频失败") });
    }
  }, [demoId]);

  const saveManualVideoCalibration = useCallback(async (calibration: VideoCalibrationUpdate) => {
    try {
      const video = await saveVideoCalibration(demoId, calibration);
      setSelectedClip(null);
      setReplay((currentReplay) =>
        currentReplay ? { ...currentReplay, video } : currentReplay
      );
      setError(null);
    } catch (err) {
      setError({ source: "action", message: friendlyErrorMessage(err instanceof Error ? err.message : "保存校准失败") });
    }
  }, [demoId]);

  async function confirmDelete() {
    const title = status?.name ?? `比赛 ${demoId.slice(0, 8)}`;
    deletingRef.current = true;
    setDeleting(true);
    setDeleteError(null);
    setPlaying(false);
    try {
      await deleteDemo(demoId);
    } catch (err) {
      // A 404 means it is already gone, which is what the player asked for.
      if (requestFailureKind(err) !== "not_found") {
        deletingRef.current = false;
        setDeleting(false);
        setDeleteError(userFacingError(err, "删除失败，请稍后再试。"));
        return;
      }
    }
    // Carried in memory, never in the URL: the name must not land in the address bar or history.
    leaveLibraryNotice(`已永久删除「${title}」`);
    router.replace("/dashboard");
  }

  const openDelete = () => {
    setDeleteError(null);
    setDeleteOpen(true);
  };

  const knownStatus = statusFailure === "not_found" ? null : status;
  const pageTitle = statusFailure === "not_found"
    ? "找不到这场比赛"
    : status
      ? status.name ?? `比赛 ${status.id.slice(0, 8)}`
      : statusFailure === "unreachable" ? "暂时无法打开比赛" : "正在打开比赛";
  const tickClipLabel = tickClipRequesting ? "正在提交"
    : tickClipWorkerOffline ? RENDER_OFFLINE_LABEL
      : currentTickClipJob?.status === "queued" ? "等待生成"
        : clipIsActive(currentTickClipJob) ? "视频生成中"
          : playableClipVideo(currentTickClipJob) ? "观看这一刻的视频" : "生成这一刻的视频";
  const focusedRound = focusedEvent ? replay?.rounds.find((round) => round.roundNumber === focusedEvent.round_number) : undefined;
  // Memoized so the memoized score banner skips playback frames.
  const hasReplay = Boolean(replay);
  const personalEventCount = personalEvents.length;
  const headerFacts = useMemo(() => (
    <>
      {hasReplay && selectedPlayer ? <span className="fact">{personalEventCount} 条建议</span>
        : status?.status === "completed" ? (
          <span className="fact" title="所有玩家合计；选择你的玩家后只显示你的建议">
            <span className="fact-label">全场建议</span>{status.coaching_event_count}
          </span>
        ) : null}
      {status?.archived ? <span className="fact">已归档</span> : null}
      {/* "可以复盘" says nothing the workspace does not; it stays for screen readers only. */}
      {knownStatus ? (
        <span className={`status-badge ${knownStatus.status}${statusBadgeLabel(knownStatus, loadState) === demoStatusDisplayLabel("completed") ? " visually-hidden" : ""}`}>
          {statusBadgeLabel(knownStatus, loadState)}
        </span>
      ) : null}
    </>
  ), [hasReplay, knownStatus, loadState, personalEventCount, selectedPlayer, status]);

  return (
    <main className="app-shell review-detail-shell review-app">
      <header className="topbar">
        <AppBrand />
        <div className="topbar-actions">
          <SessionControls />
        </div>
      </header>

      <section className="page">
        <p className="visually-hidden" aria-live="polite">{progressAnnouncement(sawProcessing, Boolean(replay), loadState)}</p>
        <nav className="review-breadcrumb" aria-label="当前位置">
          <Link href="/dashboard">我的比赛</Link>
          <span aria-hidden="true">›</span>
          <span aria-current="page">{pageTitle}</span>
        </nav>
        {/* A missing or unreachable match has nothing to head: its state card carries the title. */}
        {loadState?.kind === "not_found" || loadState?.kind === "unreachable" ? null : (
        <header className={`panel review-header${replay ? " has-banner" : ""}`}>
          {replay ? (
            <MatchScoreBanner
              title={pageTitle}
              teams={teams}
              mapName={mapDisplayName(knownStatus?.map_name ?? replay.mapName)}
              date={status?.completed_at ?? null}
              roundCount={replay.rounds.length}
              yourTeamKey={yourTeamKey}
              reviewedTeamKey={reviewedTeamKey}
            >
              {headerFacts}
            </MatchScoreBanner>
          ) : (
          <div className="panel-bar review-header-bar">
            <div className="detail-title">
              <h1 className="panel-bar-title">{pageTitle}</h1>
              {statusFailure === "not_found" || (!status && statusFailure) ? null : <div className="detail-meta facts">
                {knownStatus ? <span className="fact"><span className="fact-label">地图</span><span className="detail-map">{mapDisplayName(knownStatus.map_name)}</span></span> : null}
                <span className="fact">{status?.status === "completed" ? status.round_count : "—"} 回合</span>
                {headerFacts}
              </div>}
            </div>
          </div>
          )}
          {/* Beside the bar on wide screens (same grey), under it on phones; the first-run picker is the panel body. */}
          {replay ? (
            <PersonalReviewPanel
              key={savedIdentity}
              savedIdentity={savedIdentity}
              match={preferredPlayerMatch}
              players={replay.players}
              selectedPlayer={selectedPlayer}
              summary={personalSummary}
              preferenceSaved={preferenceSaved}
              onSaveIdentity={saveIdentity}
              onSelectPlayer={selectPlayer}
              onSeek={seekToFinding}
            />
          ) : null}
        </header>
        )}

        {statusFailure === "unreachable" && status ? (
          <ErrorBanner message="网络连接中断，正在自动重试…" retryLabel="立即重试" onRetry={() => void refreshStatus()} />
        ) : null}
        {error ? <ErrorBanner message={error.message} onDismiss={() => setError(null)} /> : null}
        {parseRetryError ? <ErrorBanner message={parseRetryError} onDismiss={() => setParseRetryError(null)} /> : null}

        {!replay ? (
          loadState === null || loadState.kind === "connecting" || loadState.kind === "loading_replay" ? (
            <ReviewSkeleton label={loadState?.kind === "loading_replay" ? "正在载入回放…" : "正在打开比赛…"} />
          ) : (
            <DemoStateCard
              state={loadState}
              parseRetrying={parseRetrying}
              replayReloading={replayReloading}
              onRetryParse={() => void retryParse()}
              onRetryStatus={() => void refreshStatus()}
              onReloadReplay={() => void reloadReplay()}
              onDelete={openDelete}
              technicalDetails={devTools && status ? <DetailSummary items={summaryItems} /> : null}
            />
          )
        ) : (
          <>
            <RoundStrip
              replay={replay}
              coachingEvents={personalEvents}
              selectedPlayerId={selectedPlayerId}
              currentRoundNumber={currentRoundNumber}
              selectedRound={selectedRound}
              onSelectRound={changeRound}
            />
            <div className="review-layout">
            <div className="review-main-column">
            <section id="player" className="panel review-stage" aria-label="回放" tabIndex={-1} ref={stageRef}>
              <div className="panel-bar review-stage-toolbar">
                {showVideoControls || utilityTab !== "hidden" ? (
                  <div className="panel-bar-tabs review-view-switch" role="group" aria-label="回放视图">
                    <button type="button" className={`panel-tab${firstPersonSelected || finderSelected ? "" : " selected"}`}
                      aria-pressed={!firstPersonSelected && !finderSelected} onClick={() => setViewMode("map")}>
                      战术回放
                    </button>
                    {utilityTab !== "hidden" ? (
                      <button type="button" className={`panel-tab${finderSelected ? " selected" : ""}`} aria-pressed={finderSelected}
                        onClick={showUtilityFinder}>
                        道具反查
                      </button>
                    ) : null}
                    {/* Always clickable: without a clip for this moment the stage explains why and what to do. */}
                    {showVideoControls ? (
                      <button type="button" className={`panel-tab${firstPersonSelected ? " selected" : ""}`} aria-pressed={firstPersonSelected}
                        onClick={showFirstPerson}>
                        第一人称
                      </button>
                    ) : null}
                  </div>
                ) : <h2 className="panel-bar-title">战术回放</h2>}
                {renderClips ? <button className="text-button review-clip-button" type="button"
                  disabled={tickClipRequesting || clipIsActive(currentTickClipJob) || !selectedPlayerId}
                  title={tickClipWorkerOffline ? RENDER_OFFLINE_DETAIL : undefined}
                  onClick={() => void requestRenderClipAtCurrentTick()}>
                  {tickClipLabel}
                </button> : null}
              </div>
              {findingStripOpen && focusedEvent ? (
                <div className="review-finding-strip" role="status">
                  <span className="review-finding-strip-label">当前建议</span>
                  <strong>{coachingCopy(focusedEvent).title}</strong>
                  <small>第 {focusedEvent.round_number} 回合 <span className="review-finding-strip-time">{roundTimeAt(focusedEvent.tick_start, focusedRound, replay.tickRate)}</span></small>
                  <button type="button" className="text-button" onClick={returnToFinding}>
                    {focusedFinding?.fromCard ? "返回建议" : "查看建议"}
                  </button>
                  <button type="button" className="review-finding-strip-close" aria-label="收起当前建议" onClick={() => setFindingStripOpen(false)}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </div>
              ) : null}
              {focusPlayer && !finderSelected && !videoDrivesClock && !showFirstPersonExplainer ? (
                <div className="review-focus-strip" role="status">
                  <span>只看 <strong>{focusPlayer.name || "这名玩家"}</strong></span>
                  <button type="button" className="text-button" onClick={clearFocusPlayer}>显示全部</button>
                </div>
              ) : null}
              <div className={`review-main-canvas ${finderSelected ? "showing-finder" : videoDrivesClock ? "showing-video" : showFirstPersonExplainer ? "showing-explainer" : "showing-map"}`}>
              {finderSelected ? (
                utilityTab === "available" ? (
                  <UtilityFinder replay={replay} teams={teams} currentRound={selectedRound} state={finderState}
                    onStateChange={setFinderState} onJump={jumpToUtility} />
                ) : <UtilityFinderPending />
              ) : videoDrivesClock ? (
              <FirstPersonReplay
                compact
                ref={firstPersonReplayRef}
                replay={replay}
                currentTick={currentTick}
                playing={playing}
                speed={speed}
                playbackState={videoPlayback}
                mediaUnavailable={videoUnavailable}
                renderRequesting={renderRequesting}
                renderClipRequesting={tickClipRequesting}
                latestRenderClipJob={latestRenderClipJob}
                currentTickClipJob={currentTickClipJob}
                renderClipPlayerSelected={Boolean(selectedPlayerId)}
                renderWorker={renderWorker}
                onRequestMockRender={requestMockRender}
                onRequestRenderClip={renderClips ? requestRenderClipAtCurrentTick : undefined}
                onVideoTickChange={updateCoordinateFromTick}
                onVideoUnavailable={setUnavailableVideoIdentity}
                onViewVideoClip={viewVideoClip}
                onVideoDurationChange={setDetectedVideoDuration}
                onVideoTimeChange={devTools && inspectorOpen ? setCurrentVideoTime : undefined}
              />
              ) : showFirstPersonExplainer ? (
                <FirstPersonExplainer
                  canGenerate={renderClips}
                  playerSelected={Boolean(selectedPlayerId)}
                  workerOffline={renderWorkerOffline(renderWorker)}
                  clipJob={currentTickClipJob}
                  requestLabel={tickClipLabel}
                  requesting={tickClipRequesting}
                  savedClipCount={playableClipCount}
                  devTools={devTools}
                  onRequest={() => void requestRenderClipAtCurrentTick()}
                  onChoosePlayer={revealPlayerPicker}
                  onShowSavedClips={showSavedClips}
                  onBackToMap={() => setViewMode("map")}
                />
              ) : (
                <ReplayViewer replay={scopedReplay ?? replay} matchReplay={replay} currentTick={currentTick}
                  selectedPlayerId={selectedPlayerId} onSelectPlayer={selectPlayer} variant="featured"
                  levelMode={mapLevelMode} onLevelModeChange={setMapLevelMode} teamNames={matchSummaryTeams}
                  overlayAbove={utilityTab === "available" ? utilityOverlay : undefined}
                  focusPlayerId={focusPlayer?.id ?? null} />
              )}
              </div>
              <Timeline currentTick={currentTick} selectedRound={selectedRound} rounds={replay.rounds}
                tickRate={replay.tickRate} events={personalEvents} parserEvents={scopedReplay?.events ?? []}
                selectedPlayerName={selectedPlayer?.name ?? null} selectedPlayerId={selectedPlayerId}
                onSeek={manualSeek} onSeekFinding={seekToFindingFromTimeline} />
              <ReviewCommandBar
                selectedRound={selectedRound}
                roundTime={roundTimeAt(currentTick, selectedRoundData, replay.tickRate)}
                roundDuration={selectedRoundData ? formatRoundTime((selectedRoundData.endTick - selectedRoundData.startTick) / (replay.tickRate > 0 ? replay.tickRate : 64)) : undefined}
                currentPovName={selectedPlayer?.name ?? "请选择玩家"}
                playing={playing}
                speed={speed}
                previousFinding={previousFinding}
                nextFinding={nextFinding}
                nextRoundNumber={atRoundEnd && !playing && nextRoundData ? nextRoundData.roundNumber : null}
                shortcutsOpen={shortcutsOpen}
                onTogglePlay={togglePlay}
                onSpeedChange={setSpeed}
                onPreviousFinding={goToPreviousFinding}
                onNextFinding={goToNextFinding}
                onSeekBy={seekBy}
                onNextRound={playNextRound}
                onToggleShortcuts={toggleShortcuts}
              />
            </section>
            {/* Only when there is something to say: the video failed, or a saved one can be opened. */}
            {showVideoControls && (videoUnavailable || (videoPlayback !== "active" && replay.video.url)) ? (
              <div className={`review-media-note${videoUnavailable ? " is-error" : ""}`} role="status">
                <span>{videoUnavailable ? "视频暂时无法播放，已切换到战术回放。" : "当前时刻使用战术回放。"}</span>
                {replay.video.url && !videoUnavailable ? <button type="button" className="text-button" onClick={viewVideoClip}>打开已保存的视频</button> : null}
              </div>
            ) : null}
            <RoundReviewPanel
              replay={replay}
              coachingEvents={personalEvents}
              selectedPlayerId={selectedPlayerId}
              selectedPlayerName={selectedPlayer?.name ?? null}
              currentRoundNumber={currentRoundNumber}
              selectedRound={selectedRound}
              onSeek={manualSeek}
            />
            </div>
              <CoachingPanel
                key={selectedPlayer?.id ?? "no-player"}
                events={personalEvents}
                selectedPlayerName={selectedPlayer?.name ?? null}
                players={replay.players}
                rounds={replay.rounds}
                tickRate={replay.tickRate}
                activeEventIds={activeEventIds}
                selectedRound={selectedRound}
                renderJobByEventId={renderJobByEventId}
                requestingEventId={clipRequestingEventId}
                onSeek={seekToFinding}
                onGenerateClip={renderClips ? generateClipForEvent : undefined}
                onFeedback={submitCoachingFeedback}
                onChoosePlayer={revealPlayerPicker}
              />
            </div>
            {/* S9 数据 section (UI-B) */}
            <MatchAnalysis replay={replay} player={selectedPlayer} onSeekTick={jumpToTick} onSelectRound={jumpToRound}
              onChoosePlayer={revealPlayerPicker} />
            <Scoreboard teams={teams} stats={scoreboardStats} reviewedPlayerId={selectedPlayerId} />
            {showVideoControls ? (
              <details className="review-saved-clips" id="saved-clips" ref={savedClipsRef}>
                <summary><span>已保存的视频</span>
                  <span className="saved-clips-count">{playableClipCount} 段可观看</span>
                  {hasActiveRenderClipJob ? <span role="status">有视频正在生成</span> : null}
                </summary>
                <ClipLibrary jobs={personalClips} playerName={selectedPlayer?.name ?? null} rounds={replay.rounds}
                  canGenerate={renderClips} selectedJobId={replay.video.renderJobId ?? null} onPlay={playSavedClip} />
              </details>
            ) : null}
            <details className="review-inspector" onToggle={(event) => setInspectorOpen(event.currentTarget.open)}>
              <summary><span>高级工具</span><small>{devTools ? "视频校准、生成记录与技术详情" : renderClips ? "视频生成状态与比赛信息" : "比赛信息"}</small></summary>
              {inspectorOpen ? (
                <div className="review-inspector-content">
                  {devTools ? <button className="secondary-button compact-button" type="button" disabled={renderRequesting}
                    onClick={() => void requestMockRender()}>{renderRequesting ? "提交中" : "创建模拟视频任务（开发测试）"}</button> : null}
                  {renderClips || devTools ? <section className="review-support-bay" aria-label="视频生成与校准">
                    {renderClips ? <RenderOperatorPanel
                      video={replay.video}
                      latestJob={latestRenderClipJob}
                      renderWorker={renderWorker}
                      jobCount={renderJobs.length}
                      refreshing={renderJobsRefreshing}
                      onRefresh={refreshRenderOperator}
                      devTools={devTools}
                    /> : null}
                    {devTools ? <VideoSetupPanel
                      currentVideoTime={currentVideoTime}
                      detectedDurationSeconds={detectedVideoDuration}
                      video={replay.video}
                      calibrationDisabled={Boolean(replay.video.renderJobId)}
                      onSaveCalibration={saveManualVideoCalibration}
                      onUploadVideo={uploadManualVideo}
                    /> : null}
                  </section> : null}
                  {status ? <DetailSummary items={summaryItems} /> : null}
                  {detailDiagnostics ? <ReplayDiagnosticsPanel diagnostics={detailDiagnostics} /> : null}
                  <button className="danger-button compact-button" type="button" onClick={openDelete}>
                    删除这场比赛
                  </button>
                </div>
              ) : null}
            </details>
          </>
        )}
      </section>
      <SiteFooter />

      <ConfirmDialog
        open={deleteOpen}
        title="永久删除这场比赛？"
        confirmLabel="永久删除"
        busy={deleting}
        busyLabel="正在删除…"
        error={deleteError}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          setDeleteOpen(false);
          setDeleteError(null);
        }}
      >
        <p>「{pageTitle}」的这些内容会被永久删除：</p>
        <ul>
          <li>比赛文件 .dem</li>
          <li>回放数据</li>
          <li>复盘建议和你的评价</li>
        </ul>
        <p className="confirm-dialog-note">此操作无法撤销。</p>
      </ConfirmDialog>
    </main>
  );
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

// Inputs that are not typed into: the slider, checkboxes, radios and buttons leave the review keys working.
const NON_TEXT_INPUT_TYPES = new Set(["range", "checkbox", "radio", "button", "submit", "reset", "image", "file", "color"]);
// What a closed <select> does itself: open, and step through its options.
const SELECT_OWN_KEYS = new Set([" ", "Spacebar", "Enter", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

// True while the focused element needs this key: typing, picking an option, or moving between radios.
function keepsKeyForItself(target: HTMLElement, key: string): boolean {
  if (target.isContentEditable || target instanceof HTMLTextAreaElement) return true;
  if (target instanceof HTMLSelectElement) {
    // A select whose options cannot be reached by a shortcut letter (the speed select) opts in.
    return !target.hasAttribute("data-review-shortcuts") || SELECT_OWN_KEYS.has(key);
  }
  if (target instanceof HTMLInputElement) {
    if (!NON_TEXT_INPUT_TYPES.has(target.type)) return true;
    return target.type === "radio" && key.startsWith("Arrow");
  }
  return false;
}

function statusBadgeLabel(status: DemoStatus, loadState: DetailLoadState | null): string {
  if (loadState?.kind === "processing") return demoStatusDisplayLabel(PROCESSING_STEP_STATUSES[loadState.step]);
  if (status.status === "failed") return demoStatusDisplayLabel("failed");
  if (loadState?.kind === "replay_unavailable" || loadState?.kind === "replay_load_failed") return "回放不可用";
  return demoStatusDisplayLabel("completed");
}

// Spoken only once this page has watched the demo being processed, so opening a
// ready demo stays quiet while a fresh upload announces each step and the result.
function progressAnnouncement(sawProcessing: boolean, replayReady: boolean, loadState: DetailLoadState | null): string {
  if (!sawProcessing) return "";
  if (replayReady) return "比赛处理完成，可以开始复盘。";
  if (loadState?.kind === "processing") return processingHeadline(loadState.step);
  if (loadState?.kind === "loading_replay") return "比赛处理完成，正在载入回放…";
  if (loadState?.kind === "failed") return `比赛处理失败。${loadState.failure.message}`;
  return "";
}
