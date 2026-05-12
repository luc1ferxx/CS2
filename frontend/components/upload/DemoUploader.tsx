"use client";

import { FileUp, UploadCloud } from "lucide-react";
import { useRef } from "react";

interface DemoUploaderProps {
  disabled: boolean;
  inputId?: string;
  onMockUpload: () => void;
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
    <div className="upload-actions">
      <input
        id={inputId}
        ref={inputRef}
        className="file-input"
        type="file"
        accept=".dem"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) {
            onDemoUpload(file);
          }
          event.currentTarget.value = "";
        }}
      />
      <div className="upload-action-stack">
        <button
          className="secondary-button"
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
          title="Upload a local .dem and queue the real parser flow"
        >
          <FileUp size={17} strokeWidth={2.2} />
          Upload .dem
        </button>
        <span>real parser flow</span>
      </div>
      <div className="upload-action-stack">
        <button
          className="primary-button"
          type="button"
          onClick={onMockUpload}
          disabled={disabled}
          title="Create a synthetic demo and queue a mock parse job"
        >
          <UploadCloud size={17} strokeWidth={2.2} />
          Create mock demo
        </button>
        <span>fast UI smoke</span>
      </div>
    </div>
  );
}
