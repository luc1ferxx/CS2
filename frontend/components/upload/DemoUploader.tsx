"use client";

import { UploadCloud } from "lucide-react";

interface DemoUploaderProps {
  disabled: boolean;
  onMockUpload: () => void;
}

export function DemoUploader({ disabled, onMockUpload }: DemoUploaderProps) {
  return (
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
  );
}
