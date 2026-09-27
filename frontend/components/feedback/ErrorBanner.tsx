"use client";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  onDismiss?: () => void;
}

// A notice with a red left mark: the message, then retry as a text link and an icon-only close.
export function ErrorBanner({ message, onRetry, retryLabel = "重试", onDismiss }: ErrorBannerProps) {
  return (
    <div className="error-panel error-banner" role="alert">
      <span>{message}</span>
      {onRetry || onDismiss ? (
        <div className="error-banner-actions">
          {onRetry ? (
            <button className="text-button" type="button" onClick={onRetry}>
              {retryLabel}
            </button>
          ) : null}
          {onDismiss ? (
            <button className="icon-button compact-button" type="button" aria-label="关闭提示" onClick={onDismiss}>
              <span aria-hidden="true">✕</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
