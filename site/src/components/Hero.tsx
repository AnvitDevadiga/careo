import { REPO_URL } from "../lib/data";
import { Wordmark } from "./Wordmark";

export function Hero() {
  return (
    <section className="hero" id="top" aria-labelledby="hero-title">
      <div className="container">
        <h1 id="hero-title" className="hero-mark">
          <Wordmark height={72} title="careo." />
        </h1>
        <p className="hero-tagline">Catch what the AI rewrite broke.</p>
        <p className="hero-copy">
          Behavioral comparison for software rewrites. Careo runs the original and the rewritten
          implementation on identical generated scenarios from equivalent starting states, compares
          their responses and persistent state, and hands you a minimal, reproducible
          counterexample when they differ.
        </p>
        <div className="actions">
          <a className="button primary" href="#demo">
            Explore demo
          </a>
          <a className="button" href={REPO_URL}>
            View GitHub
          </a>
        </div>
      </div>
    </section>
  );
}
