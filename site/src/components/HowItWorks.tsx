import { Section } from "./Section";

const STAGES = [
  ["Identical scenarios", "Hypothesis generates operation sequences from a spec or an OpenAPI subset. Both sides receive the same steps."],
  ["Isolated execution", "Before each scenario the database is reset and independently verified against the fixture. A failed reset voids the run."],
  ["Behavioral comparison", "Status codes, bodies, selected headers and database rows are compared under explicit, visible rules. Generated ids are mapped, not ignored."],
  ["Minimal counterexample", "The failing sequence is shrunk, replayed from clean state until it reproduces every time, and exported as a regression test."],
] as const;

export function HowItWorks() {
  return (
    <Section id="how" title="How it works" lead="One flow, two implementations, no hidden tolerance.">
      <div className="flow" role="group" aria-label="Careo pipeline">
        <div className="flow-inputs">
          <div className="flow-node">Original implementation</div>
          <span className="flow-plus" aria-hidden="true">+</span>
          <div className="flow-node">Rewritten implementation</div>
        </div>
        <ol className="flow-stages">
          {STAGES.map(([title, text]) => (
            <li key={title} className="flow-stage">
              <strong>{title}</strong>
              <span>{text}</span>
            </li>
          ))}
        </ol>
      </div>
    </Section>
  );
}
