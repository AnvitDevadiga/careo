import { TableScroll } from "../TableScroll";
import { ratio } from "../../lib/format";
import type { BenchmarkData } from "../../lib/types";

export function CaseMatrix({ data }: { data: BenchmarkData }) {
  return (
    <TableScroll label="Detection by case">
      <table className="data matrix">
        <caption>Detected runs / runs, per case. Clean cases should stay at zero.</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            {data.methods.map((m) => (
              <th scope="col" key={m.id}>{m.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.cases.map((c) => (
            <tr key={c.name}>
              <th scope="row">
                <code>{c.name}</code>
                <span className="case-kind">{c.seeded ? "seeded regression" : "clean, no known difference"}</span>
                <span className="case-desc">{c.description}</span>
              </th>
              {data.methods.map((m) => {
                const r = m.per_case_detection[c.name];
                const alarm = !c.seeded && r.numerator > 0;
                return (
                  <td key={m.id} className={alarm ? "cell-bad" : r.numerator ? "cell-hit" : undefined}>
                    {ratio(r)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
