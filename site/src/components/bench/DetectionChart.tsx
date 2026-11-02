import { percent, ratio } from "../../lib/format";
import type { BenchmarkData } from "../../lib/types";

/** Horizontal bars for seeded detection rate; the exact numbers are in the table above. */
export function DetectionChart({ data }: { data: BenchmarkData }) {
  const rowH = 34;
  const labelW = 190;
  const barW = 300;
  const h = data.methods.length * rowH + 8;
  const summary = data.methods.map((m) => `${m.label}: ${ratio(m.seeded_detection)}`).join("; ");
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${labelW + barW + 90} ${h}`} role="img" aria-label={`Seeded regression detection rate. ${summary}`}>
        {data.methods.map((m, i) => {
          const r = m.seeded_detection.denominator ? m.seeded_detection.numerator / m.seeded_detection.denominator : 0;
          const y = i * rowH + 6;
          return (
            <g key={m.id}>
              <text x={0} y={y + 16} className="chart-label">{m.label}</text>
              <rect x={labelW} y={y + 4} width={barW} height={16} className="chart-track" rx={2} />
              <rect x={labelW} y={y + 4} width={Math.max(r * barW, 1)} height={16} className="chart-bar" rx={2} />
              <text x={labelW + barW + 8} y={y + 16} className="chart-value">
                {percent(m.seeded_detection)} ({ratio(m.seeded_detection)})
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="small">Seeded regression detection rate per method (runs that found the seeded difference / runs).</figcaption>
    </figure>
  );
}
