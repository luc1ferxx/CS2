"use client";

import { FileUp, Loader2, UploadCloud } from "lucide-react";
import { useRef } from "react";

interface DemoUploaderProps {
  disabled: boolean;
  inputId?: string;
  // Omitted when the API does not serve mock demos (production).
  onMockUpload?: () => void;
  onDemoUpload: (file: File) => void;
}

export function DemoUploader({
  disabled,
  inputId = "demo-upload-input",
  onMockUpload,
  onDemoUpload
}: DemoUploaderProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

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
          className="primary-button"
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
          title="上传 .dem 比赛文件，自动准备回放和复盘建议"
        >
          {disabled ? <Loader2 size={17} className="spin-icon" /> : <FileUp size={17} strokeWidth={2.2} />}
          {disabled ? "正在添加…" : "上传比赛"}
        </button>
        <span>.dem 比赛文件</span>
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
