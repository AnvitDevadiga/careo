import { REPO_URL, repoFile } from "../lib/data";
import { Wordmark } from "./Wordmark";

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-row">
        <Wordmark height={18} title="Careo" />
        <p>
          Built by Anvit Devadiga. <a href={repoFile("LICENSE")}>MIT License</a>. <a href={REPO_URL}>Source on GitHub</a>.
        </p>
      </div>
    </footer>
  );
}
