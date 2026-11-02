import { CodeBlock } from "./CodeBlock";
import { Section } from "./Section";

export const INSTALL = `python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+
pip install git+https://github.com/AnvitDevadiga/careo
careo --version`;

export const RUN = `git clone https://github.com/AnvitDevadiga/careo && cd careo

# One-shot comparison of two CLI programs (JSON in, JSON out)
careo compare --config examples/bulk-pricing/careo.yaml

# Stateful comparison of two local HTTP services
careo scenario run --spec examples/booking-api/careo-scenarios.yaml --output-dir out
careo scenario replay --scenario out/regression.json`;

export function QuickStart() {
  return (
    <Section id="quick-start" title="Quick start" lead="Linux or macOS, Python 3.11 or newer. These commands were run from a clean environment.">
      <CodeBlock code={INSTALL} label="Install" language="sh" />
      <CodeBlock code={RUN} label="Run the examples" language="sh" />
      <p className="small">
        Both example runs exit with status 1 because they find their seeded regressions. <code>replay</code> exits 0 when the recorded
        result reproduces. Exit codes: 0 no difference, 1 difference or failure, 2 invalid configuration, 3 execution or reset error.
      </p>
    </Section>
  );
}
