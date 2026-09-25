"use client";

import { FileUp, Loader2, UploadCloud } from "lucide-react";
import { useRef } from "react";

interface DemoUploaderProps {
  disabled: boolean;
  // Shown with a spinner while something is being added ("上传中 42%").
  busyLabel?: string | null;
  // The line under the button; defaults to what the button accepts.
  hint?: string | null;
  // Why the button is disabled when nothing is running (quota used up).
  disabledReason?: string | null;
  inputId?: string;
  // Omitted when the API does not serve mock demos (production).
  onMockUpload?: () => void;
  onDemoUpload: (file: File) => void;
}

export function DemoUploader({
  disabled,
  busyLabel = null,
  hint = null,
  disabledReason = null,
  inputId = "demo-upload-input",
  onMockUpload,
  onDemoUpload
}: DemoUploaderProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const busy = Boolean(busyLabel);

  return (
    <div className="upload-actions" aria-label="添加比赛">
      <input
        id={inputId}
        ref={inputRef}
        className="file-input"
        type="file"
        accept=".dem"
        aria-label="选择 .dem 比赛文件"
        disabled={disabled}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) {
            onDemoUpload(file);
          }
          event.currentTarget.value = "";
        }}
      />
      <div className="upload-action-stack upload-real-action">
        <button
          id={`${inputId}-button`}
          className="primary-button"
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
          title={disabledReason ?? "上传 .dem 比赛文件，自动准备回放和复盘建议"}
        >
          {busy ? <Loader2 size={17} className="spin-icon" /> : <FileUp size={17} strokeWidth={2.2} />}
          {busyLabel ?? "上传比赛"}
        </button>
        <span className="upload-action-hint">{hint ?? ".dem 比赛文件"}</span>
      </div>
      {onMockUpload ? (
        <div className="upload-action-stack upload-mock-action">
          <button
            className="secondary-button"
            type="button"
            onClick={onMockUpload}
            disabled={disabled}
            title="创建一场模拟比赛，体验复盘功能"
          >
            <UploadCloud size={17} strokeWidth={2.2} />
            示例比赛
          </button>
          <span>模拟数据</span>
        </div>
      ) : null}
    </div>
  );
}
