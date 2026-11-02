import { describeStep } from "../../lib/format";
import type { Evidence } from "../../lib/types";

interface Props {
  evidence: Evidence[];
  current: number;
  onSelect: (i: number) => void;
}

export function StepPlayer({ evidence, current, onSelect }: Props) {
  const last = evidence.length - 1;
  return (
    <div className="player">
      <div className="player-controls" role="group" aria-label="Step controls">
        <button type="button" className="button small" onClick={() => onSelect(0)} disabled={current === 0}>
          Restart
        </button>
        <button type="button" className="button small" onClick={() => onSelect(current - 1)} disabled={current === 0}>
          Previous
        </button>
        <button type="button" className="button small primary" onClick={() => onSelect(current + 1)} disabled={current === last}>
          Next step
        </button>
        <span className="player-pos" aria-live="polite">
          Step {current + 1} of {evidence.length}
        </span>
      </div>
      <ol className="step-list">
        {evidence.map((e, i) => (
          <li key={i}>
            <button
              type="button"
              className={`step${i === current ? " is-current" : ""}${e.verdict !== "MATCH" ? " is-diff" : ""}`}
              aria-current={i === current ? "step" : undefined}
              onClick={() => onSelect(i)}
            >
              <span className="step-n">{i + 1}</span>
              <code>{describeStep(e.step)}</code>
              <span className={`verdict ${e.verdict === "MATCH" ? "ok" : "bad"}`}>{e.verdict}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
