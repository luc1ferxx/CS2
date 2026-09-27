"use client";

import Link from "next/link";
import { Check } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import {
  processingElapsedLabel,
  processingHeadline,
  processingSteps,
  type DetailFailureState,
  type DetailLoadState
} from "@/lib/demo-library";

type CardState = Exclude<DetailLoadState, { kind: "connecting" } | { kind: "loading_replay" }>;

interface DemoStateCardProps {
  state: CardState;
  parseRetrying: boolean;
  replayReloading: boolean;
  onRetryParse: () => void;
  onRetryStatus: () => void;
  onReloadReplay: () => void;
  // Dev-only diagnostics, shown inside the collapsed technical details.
  technicalDetails?: ReactNode;
}

const STEP_STATE_TEXT = { done: "已完成", current: "进行中", pending: "未开始" } as const;

// The review layout at its final size, so the workspace fills in instead of jumping in.
// Static placeholders: nothing moves while the page loads.
export function ReviewSkeleton({ label }: { label: string }) {
  return (
    <div className="review-skeleton" aria-busy="true">
      <div className="panel review-skeleton-strip" aria-hidden="true">
        <div className="panel-bar" />
        <div className="review-skeleton-cells">
          {Array.from({ length: 18 }, (_, index) => <span key={index} />)}
        </div>
      </div>
      <div className="review-layout">
        <div className="review-main-column">
          <div className="panel review-stage">
            <div className="panel-bar" aria-hidden="true" />
            <div className="review-main-canvas showing-map review-skeleton-canvas">
              <p role="status">{label}</p>
            </div>
            <div className="review-skeleton-transport" aria-hidden="true">
              <span className="skeleton-bar wide" />
              <span className="skeleton-bar" />
            </div>
          </div>
        </div>
        <div className="panel coaching-panel review-skeleton-coaching" aria-hidden="true">
          <div className="panel-bar" />
          {[0, 1, 2].map((index) => (
            <div className="review-skeleton-card" key={index}>
              <span className="skeleton-bar wide" />
              <span className="skeleton-bar" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function DemoStateCard(props: DemoStateCardProps) {
  const { state } = props;
  if (state.kind === "processing") {
    return <ProcessingCard step={state.step} stale={state.stale} startedAt={state.startedAt} technicalDetails={props.technicalDetails} />;
  }
  if (state.kind === "failed" || state.kind === "replay_unavailable") {
    return <FailureCard {...props} state={state} />;
  }
  // No match to head these two: the page renders no match header, so the card carries the page title.
  if (state.kind === "not_found") {
    return (
      <section className="panel demo-state-card" aria-labelledby="demo-state-title">
        <div className="panel-bar"><h1 className="panel-bar-title" id="demo-state-title">找不到这场比赛</h1></div>
        <div className="demo-state-heading"><p>这场比赛可能已被删除，或属于其他账号。</p></div>
        <div className="demo-state-actions">
          <Link href="/dashboard">返回我的比赛</Link>
        </div>
      </section>
    );
  }
  if (state.kind === "unreachable") {
    return (
      <section className="panel demo-state-card" aria-labelledby="demo-state-title">
        <div className="panel-bar"><h1 className="panel-bar-title" id="demo-state-title">暂时无法打开比赛</h1></div>
        <div className="demo-state-heading">
          <p role="status">网络连接中断，正在自动重试…</p>
        </div>
        <div className="demo-state-actions">
          <button className="secondary-button compact-button" type="button" onClick={props.onRetryStatus}>立即重试</button>
          <Link href="/dashboard">返回我的比赛</Link>
        </div>
      </section>
    );
  }
  return (
    <section className="panel demo-state-card failed" aria-labelledby="demo-state-title">
      <div className="panel-bar"><h2 className="panel-bar-title" id="demo-state-title">回放暂时无法打开</h2></div>
      <div className="demo-state-heading"><p role="alert">载入回放数据时出错，可以重新载入。</p></div>
      <div className="demo-state-actions">
        <button className="primary-button compact-button" type="button" onClick={props.onReloadReplay}
          disabled={props.replayReloading}>
          {props.replayReloading ? "正在重新载入…" : "重新载入"}
        </button>
        {state.retryable ? (
          <button className="secondary-button compact-button" type="button" onClick={props.onRetryParse}
            disabled={props.parseRetrying}>
            {props.parseRetrying ? "正在重新处理…" : "重新处理"}
          </button>
        ) : null}
      </div>
      {props.technicalDetails ? <TechnicalDetails>{props.technicalDetails}</TechnicalDetails> : null}
    </section>
  );
}

function ProcessingCard({ step, stale, startedAt, technicalDetails }: {
  step: Extract<CardState, { kind: "processing" }>["step"];
  stale: boolean;
  startedAt: string | null;
  technicalDetails?: ReactNode;
}) {
  const now = useNow(1000);
  const elapsed = processingElapsedLabel(startedAt, now);
  return (
    <section className="panel demo-state-card" aria-labelledby="demo-state-title">
      <div className="panel-bar"><h2 className="panel-bar-title" id="demo-state-title">正在处理这场比赛</h2></div>
      <div className="demo-state-heading"><p>{processingHeadline(step)}</p></div>
      <ol className="demo-progress-steps" aria-label="处理进度">
        {processingSteps(step).map((item, index) => (
          <li key={item.key} data-state={item.state} aria-current={item.state === "current" ? "step" : undefined}>
            <span className="demo-progress-marker" aria-hidden="true">
              {item.state === "done" ? <Check size={12} /> : index + 1}
            </span>
            <span>{item.label}</span>
            <span className="visually-hidden">（{STEP_STATE_TEXT[item.state]}）</span>
          </li>
        ))}
      </ol>
      <p className="demo-state-meta">
        {elapsed ?? (step === "uploaded" ? "排队中" : null)}
        {elapsed || step === "uploaded" ? "。" : null}
        完成后会自动打开复盘，无需刷新页面。
      </p>
      {stale ? (
        <p className="demo-state-warning">处理时间比平时长。如果超时，会自动标记为失败，届时可以重新处理。</p>
      ) : null}
      <div className="demo-state-actions">
        <Link href="/dashboard">返回我的比赛</Link>
      </div>
      {technicalDetails ? <TechnicalDetails>{technicalDetails}</TechnicalDetails> : null}
    </section>
  );
}

function FailureCard({ state, parseRetrying, onRetryParse, technicalDetails }: DemoStateCardProps & { state: DetailFailureState }) {
  const retryFirst = state.next.action === "retry";
  const retryButton = state.retryable ? (
    <button className={`${retryFirst ? "primary-button" : "secondary-button"} compact-button`} type="button"
      onClick={onRetryParse} disabled={parseRetrying}>
      {parseRetrying ? "正在重新处理…" : "重新处理"}
    </button>
  ) : null;
  return (
    <section className="panel demo-state-card failed" aria-labelledby="demo-state-title">
      <div className="panel-bar"><h2 className="panel-bar-title" id="demo-state-title">{state.kind === "failed" ? "这场比赛处理失败" : "回放暂时无法打开"}</h2></div>
      <div className="demo-state-heading"><p>{state.failure.message}</p></div>
      <p className="demo-state-hint">{state.next.hint}</p>
      <div className="demo-state-actions">
        {retryFirst ? retryButton : (
          <Link className="primary-button compact-button" href="/dashboard">去「我的比赛」重新上传</Link>
        )}
        {retryFirst ? <Link href="/dashboard">返回我的比赛</Link> : retryButton}
      </div>
      <TechnicalDetails>
        <dl className="demo-state-facts">
          <div><dt>错误代码</dt><dd>{state.errorCode ?? "未提供"}</dd></div>
          {state.attemptCount > 0 ? <div><dt>处理次数</dt><dd>{state.attemptCount}</dd></div> : null}
        </dl>
        {technicalDetails}
      </TechnicalDetails>
    </section>
  );
}

function TechnicalDetails({ children }: { children: ReactNode }) {
  return (
    <details className="demo-state-technical">
      <summary>技术信息</summary>
      {children}
    </details>
  );
}

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(intervalId);
  }, [intervalMs]);
  return now;
}
