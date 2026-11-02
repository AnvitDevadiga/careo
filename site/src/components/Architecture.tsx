import { repoFile } from "../lib/data";
import { Section } from "./Section";

const PARTS = [
  {
    title: "Adapters",
    text: "A process adapter runs any executable with JSON on stdin and stdout. An HTTP adapter talks to services on loopback addresses only, unless you explicitly allow a remote host. It never follows redirects and reports partial or oversized responses as failures. A SQLite state adapter owns the reset.",
  },
  {
    title: "State isolation",
    text: "Before every scenario Careo deletes and reloads every table in one transaction, then verifies the result independently: schema, every row and autoincrement counters must equal the fixture loaded into a private in-memory database. A mismatch is RESET_FAILED, never a verdict. Services can also be restarted per scenario to clear in-memory state.",
  },
  {
    title: "Comparison rules",
    text: "Strict by default: 1 vs 1.0, null vs absent, array order and types all count. Normalization is explicit, scoped by JSON Pointer and reported. Generated ids map to logical entities (booking#1 → 2 / 8000) with their JSON type kept, so a wrong or retyped id is still a difference.",
  },
  {
    title: "Shrinking and replay",
    text: "Hypothesis shrinks the failing sequence; a bounded delta-debugging pass removes more steps, keeping a reduction only if it reproduces. The result is replayed from clean state and confirmed only when every replay fails the same way. Results are smaller, not proven minimal.",
  },
];

export function Architecture() {
  return (
    <Section id="architecture" title="Architecture" lead="Small parts with explicit contracts.">
      <div className="grid-2">
        {PARTS.map((p) => (
          <article key={p.title} className="card">
            <h3>{p.title}</h3>
            <p>{p.text}</p>
          </article>
        ))}
      </div>
      <p className="small">
        Details: <a href={repoFile("docs/stateful.md")}>stateful testing</a> · <a href={repoFile("docs/design.md")}>execution model</a> ·{" "}
        <a href={repoFile("docs/report-schema.md")}>report schemas</a>
      </p>
    </Section>
  );
}
