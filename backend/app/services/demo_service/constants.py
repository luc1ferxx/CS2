"""Tunables, status vocabularies and user-facing copy shared by the DemoService components."""



RENDER_CLIP_JOB_TYPE = "render_clip"
RENDER_FAILED_ERROR_CODE = "RENDER_FAILED"
RENDER_FAILED_PUBLIC_MESSAGE = "Render output could not be produced."
RENDER_WORKER_UNAVAILABLE_ERROR_CODE = "RENDER_WORKER_UNAVAILABLE"
RENDER_CLIP_NOT_CONNECTED_ERROR = (
    "GPU worker not connected for render_clip. "
    "A separate Windows/Linux GPU worker or manual operator must process this job."
)
RENDER_TIMED_OUT_ERROR_CODE = "RENDER_TIMED_OUT"
RENDER_TIMED_OUT_PUBLIC_MESSAGE = (
    "Render did not finish after repeated attempts. Generate the clip again."
)
# Distinct from RENDER_TIMED_OUT, which means a worker took the clip and never
# came back. This one means no worker ever took it at all, so the copy has to
# point at the renderer rather than at the clip.
RENDER_QUEUE_TIMED_OUT_ERROR_CODE = "RENDER_QUEUE_TIMED_OUT"
RENDER_QUEUE_TIMED_OUT_PUBLIC_MESSAGE = (
    "No render worker picked this clip up. Start the renderer, then retry the render."
)
REPLAY_ARTIFACT_MISSING_ERROR_CODE = "REPLAY_ARTIFACT_MISSING"
REPLAY_ARTIFACT_MISSING_MESSAGE = (
    "Replay data for this demo is no longer readable. Re-parse the demo to rebuild it."
)
PARSE_JOB_TYPES = ("real_parse", "mock_parse")
RENDER_CLIP_DEFAULT_PRESET = "event_clip_v1"
RENDER_WORKER_MANIFEST_VERSION = "render_worker_v1"
DEMO_STATUS_ORDER = ("queued", "parsing", "analyzing", "completed", "failed")
ACTIVE_DEMO_STATUSES = {"queued", "parsing", "analyzing"}
ACTIVE_PARSE_JOB_STATUSES = {"queued", "pending", "processing"}
# Purely a display flag: DemoIngestionStatus.stale turns true here so the UI can
# say "this has been going a while". It is NOT a reclaim threshold -- reclaiming
# is gated on settings.parse_reclaim_after_seconds, which has to stay above the
# parse timeout. Do not collapse the two.
STALE_PARSE_AFTER_SECONDS = 15 * 60
# A parse job abandoned so many times that the demo itself is the likely cause.
# Failing it is what restores the user's way out: _parse_retryable() only returns
# true once the job is off ACTIVE_PARSE_JOB_STATUSES, so a row left on
# "processing" forever means a demo page stuck on "parsing" with the retry button
# disabled and no way to unstick it short of a hand-written UPDATE.
PARSE_ABANDONED_ERROR_CODE = "PARSE_ABANDONED"
PARSE_ABANDONED_MESSAGE = (
    "Parsing was interrupted repeatedly and has been stopped. Retry the upload."
)
# A render_clip job only leaves "rendering" when the worker that claimed it comes
# back. When that process dies -- crash, reboot, Ctrl+C -- nothing else touches
# the row: next_render_clip_job() ignores "rendering", so no replacement worker
# can ever see it and the demo page sits on "generating" forever. These three
# bound the recovery.
#
# The 30 minute backstop deliberately equals diagnostics.RENDER_WORKER_BUSY_GRACE_SECONDS,
# which answers the same question from the other side ("how long may a rendering
# job still count as proof the worker is alive"). Two different numbers would
# open a window where a job is neither trusted as live nor reclaimed as dead.
RENDER_CLIP_STALE_AFTER_SECONDS = 30 * 60
# Headroom for the fast path in GET /render-worker/jobs/next: a job claimed this
# recently is never reclaimed, whatever the caller looks like.
RENDER_CLIP_IDLE_RECLAIM_SECONDS = 60
# attempts is incremented by claim_render_clip_job, so this counts claims, not
# reclaims: a clip that kills the renderer gets three goes and then fails for
# good instead of occupying the queue forever.
RENDER_CLIP_MAX_ATTEMPTS = 3
# The statuses a render_clip job can sit in without any worker owning it.
# claim_render_clip_job accepts both, so both are waiting to be picked up and
# both wait forever when nothing is polling.
UNCLAIMED_RENDER_CLIP_STATUSES = ("queued", "pending")
STEAM_MATCH_PLAYER_LIMIT = 20
STEAM_MATCH_PLAYER_ID_LIMIT = 64
STEAM_MATCH_PLAYER_NAME_LIMIT = 64
STEAM_MATCH_PARSE_FAILED_MESSAGE = (
    "Imported Demo could not be parsed. Upload a valid .dem file manually or retry later."
)
DEMO_STATUS_SEARCH_ALIASES = {
    "uploaded": "queued",
    "upload": "queued",
    "ready": "completed",
    "complete": "completed",
}
