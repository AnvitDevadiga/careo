import { TableScroll } from "../TableScroll";
import { num, percent, ratio, seconds } from "../../lib/format";
import type { BenchmarkData } from "../../lib/types";

export function MethodTable({ data }: { data: BenchmarkData }) {
  return (
    <TableScroll label="Results per method">
      <table className="data">
        <caption>Results per method ({data.meta.seeds} seeds per case; method A is deterministic and runs once per case)</caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col">Seeded regressions found</th>
            <th scope="col">Cases found</th>
            <th scope="col">False alarms</th>
            <th scope="col">Median ops to first difference</th>
            <th scope="col">Median time to first difference</th>
            <th scope="col">Median length before → after shrink</th>
            <th scope="col">Replay reliability</th>
            <th scope="col">Search ops</th>
            <th scope="col">Total ops</th>
          </tr>
        </thead>
        <tbody>
          {data.methods.map((m) => (
            <tr key={m.id}>
              <th scope="row">{m.label}</th>
              <td>{ratio(m.seeded_detection)} <span className="muted">({percent(m.seeded_detection)})</span></td>
              <td>{ratio(m.seeded_cases_detected_at_least_once)}</td>
              <td className={m.false_alarms_on_clean.numerator ? "cell-bad" : undefined}>{ratio(m.false_alarms_on_clean)}</td>
              <td>{num(m.median_operations_to_first_difference)}</td>
              <td>{seconds(m.median_time_to_first_difference_seconds)}</td>
              <td>
                {num(m.median_sequence_length_before_shrink)} → {num(m.median_sequence_length_after_shrink)}
              </td>
              <td>{m.replay_success.denominator ? ratio(m.replay_success) : "n/a"}</td>
              <td>{m.total_search_operations.toLocaleString("en-US")}</td>
              <td>{m.total_operations_executed.toLocaleString("en-US")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
