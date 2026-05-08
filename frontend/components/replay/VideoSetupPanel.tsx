"use client";

import { Save, TimerReset, Upload } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type { VideoCalibrationUpdate } from "@/lib/api";
import type { ReplayVideo } from "@/types/replay";

interface VideoSetupPanelProps {
  currentVideoTime: number;
  detectedDurationSeconds: number | null;
  video: ReplayVideo;
  onSaveCalibration: (calibration: VideoCalibrationUpdate) => Promise<void>;
  onUploadVideo: (file: File) => Promise<void>;
}

export function VideoSetupPanel({
  currentVideoTime,
  detectedDurationSeconds,
  video,
  onSaveCalibration,
  onUploadVideo
}: VideoSetupPanelProps) {
  const fileInputId = useId();
  const [tickStart, setTickStart] = useState(String(video.tickStart));
  const [tickEnd, setTickEnd] = useState(String(video.tickEnd));
  const [tickRate, setTickRate] = useState(String(video.tickRate));
  const [timeOriginSeconds, setTimeOriginSeconds] = useState(String(video.timeOriginSeconds ?? 0));
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTickStart(String(video.tickStart));
    setTickEnd(String(video.tickEnd));
    setTickRate(String(video.tickRate));
    setTimeOriginSeconds(String(video.timeOriginSeconds ?? 0));
  }, [video.tickEnd, video.tickRate, video.tickStart, video.timeOriginSeconds]);

  async function handleUpload(file: File | undefined) {
    if (!file) {
      return;
    }
    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      await onUploadVideo(file);
      setMessage("Video uploaded");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload video");
    } finally {
      setUploading(false);
    }
  }

  async function handleSave() {
    const parsedTickStart = parseInteger(tickStart);
    const parsedTickEnd = parseInteger(tickEnd);
    const parsedTickRate = parseInteger(tickRate);
    const parsedTimeOriginSeconds = parseNumber(timeOriginSeconds);

    if (
      parsedTickStart === null ||
      parsedTickEnd === null ||
      parsedTickRate === null ||
      parsedTimeOriginSeconds === null
    ) {
      setError("Calibration values must be valid numbers");
      return;
    }
    if (parsedTickRate <= 0) {
      setError("tickRate must be greater than zero");
      return;
    }
    if (parsedTickEnd < parsedTickStart) {
      setError("tickEnd must be greater than or equal to tickStart");
      return;
    }

    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await onSaveCalibration({
        durationSeconds: detectedDurationSeconds ?? video.durationSeconds,
        tickStart: parsedTickStart,
        tickEnd: parsedTickEnd,
        tickRate: parsedTickRate,
        timeOriginSeconds: parsedTimeOriginSeconds
      });
      setMessage("Calibration saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save calibration");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel video-setup-panel" aria-label="Video setup and sync calibration">
      <div className="video-setup-header">
        <div>
          <h2>Video Setup / Sync Calibration</h2>
          <span>{video.source} / {video.status}</span>
        </div>
        <label className={`secondary-button compact-button ${uploading ? "disabled-label" : ""}`} htmlFor={fileInputId}>
          <Upload size={14} />
          {uploading ? "Uploading" : "Upload MP4"}
        </label>
        <input
          id={fileInputId}
          className="file-input"
          type="file"
          accept=".mp4,video/mp4"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0];
            void handleUpload(file);
            event.target.value = "";
          }}
        />
      </div>

      <dl className="video-metadata-grid">
        <div>
          <dt>URL</dt>
          <dd className="metadata-url">{video.url ?? "No video bound"}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>
            {formatSeconds(video.durationSeconds)}
            {detectedDurationSeconds ? ` detected ${formatSeconds(detectedDurationSeconds)}` : ""}
          </dd>
        </div>
        <div>
          <dt>Tick range</dt>
          <dd>{video.tickStart} - {video.tickEnd}</dd>
        </div>
        <div>
          <dt>Origin</dt>
          <dd>{formatSeconds(video.timeOriginSeconds ?? 0)}</dd>
        </div>
      </dl>

      <div className="calibration-grid">
        <label>
          <span>timeOriginSeconds</span>
          <input
            className="setup-input"
            type="number"
            min="0"
            step="0.01"
            value={timeOriginSeconds}
            onChange={(event) => setTimeOriginSeconds(event.target.value)}
          />
        </label>
        <label>
          <span>tickStart</span>
          <input
            className="setup-input"
            type="number"
            step="1"
            value={tickStart}
            onChange={(event) => setTickStart(event.target.value)}
          />
        </label>
        <label>
          <span>tickEnd</span>
          <input
            className="setup-input"
            type="number"
            step="1"
            value={tickEnd}
            onChange={(event) => setTickEnd(event.target.value)}
          />
        </label>
        <label>
          <span>tickRate</span>
          <input
            className="setup-input"
            type="number"
            min="1"
            step="1"
            value={tickRate}
            onChange={(event) => setTickRate(event.target.value)}
          />
        </label>
      </div>

      <div className="calibration-actions">
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={!video.url}
          onClick={() => setTimeOriginSeconds(currentVideoTime.toFixed(2))}
        >
          <TimerReset size={14} />
          Use current video time as tickStart origin
        </button>
        <button
          className="primary-button compact-button"
          type="button"
          disabled={saving}
          onClick={() => void handleSave()}
        >
          <Save size={14} />
          {saving ? "Saving" : "Save Calibration"}
        </button>
      </div>

      {message ? <div className="setup-message">{message}</div> : null}
      {error ? <div className="setup-error">{error}</div> : null}
    </section>
  );
}

function parseInteger(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return Math.round(parsed);
}

function parseNumber(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatSeconds(seconds: number): string {
  return `${Number(seconds || 0).toFixed(2)}s`;
}
