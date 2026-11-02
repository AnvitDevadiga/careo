import { REPO_URL, useStaticJson } from "../../lib/data";
import type { BenchmarkData } from "../../lib/types";
import { CodeBlock } from "../CodeBlock";
import { Section } from "../Section";
import { CaseMatrix } from "./CaseMatrix";
import { DetectionChart } from "./DetectionChart";
import { MethodTable } from "./MethodTable";
import { RunExplorer } from "./RunExplorer";

export function BenchmarksView({ data }: { data: BenchmarkData }) {
  const m = data.meta;
  return (
    <>
      <p className="notice" role="note">
        <strong>Seeded fixtures, not production discoveries.</strong> Six regressions were deliberately planted in an
        independent rewrite of the demo booking API; two clean candidates measure false alarms. These results describe
        Careo on these fixtures only and do not predict how it performs on real migrations.
      </p>
      <MethodTable data={data} />
      <ul className="caveats">
        <li>Methods B, C and D share an equal budget of {m.budget_operations} search operations per run. Shrinking and verification replays are extra work, counted in total ops.</li>
        <li>Method A, the example project&rsquo;s own happy-path tests, is a fixed suite and is not budget-equal.</li>
        <li>D runs the same seeded search as C, so it finds exactly what C finds by construction. D adds shorter, replay-verified counterexamples, at the cost of more total operations.</li>
        <li>In smaller trial runs (150 operations), C and D missed <code>missing_error</code> because Hypothesis front-loads simple values. Budget matters.</li>
      </ul>
      <DetectionChart data={data} />
      <h3>Detection by case</h3>
      <CaseMatrix data={data} />
      <h3>Every run</h3>
      <RunExplorer data={data} />
      <h3>Provenance</h3>
      <dl className="facts">
        <div><dt>Source commit</dt><dd><a href={`${REPO_URL}/commit/${m.source_commit}`}><code>{m.source_commit.slice(0, 12)}</code></a></dd></div>
        <div><dt>Careo</dt><dd>{m.careo_version}</dd></div>
        <div><dt>Recorded</dt><dd>{m.finished_at.slice(0, 10)}</dd></div>
        <div><dt>Environment</dt><dd>Python {m.python}, {m.platform}</dd></div>
        <div><dt>Raw records</dt><dd><code>{data.generated_from.raw_records}</code></dd></div>
        <div><dt>SHA-256</dt><dd><code className="wrap">{data.generated_from.raw_records_sha256.slice(7, 23)}…</code></dd></div>
      </dl>
      <p className="small">
        Recorded with Careo {m.careo_version} at commit <code>{m.source_commit.slice(0, 7)}</code>. Changes to the engine since then
        only add per-step state to scenario evidence and change version and report formatting; generation, comparison and
        classification are unchanged.
      </p>
      <CodeBlock code={data.reproduce} label="Reproduce" language="sh" />
    </>
  );
}

export function Benchmarks() {
  const state = useStaticJson<BenchmarkData>("benchmark.json");
  return (
    <Section id="benchmarks" title="Benchmark explorer" lead="Four testing methods, the same seeded regressions, every run published.">
      {state.status === "loading" && <p aria-busy="true">Loading benchmark records…</p>}
      {state.status === "error" && <p role="alert">Could not load benchmark data: {state.message}</p>}
      {state.status === "ready" && <BenchmarksView data={state.data} />}
    </Section>
  );
}
