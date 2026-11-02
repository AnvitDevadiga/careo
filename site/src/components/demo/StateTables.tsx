import { TableScroll } from "../TableScroll";
import { alignRows, tablesOf } from "../../lib/diff";
import type { Evidence } from "../../lib/types";

function Row({ row }: { row?: Record<string, unknown> }) {
  if (!row) return <span className="muted">(no row)</span>;
  return (
    <code>
      {Object.entries(row)
        .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
        .join(", ")}
    </code>
  );
}

export function StateTables({ ev }: { ev: Evidence }) {
  if (!ev.state) return null;
  const state = ev.state;
  return (
    <div className="state">
      {tablesOf(state).map((table) => (
        <TableScroll key={table} label={`Table ${table} after this step`}>
          <table className="data">
            <caption>
              Table <code>{table}</code> after step {ev.index + 1} (ids shown as logical ids)
            </caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Original database</th>
                <th scope="col">Rewrite database</th>
              </tr>
            </thead>
            <tbody>
              {alignRows(state, table).map((p) => (
                <tr key={p.index} className={p.differs ? "row-diff" : undefined}>
                  <td>{p.index}{p.differs && <span className="visually-hidden"> (differs)</span>}</td>
                  <td><Row row={p.reference} /></td>
                  <td><Row row={p.candidate} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      ))}
    </div>
  );
}
