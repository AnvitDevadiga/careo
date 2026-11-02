import { Section } from "./Section";

const ITEMS: [string, string][] = [
  ["Finite coverage", "Careo tests the operations, values and sequence lengths it generates within a budget. Defects outside them stay invisible. Small budgets missed a seeded defect in our trial runs."],
  ["No proof of equivalence", "A clean report means no difference was observed in the executed scenarios. It is evidence, not a proof that two programs are equivalent."],
  ["The reference can be wrong", "Careo treats the original as the oracle. If the original has a bug that the rewrite fixed, Careo reports the fix as a difference. You decide which side is right."],
  ["Nondeterminism", "Time, randomness and ordering need explicit normalization rules or id mappings. Differences that do not reproduce on every replay are labelled flaky, not confirmed. In-memory state survives a database-only reset unless services are restarted."],
  ["Authorized, trusted programs only", "Careo runs the commands you configure as your user, with your files and network. Subprocesses are not a sandbox. Only compare software you own or are authorized to test, and never point it at production systems."],
  ["Platform and scope", "Linux and macOS. HTTP/JSON services with SQLite state, and CLI programs with a JSON contract. Only a documented subset of OpenAPI is supported, and PostgreSQL is not supported."],
];

export function Limitations() {
  return (
    <Section id="limitations" title="Limitations" lead="What Careo does not do, stated plainly.">
      <dl className="limits">
        {ITEMS.map(([t, d]) => (
          <div key={t}>
            <dt>{t}</dt>
            <dd>{d}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}
