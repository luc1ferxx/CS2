"use client";

import { RefreshCcw, X } from "lucide-react";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  onDismiss?: () => void;
}

export function ErrorBanner({ message, onRetry, retryLabel = "重试", onDismiss }: ErrorBannerProps) {
  return (
    <div className="error-panel error-banner" role="alert">
      <span>{message}</span>
      {onRetry || onDismiss ? (
        <div className="error-banner-actions">
          {onRetry ? (
            <button className="secondary-button compact-button" type="button" onClick={onRetry}>
              <RefreshCcw size={14} aria-hidden="true" />
              {retryLabel}
            </button>
          ) : null}
          {onDismiss ? (
            <button className="icon-button" type="button" aria-label="关闭提示" onClick={onDismiss}>
              <X size={15} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
