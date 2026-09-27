import type { UtilityCounts } from "@/lib/match-stats";

interface UtilitySummaryProps {
  counts: UtilityCounts;
  // Rounds the player was in; 0 leaves the per-round column empty.
  roundsPlayed: number;
}

const UTILITY_ROWS: { key: keyof UtilityCounts; label: string }[] = [
  { key: "smoke", label: "烟雾弹" },
  { key: "flash", label: "闪光弹" },
  { key: "molotov", label: "燃烧弹" },
  { key: "he", label: "手雷" }
];

export function UtilitySummary({ counts, roundsPlayed }: UtilitySummaryProps) {
  const total = UTILITY_ROWS.reduce((sum, row) => sum + counts[row.key], 0);
  const max = Math.max(1, ...UTILITY_ROWS.map((row) => counts[row.key]));

  return (
    <section className="match-analysis-cell utility-summary" aria-labelledby="utility-summary-title">
      <div className="match-analysis-cell-bar">
        <h3 id="utility-summary-title">道具</h3>
        <span className="match-analysis-cell-meta">{roundsPlayed > 0 ? `${roundsPlayed} 个回合` : null}</span>
      </div>
      <table className="data-table utility-table">
        <thead>
          <tr>
            <th scope="col">道具</th>
            <th scope="col" className="num">投掷</th>
            <th scope="col" className="num">每回合</th>
            <th scope="col"><span className="visually-hidden">占比</span></th>
          </tr>
        </thead>
        <tbody>
          {UTILITY_ROWS.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              <td className="num">{counts[row.key]}</td>
              <td className="num">{perRound(counts[row.key], roundsPlayed)}</td>
              <td className="utility-bar-cell" aria-hidden="true">
                <span className="utility-bar" style={{ width: `${(counts[row.key] / max) * 100}%` }} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">合计</th>
            <td className="num">{total}</td>
            <td className="num">{perRound(total, roundsPlayed)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </section>
  );
}

function perRound(count: number, rounds: number): string {
  return rounds > 0 ? (count / rounds).toFixed(2) : "—";
}
