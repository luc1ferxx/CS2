"use client";

import { FileUp, UploadCloud } from "lucide-react";
import { useRef } from "react";

interface DemoUploaderProps {
  disabled: boolean;
  onMockUpload: () => void;
  onDemoUpload: (file: File) => void;
}

export function DemoUploader({ disabled, onMockUpload, onDemoUpload }: DemoUploaderProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="upload-actions">
      <input
        ref={inputRef}
        className="file-input"
        type="file"
        accept=".dem,.zip"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) {
            onDemoUpload(file);
          }
          event.currentTarget.value = "";
        }}
      />
      <button
        className="secondary-button"
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        title="Upload a local .dem or .zip and queue a real parser spike job"
      >
        <FileUp size={17} strokeWidth={2.2} />
        Demo Upload
      </button>
      <button
        className="primary-button"
        type="button"
        onClick={onMockUpload}
        disabled={disabled}
        title="Create a synthetic demo and queue a mock parse job"
      >
        <UploadCloud size={17} strokeWidth={2.2} />
        Mock Upload
      </button>
    </div>
  );
}
