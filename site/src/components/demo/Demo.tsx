import { useState } from "react";
import { useStaticJson } from "../../lib/data";
import type { DemoData } from "../../lib/types";
import { CodeBlock } from "../CodeBlock";
import { Section } from "../Section";
import { Counterexample } from "./Counterexample";
import { Downloads } from "./Downloads";
import { SourceCompare } from "./SourceCompare";
import { StateTables } from "./StateTables";
import { StepDetail } from "./StepDetail";
import { StepPlayer } from "./StepPlayer";
import { Tabs } from "./Tabs";

export function DemoView({ demo }: { demo: DemoData }) {
  const [current, setCurrent] = useState(0);
  const ev = demo.evidence[current];
  return (
    <>
      <p className="notice" role="note">
        <strong>Recorded run, replayed in your browser.</strong> {demo.generated_from.note} The bug is{" "}
        {demo.seeded_bug.charAt(0).toLowerCase() + demo.seeded_bug.slice(1)}
      </p>

      <h3>1. Two implementations of the same booking API</h3>
      <p>
        Both serve the same contract on a SQLite database. Their ordinary happy-path tests pass. The rewrite
        changes how cancellation behaves and numbers bookings differently (8000, 8003, … instead of 2, 3, …).
      </p>
      <SourceCompare demo={demo} />

      <h3>2. Step through the counterexample</h3>
      <StepPlayer evidence={demo.evidence} current={current} onSelect={(i) => setCurrent(Math.max(0, Math.min(i, demo.evidence.length - 1)))} />
      <StepDetail ev={ev} />
      <h4>Persistent state after this step</h4>
      <StateTables ev={ev} />

      <h3>3. From the first failure to a minimal case</h3>
      <Counterexample demo={demo} />

      <h3>4. Generated regression test</h3>
      <p>
        It fails while the rewrite differs and passes once it behaves like the original. It replays the minimized
        scenario from a clean, verified state.
      </p>
      <Tabs
        label="Exported regression artifacts"
        tabs={[
          { id: "test", label: "test_careo_regression.py", content: <CodeBlock code={demo.regression_test} label="test_careo_regression.py" language="python" /> },
          { id: "json", label: "regression.json", content: <CodeBlock code={JSON.stringify(demo.regression_json, null, 2)} label="regression.json" language="json" /> },
          { id: "spec", label: "careo-scenarios.yaml", content: <CodeBlock code={demo.spec} label="examples/booking-api/careo-scenarios.yaml" language="yaml" /> },
        ]}
      />
      <h3>5. Evidence</h3>
      <Downloads />
    </>
  );
}

export function Demo() {
  const state = useStaticJson<DemoData>("demo.json");
  return (
    <Section
      id="demo"
      title="Interactive demo"
      lead="A booking API and an AI-style rewrite that passes its happy-path tests. Careo finds where they diverge."
    >
      {state.status === "loading" && <p aria-busy="true">Loading recorded run…</p>}
      {state.status === "error" && <p role="alert">Could not load demo data: {state.message}</p>}
      {state.status === "ready" && <DemoView demo={state.data} />}
    </Section>
  );
}
