"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode
} from "react";
import { createPortal } from "react-dom";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  // What goes, and that it cannot be undone.
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  busyLabel?: string;
  error?: string | null;
  // Type-to-confirm: the confirm button stays off until the input equals this exactly.
  requireText?: string;
  onConfirm: () => void;
  // Esc and 取消; focus goes back to whatever opened the dialog.
  onCancel: () => void;
}

const FOCUSABLE = "button:not(:disabled), input:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])";

function focusableIn(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
}

// The typed confirmation when there is one, otherwise the harmless choice.
function firstControl(panel: HTMLElement): HTMLElement | null {
  return panel.querySelector<HTMLElement>("input:not(:disabled)") ?? panel.querySelector<HTMLElement>("[data-dialog-cancel]:not(:disabled)");
}

// A modal panel over a dim page for irreversible actions: the header bar carries
// the question, the body what will be lost, then a red confirm and a plain cancel.
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busy = false,
  busyLabel = "正在删除…",
  error = null,
  requireText,
  onConfirm,
  onCancel
}: ConfirmDialogProps) {
  const titleId = useId();
  const inputId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setTyped("");
    const panel = panelRef.current;
    if (panel) firstControl(panel)?.focus();
  }, [open]);

  // Both buttons go disabled while the request runs; keep focus (and Esc/Tab) inside.
  // When it fails and the dialog stays open, hand focus back to a real control.
  const wasBusyRef = useRef(busy);
  useEffect(() => {
    const panel = panelRef.current;
    const active = document.activeElement;
    const wasBusy = wasBusyRef.current;
    wasBusyRef.current = busy;
    if (!open || !panel) return;
    if (busy) {
      if (!panel.contains(active) || active?.matches(":disabled")) panel.focus();
    } else if (wasBusy && (active === panel || !panel.contains(active))) {
      firstControl(panel)?.focus();
    }
  }, [busy, open]);

  // Backstop: if focus ever lands outside the panel while it is open, the next
  // key brings it back (and Esc still closes) instead of reaching the page.
  const latestRef = useRef({ busy, onCancel });
  useEffect(() => {
    latestRef.current = { busy, onCancel };
  });
  useEffect(() => {
    if (!open) return;
    function onDocumentKeyDown(event: KeyboardEvent) {
      const panel = panelRef.current;
      if (!panel || panel.contains(document.activeElement)) return;
      event.stopPropagation();
      if (event.key === "Escape") {
        event.preventDefault();
        if (latestRef.current.busy) return;
        const opener = openerRef.current;
        if (opener?.isConnected) opener.focus();
        latestRef.current.onCancel();
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        const focusable = focusableIn(panel);
        (event.shiftKey ? focusable[focusable.length - 1] : focusable[0])?.focus();
      }
    }
    document.addEventListener("keydown", onDocumentKeyDown, true);
    return () => document.removeEventListener("keydown", onDocumentKeyDown, true);
  }, [open]);

  if (!open || typeof document === "undefined") return null;

  const confirmBlocked = requireText !== undefined && typed !== requireText;

  function cancel() {
    if (busy) return;
    const opener = openerRef.current;
    if (opener?.isConnected) opener.focus();
    onCancel();
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    // Page shortcuts (the replay's keys) stay off while the dialog is up.
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
      return;
    }
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    const focusable = panel ? focusableIn(panel) : [];
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    // The panel itself (focused while busy, or after a failure) counts as
    // outside the tab ring, so the browser never gets to move past it.
    const outside = active === panel || !panel?.contains(active);
    if (event.shiftKey && (active === first || outside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || outside)) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div className="dialog-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) event.preventDefault();
    }}>
      <div
        ref={panelRef}
        className="panel confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={busy || undefined}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className="panel-bar">
          <h2 className="panel-bar-title" id={titleId}>{title}</h2>
        </div>
        <form
          className="confirm-dialog-body"
          onSubmit={(event) => {
            event.preventDefault();
            if (!busy && !confirmBlocked) onConfirm();
          }}
        >
          {children}
          {requireText !== undefined ? (
            <label className="confirm-dialog-type" htmlFor={inputId}>
              <span>请输入「{requireText}」确认</span>
              <input
                id={inputId}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={busy}
              />
            </label>
          ) : null}
          {error ? <p className="confirm-dialog-error" role="alert">{error}</p> : null}
          <div className="confirm-dialog-actions">
            <button className="danger-button" type="submit" disabled={busy || confirmBlocked}>
              {busy ? busyLabel : confirmLabel}
            </button>
            <button className="secondary-button" type="button" data-dialog-cancel onClick={cancel} disabled={busy}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
