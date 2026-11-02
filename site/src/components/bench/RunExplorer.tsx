import { TableScroll } from "../TableScroll";
import { useId, useMemo, useState } from "react";
import { download } from "../../lib/data";
import { num, seconds } from "../../lib/format";
import type { BenchmarkData } from "../../lib/types";

export function RunExplorer({ data }: { data: BenchmarkData }) {
  const [method, setMethod] = useState("all");
  const [kase, setKase] = useState("all");
  const id = useId();
  const rows = useMemo(
    () => data.records.filter((r) => (method === "all" || r.method === method) && (kase === "all" || r.case === kase)),
    [data.records, method, kase],
  );
  const label = (m: string) => data.methods.find((x) => x.id === m)?.label ?? m;
  return (
    <div className="explorer">
      <div className="filters">
        <label htmlFor={`${id}-m`}>Method</label>
        <select id={`${id}-m`} value={method} onChange={(e) => setMethod(e.target.value)}>
          <option value="all">All methods</option>
          {data.methods.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
        <label htmlFor={`${id}-c`}>Case</label>
        <select id={`${id}-c`} value={kase} onChange={(e) => setKase(e.target.value)}>
          <option value="all">All cases</option>
          {data.cases.map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </select>
        <span className="muted" aria-live="polite">
          Showing {rows.length} of {data.records.length} runs
        </span>
      </div>
      <TableScroll label="Raw benchmark runs" tall>
        <table className="data">
          <caption>Raw benchmark runs (from raw-records.jsonl)</caption>
          <thead>
            <tr>
              <th scope="col">Case</th>
              <th scope="col">Method</th>
              <th scope="col">Seed</th>
              <th scope="col">Outcome</th>
              <th scope="col">Ops to first</th>
              <th scope="col">Time to first</th>
              <th scope="col">Length before → after</th>
              <th scope="col">Replay</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><code>{r.case}</code></td>
                <td>{label(r.method)}</td>
                <td>{r.seed ?? "n/a"}</td>
                <td className={!r.seeded && r.outcome !== "NO_DIFFERENCES_OBSERVED" ? "cell-bad" : undefined}>{r.outcome}</td>
                <td>{num(r.operations_to_first_difference)}</td>
                <td>{seconds(r.time_to_first_difference_seconds)}</td>
                <td>
                  {num(r.sequence_length_before_shrink)} → {num(r.sequence_length_after_shrink)}
                </td>
                <td>{r.verification_checks ? `${r.verification_reproduced}/${r.verification_checks}` : "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="small">
        <a href={download("careo-benchmark-raw-records.jsonl")} download>Download raw records (JSONL)</a>
        {" · "}
        <a href={download("careo-benchmark-summary.json")} download>Download summary.json</a>
      </p>
    </div>
  );
}
