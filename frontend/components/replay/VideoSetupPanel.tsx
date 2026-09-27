"use client";

import { memo, useEffect, useId, useState } from "react";

import type { VideoCalibrationUpdate } from "@/lib/api";
import type { ReplayVideo } from "@/types/replay";

interface VideoSetupPanelProps {
  currentVideoTime: number;
  detectedDurationSeconds: number | null;
  video: ReplayVideo;
  calibrationDisabled?: boolean;
  onSaveCalibration: (calibration: VideoCalibrationUpdate) => Promise<void>;
  onUploadVideo: (file: File) => Promise<void>;
}

// Dev/QA bridge: attach a manual MP4 and line it up with demo ticks.
export const VideoSetupPanel = memo(function VideoSetupPanel({
  currentVideoTime,
  detectedDurationSeconds,
  video,
  calibrationDisabled = false,
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
      setMessage("视频已上传");
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传视频失败");
    } finally {
      setUploading(false);
    }
  }

  async function handleSave() {
    if (calibrationDisabled) return;
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
      setError("校准值必须是有效数字。");
      return;
    }
    if (parsedTickRate <= 0) {
      setError("tickRate 必须大于 0。");
      return;
    }
    if (parsedTickEnd < parsedTickStart) {
      setError("tickEnd 不能小于 tickStart。");
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
      setMessage("校准已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存校准失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel video-setup-panel" aria-label="手动视频校准">
      <div className="panel-bar video-setup-header">
        <h2 className="panel-bar-title">手动视频校准（开发测试）</h2>
        <span className="panel-bar-meta">{video.source} / {video.status}</span>
        <label className={`secondary-button compact-button ${uploading ? "disabled-label" : ""}`} htmlFor={fileInputId}>
          {uploading ? "上传中" : "上传 MP4"}
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
          <dt>视频文件</dt>
          <dd>{video.url ? "已关联私有视频" : "未关联视频"}</dd>
        </div>
        <div>
          <dt>时长</dt>
          <dd>
            {formatSeconds(video.durationSeconds)}
            {detectedDurationSeconds ? `（检测到 ${formatSeconds(detectedDurationSeconds)}）` : ""}
          </dd>
        </div>
        <div>
          <dt>Tick 范围</dt>
          <dd>{video.tickStart} - {video.tickEnd}</dd>
        </div>
        <div>
          <dt>起点</dt>
          <dd>{formatSeconds(video.timeOriginSeconds ?? 0)}</dd>
        </div>
      </dl>

      {calibrationDisabled ? <p className="setup-message">已保存的视频片段沿用生成时的时间对齐；上传 MP4 后才能手动校准。</p> : null}
      <fieldset className="calibration-grid" disabled={calibrationDisabled} style={{ border: 0, margin: 0 }}>
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
      </fieldset>

      <div className="calibration-actions">
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={!video.url || calibrationDisabled}
          onClick={() => setTimeOriginSeconds(currentVideoTime.toFixed(2))}
        >
          用当前视频时间作为 tickStart 起点
        </button>
        <button
          className="primary-button compact-button"
          type="button"
          disabled={saving || calibrationDisabled}
          onClick={() => void handleSave()}
        >
          {saving ? "保存中" : "保存校准"}
        </button>
      </div>

      {message ? <div className="setup-message">{message}</div> : null}
      {error ? <div className="setup-error">{error}</div> : null}
    </section>
  );
});

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
