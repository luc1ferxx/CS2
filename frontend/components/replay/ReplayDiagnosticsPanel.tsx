"use client";

import type { ReplayDetailDiagnostics } from "@/lib/replay-diagnostics";

export function ReplayDiagnosticsPanel({ diagnostics }: { diagnostics: ReplayDetailDiagnostics }) {
  return (
    <section className="panel replay-diagnostics-panel" aria-label="Replay contract diagnostics">
      <div className="replay-diagnostics-header">
        <div>
          <h2>Replay Contract</h2>
          <p>
            {diagnostics.normalizedLegacy
              ? "Legacy or degraded contract normalized for review"
              : "Contract data loaded"}
          </p>
        </div>
        <span className={`mini-pill replay-contract-version ${diagnostics.normalizedLegacy ? "legacy" : "current"}`}>
          {diagnostics.contractVersion}
        </span>
      </div>
      <div className="replay-diagnostics-grid">
        <DiagnosticMetric label="Parser events" value={diagnostics.counts.parserEvents} />
        <DiagnosticMetric label="Coaching" value={diagnostics.counts.coachingEvents} />
        <DiagnosticMetric label="Rounds" value={diagnostics.counts.rounds} />
        <DiagnosticMetric label="Players" value={diagnostics.counts.players} />
        <DiagnosticMetric label="Frames" value={diagnostics.counts.frames} />
        <DiagnosticMetric label="Render" value={diagnostics.renderState.label} tone={diagnostics.renderState.tone} />
      </div>
      {diagnostics.warnings.length > 0 ? (
        <ul className="replay-diagnostics-warnings">
          {diagnostics.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function DiagnosticMetric({
  label,
  value,
  tone
}: {
  label: string;
  value: number | string;
  tone?: ReplayDetailDiagnostics["renderState"]["tone"];
}) {
  return (
    <div className={`diagnostic-metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
