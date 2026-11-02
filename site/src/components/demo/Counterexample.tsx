import { useState } from "react";
import { describeStep } from "../../lib/format";
import type { DemoData } from "../../lib/types";

export function Counterexample({ demo }: { demo: DemoData }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const o = demo.original_sequence;
  const m = demo.minimized_sequence;
  const s = demo.stats;
  return (
    <div className="counterexample">
      <dl className="facts">
        <div><dt>First failing sequence</dt><dd>{o.length} operations</dd></div>
        <div><dt>Minimized</dt><dd>{m.length} operations</dd></div>
        <div><dt>Verified replays</dt><dd>{demo.verification.reproduced}/{demo.verification.checks} from clean state</dd></div>
        <div><dt>Scenarios generated</dt><dd>{String(s.scenarios_generated)}</dd></div>
        <div><dt>Operations executed</dt><dd>{String(s.operations_executed)}</dd></div>
        <div><dt>Verified resets</dt><dd>{String(s.resets)}</dd></div>
      </dl>
      <p className="small">
        Method: {m.method}. {m.claim}.
      </p>
      <ol className="mini-seq">
        {m.steps.map((st, i) => (
          <li key={i}><code>{describeStep(st)}</code></li>
        ))}
      </ol>
      <button type="button" className="text-button" aria-expanded={showOriginal} aria-controls="original-seq" onClick={() => setShowOriginal(!showOriginal)}>
        {showOriginal ? "Hide" : "Show"} the original {o.length}-operation sequence
      </button>
      <ol id="original-seq" className="orig-seq" hidden={!showOriginal}>
        {o.steps.map((st, i) => (
          <li key={i}><code>{describeStep(st)}</code></li>
        ))}
      </ol>
      <p className="small">
        Logical id mapping in the final replay:{" "}
        {Object.keys(demo.id_mapping.reference).map((k) => (
          <code key={k}>
            {k} → original {JSON.stringify(demo.id_mapping.reference[k])}, rewrite {JSON.stringify(demo.id_mapping.candidate[k])}
          </code>
        ))}
      </p>
    </div>
  );
}
