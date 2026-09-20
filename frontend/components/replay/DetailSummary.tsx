"use client";

import type { DetailSummaryItem } from "@/lib/demo-library";

export function DetailSummary({ items }: { items: DetailSummaryItem[] }) {
  return (
    <section className="detail-summary-strip" aria-label="Demo status summary">
      {items.map((item) => (
        <div className={`detail-summary-item ${item.tone ?? "default"}`} key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.detail ? <small>{item.detail}</small> : null}
        </div>
      ))}
    </section>
  );
}
