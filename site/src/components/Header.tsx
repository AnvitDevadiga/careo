import { REPO_URL } from "../lib/data";
import { ThemeToggle } from "./ThemeToggle";
import { Wordmark } from "./Wordmark";

const NAV = [
  ["how", "How it works"],
  ["demo", "Demo"],
  ["benchmarks", "Benchmarks"],
  ["quick-start", "Quick start"],
  ["architecture", "Architecture"],
  ["limitations", "Limitations"],
] as const;

export function Header() {
  return (
    <header className="site-header">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="container header-row">
        <a href="#top" className="brand" aria-label="careo. home">
          <Wordmark height={22} title="careo." />
        </a>
        <nav aria-label="Primary">
          <ul className="nav-list">
            {NAV.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`}>{label}</a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="header-actions">
          <a className="text-link" href={REPO_URL}>
            GitHub
          </a>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
