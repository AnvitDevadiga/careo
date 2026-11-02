import { TableScroll } from "../TableScroll";
import { json } from "../../lib/format";
import type { Evidence, Observation } from "../../lib/types";

function Response({ title, obs, highlight }: { title: string; obs: Observation; highlight: boolean }) {
  const shown = obs.body !== undefined ? obs.body : (obs.body_text ?? obs.error);
  return (
    <div className={`response${highlight ? " is-diff" : ""}`}>
      <h4>{title}</h4>
      <p className="status-line">
        {obs.status === "ok" ? <>HTTP <strong>{obs.code}</strong></> : <strong>{obs.status}</strong>}
      </p>
      <pre tabIndex={0} aria-label={`${title} response body`}>
        <code>{JSON.stringify(shown, null, 2)}</code>
      </pre>
    </div>
  );
}

export function StepDetail({ ev }: { ev: Evidence }) {
  const diff = ev.verdict !== "MATCH";
  return (
    <div className="step-detail">
      <div className="responses">
        <Response title="Original" obs={ev.reference} highlight={diff} />
        <Response title="Rewrite" obs={ev.candidate} highlight={diff} />
      </div>
      {diff ? (
        <TableScroll label="Differences at this step">
          <table className="data">
            <caption>Differences reported at this step ({ev.differences.length})</caption>
            <thead>
              <tr>
                <th scope="col">Path</th>
                <th scope="col">Kind</th>
                <th scope="col">Original</th>
                <th scope="col">Rewrite</th>
              </tr>
            </thead>
            <tbody>
              {ev.differences.map((d, i) => (
                <tr key={i}>
                  <td><code>{d.path}</code></td>
                  <td>{d.kind}</td>
                  <td><code>{json(d.reference)}</code></td>
                  <td><code>{json(d.candidate)}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      ) : (
        <p className="match-note">
          Responses and database state match after logical-id translation
          {ev.produced ? <> (this step created <code>{ev.produced}</code>)</> : null}.
        </p>
      )}
    </div>
  );
}
