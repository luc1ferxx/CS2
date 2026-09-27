"use client";

import { useRef } from "react";

interface DemoUploaderProps {
  disabled: boolean;
  // Replaces the label while something is being added ("上传中 42%").
  busyLabel?: string | null;
  // Quota text beside the buttons ("今天还可上传 7 场"); nothing when there is no limit.
  hint?: string | null;
  // Why the button is disabled when nothing is running (quota used up).
  disabledReason?: string | null;
  inputId?: string;
  // Omitted when the API does not serve mock demos (production).
  onMockUpload?: () => void;
  onDemoUpload: (file: File) => void;
}

// The library's header-bar actions: 示例比赛 (dev only) and the one primary 上传 .dem.
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

  return (
    <div className="panel-bar-actions lib-upload" role="group" aria-label="添加比赛">
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
      {hint ? <span className="lib-upload-hint">{hint}</span> : null}
      {onMockUpload ? (
        <button
          className="secondary-button compact-button"
          type="button"
          onClick={onMockUpload}
          disabled={disabled}
          title="创建一场模拟比赛（模拟数据），体验复盘功能"
        >
          示例比赛
        </button>
      ) : null}
      <button
        id={`${inputId}-button`}
        className="primary-button compact-button"
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        title={disabledReason ?? "上传 .dem 比赛文件，自动准备回放和复盘建议"}
      >
        {busyLabel ?? "上传 .dem"}
      </button>
    </div>
  );
}
